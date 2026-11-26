"""Database connections end to end, against the real test databases (compose profile "testdbs")
with a scripted fake chat model (no GPU needed).

  docker compose --profile testdbs up -d testdb-postgres testdb-mysql

Covers: questions answered from query results, self-correction after an SQL error, writes refused
for plain questions, the three edit permissions (Never edit / Ask / Automatic), approval,
rejection, expiry and tampering, hidden tables, and the host guard.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from openai import AsyncOpenAI
from sqlalchemy import select, update

from app.api import datasources as api
from app.config import Settings, get_settings
from app.datasources.connectors import DbError, connect
from app.datasources.hosts import HostNotAllowed, resolve_db_host
from app.datasources.schema import refresh_schema
from app.db.models import DataSource, DsPendingChange, DsQueryLog, DsSchemaItem, User
from app.llm.chat import ChatTarget
from app.orchestrator.answer import answer_stream
from app.security.crypto import encrypt
from tests.integration.test_crawl_and_answer import sessions  # noqa: F401 - fixture

DBS = {
    "postgres": dict(kind="postgres", host="testdb-postgres", port=5432, ssl_mode="prefer"),
    "mysql": dict(kind="mysql", host="testdb-mysql", port=3306, ssl_mode="disable"),
}


def settings() -> Settings:
    return get_settings().model_copy(update={"allow_private_db_hosts": True})


def scripted_chat(route: dict, actions: list[dict], answer: str, sent: list[dict]) -> ChatTarget:
    """Router call -> `route`; agent calls -> next of `actions`; streamed call -> `answer`."""
    queue = list(actions)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        system = payload["messages"][0]["content"]
        if payload.get("stream"):
            body = "data: " + json.dumps({
                "id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": "stop"}],
            }) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        content = json.dumps(route if system.startswith("You route") else
                             (queue.pop(0) if queue else {"action": "answer"}))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}]})

    return ChatTarget(AsyncOpenAI(base_url="http://gpu/v1", api_key="k", max_retries=0,
                                  http_client=httpx.AsyncClient(
                                      transport=httpx.MockTransport(handler))), "nullish-chat")


@pytest_asyncio.fixture(params=["postgres", "mysql"])
async def shop(request, sessions):  # noqa: F811
    """A connected test database, plus a scratch table this test may change."""
    s = settings()
    cfg = DBS[request.param]
    src = DataSource(name=f"Shop {request.param}", database="shop", username="shop",
                     password_encrypted=encrypt("shop"), edit_policy="read_only", row_limit=200,
                     timeout_sec=15, enabled=True, **cfg)
    try:
        async with connect(src, s) as conn:
            await conn.version()
    except (DbError, HostNotAllowed) as exc:
        pytest.skip(f"test database not running ({exc}); start the testdbs profile")
    scratch = "t_" + uuid4().hex[:10]
    async with connect(src, s) as conn:
        await conn.execute(f"CREATE TABLE {scratch} (id int PRIMARY KEY, status varchar(20))")
        await conn.execute(f"INSERT INTO {scratch} VALUES (1, 'pending'), (2, 'pending'), "
                           "(3, 'shipped')")
    async with sessions() as db:
        db.add(src)
        db.add(User(email=f"u{uuid4().hex[:6]}@test", password_hash="x"))
        await db.commit()
        await refresh_schema(db, src, s)
        user = (await db.scalars(select(User))).first()
    yield src, scratch, user
    async with connect(src, s) as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {scratch}")


async def ask(sessions, src, question, route, actions, answer="Done [Q1].", sent=None,  # noqa: F811
              user_id=None):
    sent = [] if sent is None else sent
    chat = scripted_chat({"intent": "database", "query": question, "database": src.name,
                          **route}, actions, answer, sent)
    events = []
    async for raw in answer_stream([{"role": "user", "content": question}], [], settings=settings(),
                                   sessions=sessions, chat=chat, embedder=None, reranker=None,
                                   source_ids=[src.id], user_id=user_id):
        name, data = raw.strip().split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events, sent


def queries(events):
    return [d for e, d in events if e == "query"]


async def scratch_rows(src, scratch):
    async with connect(src, settings()) as conn:
        return (await conn.read(f"SELECT id, status FROM {scratch} ORDER BY id", 10)).rows


async def test_question_answered_from_query_results(sessions, shop):  # noqa: F811
    src, _, _ = shop
    events, sent = await ask(sessions, src, "How many orders are pending?", {"change": False}, [
        {"action": "query", "sql": "SELECT COUNT(*) AS n FROM orders WHERE status = 'pending'",
         "purpose": "count pending orders"},
        {"action": "answer"},
    ], answer="There are 2 pending orders [Q1].")
    (q,) = queries(events)
    assert q["status"] == "ok" and q["rows"] == [[2]] and q["columns"] == ["n"]
    done = events[-1][1]
    assert events[-1][0] == "done" and done["intent"] == "database" and done["cited"] == [1]
    # The agent saw the schema (with sample values); the writer saw the actual result.
    agent_prompt = sent[1]["messages"][0]["content"]
    assert "orders" in agent_prompt and "'pending'" in agent_prompt
    assert "change" not in agent_prompt.split("Reply with ONLY")[1]  # no write action offered
    assert "2" in sent[-1]["messages"][1]["content"]
    async with sessions() as db:
        logs = (await db.scalars(select(DsQueryLog))).all()
    assert [(lg.kind, lg.status, lg.rows) for lg in logs] == [("read", "ok", 1)]


async def test_sql_error_goes_back_to_the_model(sessions, shop):  # noqa: F811
    src, _, _ = shop
    events, sent = await ask(sessions, src, "Total revenue?", {"change": False}, [
        {"action": "query", "sql": "SELECT SUM(price_total) FROM order_items"},
        {"action": "query", "sql": "SELECT SUM(quantity * unit_price) AS revenue FROM order_items"},
        {"action": "answer"},
    ])
    first, second = queries(events)
    assert first["status"] == "error" and "price_total" in first["error"]
    assert second["status"] == "ok" and float(second["rows"][0][0]) == 5552.0
    assert "Error" in sent[2]["messages"][-1]["content"]  # the model was told what went wrong


async def test_a_question_can_never_write(sessions, shop):  # noqa: F811
    src, scratch, _ = shop
    async with sessions() as db:
        await db.execute(update(DataSource).where(DataSource.id == src.id)
                         .values(edit_policy="auto"))
        await db.commit()
    events, _ = await ask(sessions, src, "Which orders are pending?", {"change": False}, [
        # e.g. the model obeyed text planted in a row
        {"action": "query", "sql": f"UPDATE {scratch} SET status = 'x' WHERE id = 1"},
        {"action": "change", "sql": f"DELETE FROM {scratch} WHERE id = 2"},
    ])
    assert [q["status"] for q in queries(events)] == ["refused", "refused"]
    assert await scratch_rows(src, scratch) == [[1, "pending"], [2, "pending"], [3, "shipped"]]


async def test_never_edit_refuses_changes(sessions, shop):  # noqa: F811
    src, scratch, _ = shop
    events, sent = await ask(sessions, src, "Mark order 1 as shipped", {"change": True}, [
        {"action": "change", "sql": f"UPDATE {scratch} SET status = 'shipped' WHERE id = 1"},
    ])
    (q,) = queries(events)
    assert q["status"] == "refused" and "Never edit" in q["error"]
    assert "Never edit" in sent[1]["messages"][0]["content"]
    assert (await scratch_rows(src, scratch))[0] == [1, "pending"]


async def test_ask_mode_waits_for_approval(sessions, shop):  # noqa: F811
    src, scratch, user = shop
    async with sessions() as db:
        await db.execute(update(DataSource).where(DataSource.id == src.id)
                         .values(edit_policy="ask"))
        await db.commit()
    events, _ = await ask(sessions, src, "Mark order 1 as shipped", {"change": True}, [
        {"action": "change", "sql": f"UPDATE {scratch} SET status = 'shipped' WHERE id = 1",
         "reason": "mark order 1 shipped"},
    ], user_id=user.id)
    (q,) = queries(events)
    assert q["status"] == "pending" and q["rowcount"] == 1
    preview = q["pending"]["preview"]
    assert preview["before"]["rows"] == [[1, "pending"]]
    assert preview["after"]["rows"] == [[1, "shipped"]]
    assert (await scratch_rows(src, scratch))[0] == [1, "pending"]  # trial run rolled back

    async with sessions() as db:
        out = await api.approve(q["pending"]["id"], db, user)
    assert out.status == "applied" and out.result["rowcount"] == 1
    assert (await scratch_rows(src, scratch))[0] == [1, "shipped"]
    async with sessions() as db:  # approving twice is refused
        with pytest.raises(HTTPException) as err:
            await api.approve(q["pending"]["id"], db, user)
    assert err.value.status_code == 409


async def test_reject_expiry_and_tampering(sessions, shop):  # noqa: F811
    src, scratch, user = shop
    async with sessions() as db:
        await db.execute(update(DataSource).where(DataSource.id == src.id)
                         .values(edit_policy="ask"))
        await db.commit()
    ids = []
    for _ in range(3):
        events, _ = await ask(sessions, src, "Delete order 3", {"change": True}, [
            {"action": "change", "sql": f"DELETE FROM {scratch} WHERE id = 3"}], user_id=user.id)
        ids.append(queries(events)[0]["pending"]["id"])
    async with sessions() as db:
        assert (await api.reject(ids[0], db, user)).status == "rejected"
        await db.execute(update(DsPendingChange).where(DsPendingChange.id == ids[1])
                         .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.execute(update(DsPendingChange).where(DsPendingChange.id == ids[2])
                         .values(sql=f"DELETE FROM {scratch} WHERE id > 0"))
        await db.commit()
    for change_id, msg in ((ids[0], "already rejected"), (ids[1], "expired"),
                           (ids[2], "altered")):
        async with sessions() as db:
            with pytest.raises(HTTPException) as err:
                await api.approve(change_id, db, user)
        assert msg in err.value.detail
    assert len(await scratch_rows(src, scratch)) == 3  # nothing was deleted


async def test_automatic_mode(sessions, shop):  # noqa: F811
    src, scratch, user = shop
    async with sessions() as db:
        await db.execute(update(DataSource).where(DataSource.id == src.id)
                         .values(edit_policy="auto"))
        await db.commit()
    events, _ = await ask(sessions, src, "Mark all pending as shipped and add a note column",
                          {"change": True}, [
        {"action": "change", "sql": f"UPDATE {scratch} SET status = 'shipped' "
                                    "WHERE status = 'pending'"},
        {"action": "change", "sql": f"ALTER TABLE {scratch} ADD COLUMN note varchar(50)"},
    ], user_id=user.id)
    data, schema = queries(events)
    assert data["status"] == "applied" and data["rowcount"] == 2  # data: applied right away
    assert schema["status"] == "pending"  # schema change: still needs approval
    assert [r[1] for r in await scratch_rows(src, scratch)] == ["shipped"] * 3


async def test_hidden_tables_are_never_queried(sessions, shop):  # noqa: F811
    src, _, _ = shop
    async with sessions() as db:
        await db.execute(update(DsSchemaItem).where(DsSchemaItem.source_id == src.id,
                                                    DsSchemaItem.table_name == "staff_secrets")
                         .values(hidden=True))
        await db.commit()
    events, sent = await ask(sessions, src, "Show the staff logins", {"change": False}, [
        {"action": "query", "sql": "SELECT login, password FROM staff_secrets"}])
    (q,) = queries(events)
    assert q["status"] == "refused" and "hidden" in q["error"]
    assert "staff_secrets" not in sent[1]["messages"][0]["content"]  # not even in the prompt


async def test_host_guard():
    s = get_settings().model_copy(update={"allow_private_db_hosts": False})
    with pytest.raises(HostNotAllowed, match="Smish's own"):
        await resolve_db_host("postgres", s.model_copy(update={"allow_private_db_hosts": True}))
    with pytest.raises(HostNotAllowed, match="private"):
        await resolve_db_host("10.1.2.3", s)
    with pytest.raises(HostNotAllowed):
        await resolve_db_host("evil.com/x", s)
    assert await resolve_db_host("8.8.8.8", s) == "8.8.8.8"


@pytest.mark.parametrize("host,message", [
    ("db..example.com", "isn't a valid host name"),  # used to crash the API (UnicodeError)
    (".example.com", "isn't a valid host name"),
    ("a" * 70 + ".com", "isn't a valid host name"),
    ("-bad.com", "isn't a valid host name"),
    ("db.example.com:5432", "Port field"),
    ("postgres://u:p@db.example.com/shop", "not a URL"),
    ("", "host name or IP"),
])
async def test_bad_host_input_gets_a_clear_message(host, message):
    with pytest.raises(HostNotAllowed, match=message):
        await resolve_db_host(host, settings())


async def test_connection_test_never_crashes(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr("app.datasources.connectors.resolve_db_host", boom)
    res = await api.run_test(DataSource(kind="postgres", host="h", port=5432, database="d",
                                        username="u", ssl_mode="prefer", timeout_sec=5))
    assert res.ok is False and "something unexpected" in res.error


def test_password_never_returned():
    src = DataSource(id=1, name="x", kind="postgres", host="h", port=1, database="d",
                     username="u", password_encrypted=encrypt("s3cret"), ssl_mode="prefer",
                     edit_policy="ask", row_limit=1, timeout_sec=1, enabled=True,
                     created_at=datetime.now(UTC))
    body = api.out(src).model_dump_json()
    assert "s3cret" not in body and "password_encrypted" not in body
    assert json.loads(body)["has_password"] is True
