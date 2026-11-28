"""End-to-end tests for multi-user auth, admin user management, privacy/source isolation,
PostgreSQL conversations (with `name`), admin metrics, and runtime settings.
"""

import json

import httpx
import pytest_asyncio
from openai import AsyncOpenAI
from sqlalchemy import select

import app.api.ask as ask_mod
import app.api.chat as chat_mod
from app.api.auth import ensure_admin
from app.config import Settings
from app.db.models import Conversation, DataSource, Message, Site
from app.db.session import get_session
from app.email import Mailer, get_mailer
from app.llm.chat import ChatTarget, get_chat
from app.main import app
from app.security.crypto import encrypt
from tests.integration.test_crawl_and_answer import sessions  # noqa: F401 - pytest fixture

ADMIN_EMAIL, ADMIN_PW = "admin@smish.test", "Admin@12345"
ORIGIN = "http://localhost:3000"


class _RecordingMailer(Mailer):
    def __init__(self) -> None:
        self.sent: list[dict[str, str | None]] = []

    @property
    def is_configured(self) -> bool:
        return True

    async def send_verification_email(
        self, *, to_email: str, name: str, username: str | None, token: str
    ) -> None:
        self.sent.append(
            {"kind": "verify", "to": to_email, "name": name, "username": username, "token": token}
        )

    async def send_password_reset_email(
        self, *, to_email: str, name: str, username: str | None, token: str
    ) -> None:
        self.sent.append(
            {"kind": "reset", "to": to_email, "name": name, "username": username, "token": token}
        )


@pytest_asyncio.fixture
async def make_client(sessions):  # noqa: F811
    async def override():
        async with sessions() as s:
            yield s

    fake_mailer = _RecordingMailer()

    async with sessions() as db:
        await ensure_admin(
            db, Settings(_env_file=None, admin_email=ADMIN_EMAIL, admin_password=ADMIN_PW)
        )
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_mailer] = lambda: fake_mailer
    clients: list[httpx.AsyncClient] = []

    async def factory() -> httpx.AsyncClient:
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"origin": ORIGIN},
        )
        clients.append(c)
        return c

    factory.mailer = fake_mailer  # type: ignore[attr-defined]
    yield factory
    for c in clients:
        await c.aclose()
    app.dependency_overrides.clear()


async def login(client: httpx.AsyncClient, identifier: str, password: str) -> dict:
    r = await client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_admin_creates_users_and_enforces_privacy(make_client, sessions):  # noqa: F811
    admin = await make_client()
    user_a = await make_client()
    user_b = await make_client()

    await login(admin, ADMIN_EMAIL, ADMIN_PW)

    # Admin creates two regular users with email, username, name, password.
    r1 = await admin.post(
        "/api/v1/admin/users",
        json={
            "email": "asha@smish.test",
            "username": "asha.rao",
            "name": "Asha Rao",
            "password": "UserPass@1",
            "role": "member",
        },
    )
    assert r1.status_code == 201, r1.text
    r2 = await admin.post(
        "/api/v1/admin/users",
        json={
            "email": "ben@smish.test",
            "username": "ben_c",
            "name": "Ben Carter",
            "password": "UserPass@2",
            "role": "member",
        },
    )
    assert r2.status_code == 201, r2.text
    asha_id, ben_id = r1.json()["id"], r2.json()["id"]

    # Both users can sign in with username OR email.
    me_a = await login(user_a, "ASHA.RAO", "UserPass@1")
    assert me_a["name"] == "Asha Rao" and me_a["role"] == "member"
    me_b = await login(user_b, "ben@smish.test", "UserPass@2")
    assert me_b["username"] == "ben_c"

    # Regular users are blocked from admin endpoints.
    assert (await user_a.get("/api/v1/admin/users")).status_code == 403
    assert (await user_a.get("/api/v1/admin/metrics")).status_code == 403
    assert (await user_a.get("/api/v1/admin/settings")).status_code == 403

    # Each user owns isolated sites and databases (even with identical URL/DB names).
    async with sessions() as db:
        db.add_all(
            [
                Site(user_id=asha_id, name="Docs", root_url="https://example.com/"),
                Site(user_id=ben_id, name="Docs", root_url="https://example.com/"),
                DataSource(
                    user_id=asha_id,
                    name="Shop DB",
                    kind="postgres",
                    host="db-asha.internal",
                    port=5432,
                    database="shop_a",
                    username="secret_user_a",
                    password_encrypted=encrypt("super_secret_a"),
                ),
                DataSource(
                    user_id=ben_id,
                    name="Shop DB",
                    kind="mysql",
                    host="db-ben.internal",
                    port=3306,
                    database="shop_b",
                    username="secret_user_b",
                    password_encrypted=encrypt("super_secret_b"),
                ),
            ]
        )
        await db.commit()
        asha_site = await db.scalar(select(Site).where(Site.user_id == asha_id))
        asha_db = await db.scalar(select(DataSource).where(DataSource.user_id == asha_id))

    # Asha sees only her own sources; Admin sees only Admin's own (none yet) in /sites and
    # /data-sources, and cannot access Asha's site/DB directly.
    assert len((await user_a.get("/api/v1/sites")).json()) == 1
    assert (await admin.get("/api/v1/sites")).json() == []
    assert (await admin.get("/api/v1/data-sources")).json() == []
    assert (await admin.get(f"/api/v1/sites/{asha_site.id}")).status_code == 404
    assert (await user_b.get(f"/api/v1/sites/{asha_site.id}")).status_code == 404
    assert (await admin.get(f"/api/v1/data-sources/{asha_db.id}/schema")).status_code == 404

    # Admin user list shows source summaries WITHOUT any DB credentials/host/username.
    users_list = (await admin.get("/api/v1/admin/users")).json()
    asha_row = next(u for u in users_list if u["id"] == asha_id)
    assert len(asha_row["sites"]) == 1 and asha_row["sites"][0]["name"] == "Docs"
    assert len(asha_row["databases"]) == 1 and asha_row["databases"][0]["name"] == "Shop DB"
    dumped = json.dumps(users_list)
    for secret in ("db-asha.internal", "secret_user_a", "super_secret_a", "shop_a"):
        assert secret not in dumped

    # Asha creates a conversation in Postgres (`name` column), Admin cannot read it.
    conv_res = await user_a.post(
        "/api/v1/conversations",
        json={"name": "Private Asha Thread", "mode": "model", "selected_sources": []},
    )
    assert conv_res.status_code == 201
    conv_id = conv_res.json()["id"]
    async with sessions() as db:
        row = await db.get(Conversation, conv_id)
        assert row is not None and row.name == "Private Asha Thread" and row.user_id == asha_id
        db.add(
            Message(
                conversation_id=conv_id, role="user", content="Secret user note", mode="model"
            )
        )
        await db.commit()

    assert (await admin.get("/api/v1/conversations")).json() == []
    assert (await admin.get(f"/api/v1/conversations/{conv_id}")).status_code == 404
    assert (await user_b.get(f"/api/v1/conversations/{conv_id}")).status_code == 404
    detail = (await user_a.get(f"/api/v1/conversations/{conv_id}")).json()
    assert detail["name"] == "Private Asha Thread" and len(detail["messages"]) == 1

    # Metrics show aggregate counts, never message text.
    metrics = (await admin.get("/api/v1/admin/metrics")).json()
    assert metrics["users"]["total"] == 3
    assert metrics["chats"]["conversations"] == 1
    assert metrics["sites"]["total"] == 2
    assert metrics["databases"]["total"] == 2
    assert "Secret user note" not in json.dumps(metrics)

    # Admin can deactivate Ben, which revokes his active session immediately.
    deact = await admin.patch(f"/api/v1/admin/users/{ben_id}", json={"is_active": False})
    assert deact.status_code == 200 and deact.json()["is_active"] is False
    assert (await user_b.get("/api/v1/auth/me")).status_code == 401
    relogin = await user_b.post(
        "/api/v1/auth/login", json={"identifier": "ben_c", "password": "UserPass@2"}
    )
    assert relogin.status_code == 403

    # Admin can update runtime system settings in Postgres.
    cur_settings = (await admin.get("/api/v1/admin/settings")).json()
    cur_settings["max_history_chars"] = 48_000
    updated = await admin.put("/api/v1/admin/settings", json=cur_settings)
    assert updated.status_code == 200 and updated.json()["max_history_chars"] == 48_000


async def test_self_registration_requires_email_verification_and_approval(make_client):
    admin = await make_client()
    guest = await make_client()
    guest2 = await make_client()
    spammer = await make_client()
    mailer: _RecordingMailer = make_client.mailer  # type: ignore[attr-defined]
    await login(admin, ADMIN_EMAIL, ADMIN_PW)

    # Normal user registers; role stays "member", inactive, and email unverified.
    reg = await guest.post(
        "/api/v1/auth/register",
        json={
            "name": "Divya Menon",
            "username": "divya.m",
            "email": "divya@smish.test",
            "password": "UserPass@9",
            "role": "admin",
        },
    )
    assert reg.status_code == 201, reg.text
    assert reg.json()["pending_approval"] is True
    assert reg.json()["email_verification_required"] is True
    assert reg.json()["email_sent"] is True
    divya_id = reg.json()["id"]
    divya_verify = next(
        m for m in mailer.sent if m["kind"] == "verify" and m["to"] == "divya@smish.test"
    )

    # Duplicate email or username is refused.
    dup = await guest.post(
        "/api/v1/auth/register",
        json={
            "name": "Other",
            "username": "DIVYA.M",
            "email": "other@smish.test",
            "password": "UserPass@9",
        },
    )
    assert dup.status_code == 409

    # Before verification & approval: wrong password gives 401, right gives 403.
    wrong = await guest.post(
        "/api/v1/auth/login", json={"identifier": "divya.m", "password": "wrong"}
    )
    assert wrong.status_code == 401
    pending_login = await guest.post(
        "/api/v1/auth/login", json={"identifier": "divya.m", "password": "UserPass@9"}
    )
    assert pending_login.status_code == 403
    assert "verify your email" in pending_login.json()["detail"].lower()

    # Second pending request that the admin will reject.
    reg2 = await spammer.post(
        "/api/v1/auth/register",
        json={
            "name": "Spam",
            "username": "spam_acct",
            "email": "spam@smish.test",
            "password": "UserPass@9",
        },
    )
    assert reg2.status_code == 201
    spam_id = reg2.json()["id"]

    # Admin sees pending approvals and unverified flag in users list and metrics.
    users = (await admin.get("/api/v1/admin/users")).json()
    divya_row = next(u for u in users if u["id"] == divya_id)
    assert divya_row["pending_approval"] is True and divya_row["role"] == "member"
    assert divya_row["email_verified"] is False
    metrics = (await admin.get("/api/v1/admin/metrics")).json()
    assert metrics["users"]["pending_approval"] == 2

    # Admin rejects spam request and approves Divya (without bypassing email verification).
    assert (await admin.post(f"/api/v1/admin/users/{spam_id}/reject")).status_code == 204
    approved = await admin.post(f"/api/v1/admin/users/{divya_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["is_active"] is True
    assert approved.json()["pending_approval"] is False
    assert approved.json()["approved_at"] is not None
    assert approved.json()["email_verified"] is False

    # Approved but unverified user still cannot sign in until email is verified.
    blocked = await guest.post(
        "/api/v1/auth/login", json={"identifier": "divya.m", "password": "UserPass@9"}
    )
    assert blocked.status_code == 403
    assert "verify your email" in blocked.json()["detail"].lower()

    # Divya verifies using emailed token (single-use), then signs in!
    verified = await guest.post(
        "/api/v1/auth/verify-email", json={"token": divya_verify["token"]}
    )
    assert verified.status_code == 200
    reused = await guest.post(
        "/api/v1/auth/verify-email", json={"token": divya_verify["token"]}
    )
    assert reused.status_code == 400
    me = await login(guest, "divya.m", "UserPass@9")
    assert me["name"] == "Divya Menon" and me["role"] == "member"
    assert me["email_verified_at"] is not None

    # Second user registers and does NOT click email link, but admin overrides via verify-email.
    reg3 = await guest2.post(
        "/api/v1/auth/register",
        json={
            "name": "Arjun Nair",
            "username": "arjun.n",
            "email": "arjun@smish.test",
            "password": "UserPass@9",
        },
    )
    assert reg3.status_code == 201
    arjun_id = reg3.json()["id"]
    override = await admin.post(
        f"/api/v1/admin/users/{arjun_id}/verify-email", json={"approve": True}
    )
    assert override.status_code == 200
    assert override.json()["email_verified"] is True
    assert override.json()["is_active"] is True
    assert override.json()["pending_approval"] is False
    me2 = await login(guest2, "arjun.n", "UserPass@9")
    assert me2["name"] == "Arjun Nair"


async def test_long_conversation_preserves_memory_past_twenty_messages(
    make_client, sessions, monkeypatch  # noqa: F811
):
    admin = await make_client()
    user = await make_client()
    await login(admin, ADMIN_EMAIL, ADMIN_PW)
    created = await admin.post(
        "/api/v1/admin/users",
        json={
            "email": "kiran@smish.test",
            "username": "kiran",
            "name": "Kiran",
            "password": "UserPass@99",
            "role": "member",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    # Configure a compact history window (4,000 chars) to exercise rolling summarization cleanly.
    cfg = (await admin.get("/api/v1/admin/settings")).json()
    cfg["max_history_chars"] = 4_000
    assert (await admin.put("/api/v1/admin/settings", json=cfg)).status_code == 200

    await login(user, "kiran", "UserPass@99")
    conv_id = (
        await user.post(
            "/api/v1/conversations",
            json={"name": "Long Memory Thread", "mode": "model", "selected_sources": []},
        )
    ).json()["id"]

    # Seed 24 prior messages (~500 chars each = ~12,000 chars > 4,000 char window).
    # Message #1 states an early secret fact ("project codename is Orion-42").
    async with sessions() as db:
        for idx in range(1, 25):
            role = "user" if idx % 2 else "assistant"
            body = (
                "Remember for the rest of this chat: my project codename is Orion-42. "
                if idx == 1
                else f"Turn {idx} detailed note: "
            ) + ("context " * 55)
            db.add(Message(conversation_id=conv_id, role=role, content=body, mode="model"))
        await db.commit()

    captured: list[dict] = []

    def fake_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        sys_msg = payload["messages"][0]["content"]
        user_msg = payload["messages"][-1]["content"]
        if "compact running memory" in sys_msg:
            text = (
                "- User budget preference: stay under $50."
                if "$50" in user_msg
                else "- Project codename is Orion-42."
            )
            return httpx.Response(
                200,
                json={
                    "id": "sum-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "nullish-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        if "Reply with JSON only" in sys_msg:
            return httpx.Response(
                200,
                json={
                    "id": "route-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "nullish-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": '{"intent": "chat", "query": ""}',
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        delta_chunk = {
            "id": "ans-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "nullish-chat",
            "choices": [
                {"index": 0, "delta": {"content": "Codename is Orion-42."}, "finish_reason": None}
            ],
        }
        stop_chunk = {
            "id": "ans-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "nullish-chat",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        sse_body = (
            f"data: {json.dumps(delta_chunk)}\n\n"
            f"data: {json.dumps(stop_chunk)}\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})

    target = ChatTarget(
        AsyncOpenAI(
            base_url="http://gpu:8001/v1",
            api_key="k",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_handler)),
        ),
        "nullish-chat",
    )
    app.dependency_overrides[get_chat] = lambda: target
    monkeypatch.setattr(chat_mod, "SessionLocal", sessions)
    monkeypatch.setattr(ask_mod, "SessionLocal", sessions)

    # Turn 25: exceeds window -> triggers incremental summarization & injects summary into chat.
    res = await user.post(
        "/api/v1/chat",
        json={
            "conversation_id": conv_id,
            "messages": [{"role": "user", "content": "What was my project codename from turn 1?"}],
        },
    )
    assert res.status_code == 200
    assert "Orion-42" in res.text

    compaction_calls = [
        p for p in captured if "compact running memory" in p["messages"][0]["content"]
    ]
    answer_calls = [p for p in captured if p.get("stream") is True]
    assert len(compaction_calls) >= 1
    assert len(answer_calls) == 1
    assert "Project codename is Orion-42." in answer_calls[0]["messages"][0]["content"]

    async with sessions() as db:
        conv = await db.get(Conversation, conv_id)
        assert conv is not None and conv.user_id == user_id
        assert conv.summary and "Orion-42" in conv.summary
        assert conv.summary_through_message_id is not None

    # Turn 26 fits inside window with cached summary -> zero extra compaction call.
    prior_compactions = len(compaction_calls)
    res2 = await user.post(
        "/api/v1/chat",
        json={
            "conversation_id": conv_id,
            "messages": [{"role": "user", "content": "Confirm the codename again."}],
        },
    )
    assert res2.status_code == 200
    compaction_calls_after = [
        p for p in captured if "compact running memory" in p["messages"][0]["content"]
    ]
    assert len(compaction_calls_after) == prior_compactions
    assert "Project codename is Orion-42." in captured[-1]["messages"][0]["content"]

    # Also verify /api/v1/ask preserves earlier summary past the 12-turn recent cutoff.
    site = Site(user_id=user_id, name="Docs", root_url="https://docs.smish.test/")
    async with sessions() as db:
        db.add(site)
        await db.commit()
    ask_conv_id = (
        await user.post(
            "/api/v1/conversations",
            json={"name": "Long Sources Thread", "mode": "sites", "selected_sources": []},
        )
    ).json()["id"]
    async with sessions() as db:
        for idx in range(1, 17):
            role = "user" if idx % 2 else "assistant"
            msg = (
                "Earlier budget preference: stay under $50. " if idx == 1 else f"Sources turn {idx}"
            )
            db.add(Message(conversation_id=ask_conv_id, role=role, content=msg, mode="sites"))
        await db.commit()

    captured.clear()
    ask_res = await user.post(
        "/api/v1/ask",
        json={
            "conversation_id": ask_conv_id,
            "messages": [
                {"role": "user", "content": "Compare shoes within my budget from earlier"}
            ],
        },
    )
    assert ask_res.status_code == 200
    assert any("compact running memory" in p["messages"][0]["content"] for p in captured)
    router_reqs = [p for p in captured if "Reply with JSON only" in p["messages"][0]["content"]]
    assert router_reqs and "stay under $50" in router_reqs[-1]["messages"][-1]["content"]
    answer_reqs = [p for p in captured if p.get("stream") is True]
    assert answer_reqs and "stay under $50" in answer_reqs[-1]["messages"][0]["content"]
    async with sessions() as db:
        ask_conv = await db.get(Conversation, ask_conv_id)
        assert (
            ask_conv is not None
            and ask_conv.summary
            and "stay under $50" in ask_conv.summary
            and ask_conv.summary_through_message_id is not None
        )


async def test_user_storage_mode_local_vs_db_isolation(
    make_client, sessions, monkeypatch  # noqa: F811
):
    admin = await make_client()
    user = await make_client()
    await login(admin, ADMIN_EMAIL, ADMIN_PW)
    created = await admin.post(
        "/api/v1/admin/users",
        json={
            "email": "localuser@smish.test",
            "username": "localuser",
            "name": "Local User",
            "password": "UserPass@77",
            "role": "member",
        },
    )
    assert created.status_code == 201
    uid = created.json()["id"]

    # Initially unset until user chooses security preference on first entry.
    me0 = await login(user, "localuser", "UserPass@77")
    assert me0["storage_mode"] is None

    # User chooses browser-local storage only.
    chosen = await user.post("/api/v1/auth/storage-mode", json={"storage_mode": "local"})
    assert chosen.status_code == 200 and chosen.json()["storage_mode"] == "local"

    # Server DB persistence endpoints refuse writes and list nothing in local mode.
    assert (await user.get("/api/v1/conversations")).json() == []
    assert (await user.get("/api/v1/data-sources")).json() == []
    assert (
        await user.post("/api/v1/conversations", json={"name": "Nope", "mode": "model"})
    ).status_code == 409
    assert (
        await user.post(
            "/api/v1/data-sources",
            json={
                "kind": "postgres",
                "host": "db.internal",
                "database": "shop",
                "username": "u",
            },
        )
    ).status_code == 409

    # Stateless /chat with rolling summary emits `event: memory` without saving Postgres rows.
    cfg = (await admin.get("/api/v1/admin/settings")).json()
    cfg["max_history_chars"] = 4_000
    assert (await admin.put("/api/v1/admin/settings", json=cfg)).status_code == 200

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "compact running memory" in payload["messages"][0]["content"]:
            return httpx.Response(
                200,
                json={
                    "id": "sum-loc",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "nullish-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "- User prefers tea."},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        c1 = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "nullish-chat",
            "choices": [{"index": 0, "delta": {"content": "Noted."}, "finish_reason": None}],
        }
        c2 = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "nullish-chat",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        sse = f"data: {json.dumps(c1)}\n\ndata: {json.dumps(c2)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    target = ChatTarget(
        AsyncOpenAI(
            base_url="http://gpu:8001/v1",
            api_key="k",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
        "nullish-chat",
    )
    app.dependency_overrides[get_chat] = lambda: target
    monkeypatch.setattr(chat_mod, "SessionLocal", sessions)

    long_msgs = [
        {
            "role": "user" if i % 2 else "assistant",
            "content": ("Preference: I prefer tea. " if i == 1 else f"Turn {i} ")
            + ("filler " * 80),
        }
        for i in range(1, 16)
    ]
    res = await user.post(
        "/api/v1/chat",
        json={"conversation_id": "ephemeral-ignored-id", "messages": long_msgs},
    )
    assert res.status_code == 200
    assert "event: memory" in res.text and "User prefers tea." in res.text

    async with sessions() as db:
        convs = (await db.scalars(select(Conversation).where(Conversation.user_id == uid))).all()
        assert convs == []

    # Switching to secure server DB mode enables persistent Postgres storage, and switching
    # back with purge_server_data=True wipes server conversations.
    assert (
        await user.post("/api/v1/auth/storage-mode", json={"storage_mode": "db"})
    ).status_code == 200
    created_conv = await user.post(
        "/api/v1/conversations", json={"name": "Cloud Chat", "mode": "model"}
    )
    assert created_conv.status_code == 201
    purged = await user.post(
        "/api/v1/auth/storage-mode",
        json={"storage_mode": "local", "purge_server_data": True},
    )
    assert purged.status_code == 200 and purged.json()["storage_mode"] == "local"
    async with sessions() as db:
        convs_after = (
            await db.scalars(select(Conversation).where(Conversation.user_id == uid))
        ).all()
        assert convs_after == []


async def test_admin_legal_settings_and_public_read(make_client):
    anon = await make_client()
    member = await make_client()
    admin = await make_client()

    # Public read works before sign-in and serves defaults.
    pub0 = await anon.get("/api/v1/legal")
    assert pub0.status_code == 200
    assert pub0.json()["meta"]["operator_name"] == "Harish Rohit Israel"
    assert pub0.json()["documents"] == {}

    await login(admin, ADMIN_EMAIL, ADMIN_PW)
    created = await admin.post(
        "/api/v1/admin/users",
        json={
            "email": "reader@smish.test",
            "username": "reader",
            "name": "Reader",
            "password": "UserPass@42",
            "role": "member",
        },
    )
    assert created.status_code == 201
    await login(member, "reader", "UserPass@42")

    # Non-admin users cannot access admin legal endpoints.
    assert (await member.get("/api/v1/admin/legal")).status_code == 403
    assert (await member.put("/api/v1/admin/legal", json=pub0.json())).status_code == 403
    assert (await member.post("/api/v1/admin/legal/reset")).status_code == 403

    # Admin updates operator metadata and a custom Terms document.
    cur = (await admin.get("/api/v1/admin/legal")).json()
    cur["meta"]["operator_name"] = "Smish Technologies Pvt. Ltd."
    cur["meta"]["contact_email"] = "legal@smish.io"
    cur["documents"] = {
        "terms": {
            "id": "terms",
            "title": "Service Terms",
            "subtitle": "Updated rules for Smish.",
            "summary": "Custom summary.",
            "sections": [
                {
                    "heading": "1. Custom Pricing",
                    "blocks": [{"kind": "p", "text": "Subscriptions renew monthly."}],
                }
            ],
        }
    }
    saved = await admin.put("/api/v1/admin/legal", json=cur)
    assert saved.status_code == 200

    pub1 = await anon.get("/api/v1/legal")
    assert pub1.status_code == 200
    assert pub1.json()["meta"]["operator_name"] == "Smish Technologies Pvt. Ltd."
    assert pub1.json()["meta"]["contact_email"] == "legal@smish.io"
    assert pub1.json()["documents"]["terms"]["title"] == "Service Terms"

    # Admin can reset legal config back to defaults.
    reset = await admin.post("/api/v1/admin/legal/reset")
    assert reset.status_code == 200
    assert reset.json()["meta"]["operator_name"] == "Harish Rohit Israel"
    assert reset.json()["documents"] == {}


async def test_member_cannot_edit_email_or_username_via_profile(make_client):
    admin = await make_client()
    member = await make_client()
    await login(admin, ADMIN_EMAIL, ADMIN_PW)
    created = await admin.post(
        "/api/v1/admin/users",
        json={
            "email": "locked@smish.test",
            "username": "locked_user",
            "name": "Locked User",
            "password": "UserPass@11",
            "role": "member",
        },
    )
    assert created.status_code == 201
    await login(member, "locked_user", "UserPass@11")

    # Regular users can update their display name.
    ok = await member.patch("/api/v1/auth/profile", json={"name": "Updated Name"})
    assert ok.status_code == 200
    assert ok.json()["name"] == "Updated Name"

    # Regular users are refused when attempting to change email or username.
    bad_email = await member.patch("/api/v1/auth/profile", json={"email": "new@smish.test"})
    assert bad_email.status_code == 403
    bad_uname = await member.patch("/api/v1/auth/profile", json={"username": "hacked_name"})
    assert bad_uname.status_code == 403

    me = (await member.get("/api/v1/auth/me")).json()
    assert me["email"] == "locked@smish.test" and me["username"] == "locked_user"


async def test_forgot_and_reset_password_recovers_access_and_revokes_sessions(make_client):
    admin = await make_client()
    user_sess = await make_client()
    anon = await make_client()
    mailer: _RecordingMailer = make_client.mailer  # type: ignore[attr-defined]

    await login(admin, ADMIN_EMAIL, ADMIN_PW)
    created = await admin.post(
        "/api/v1/admin/users",
        json={
            "email": "meera@smish.test",
            "username": "meera.k",
            "name": "Meera",
            "password": "OldPassword@1",
            "role": "member",
        },
    )
    assert created.status_code == 201
    await login(user_sess, "meera.k", "OldPassword@1")

    # Unknown account still answers 200 and sends nothing (anti-enumeration).
    sent_before = len(mailer.sent)
    unknown = await anon.post(
        "/api/v1/auth/forgot-password", json={"identifier": "nobody@smish.test"}
    )
    assert unknown.status_code == 200
    assert len(mailer.sent) == sent_before

    # Known email or username receives reset token plus username ID reminder.
    forgot = await anon.post(
        "/api/v1/auth/forgot-password", json={"identifier": "meera@smish.test"}
    )
    assert forgot.status_code == 200
    reset_mail = next(
        m for m in reversed(mailer.sent) if m["kind"] == "reset" and m["to"] == "meera@smish.test"
    )
    assert reset_mail["username"] == "meera.k"
    assert reset_mail["token"]

    # Reset password completes, revokes existing sessions, and invalidates token reuse.
    done = await anon.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_mail["token"], "new_password": "NewPassword@2"},
    )
    assert done.status_code == 200
    assert (
        await anon.post(
            "/api/v1/auth/reset-password",
            json={"token": reset_mail["token"], "new_password": "AnotherPassword@3"},
        )
    ).status_code == 400
    assert (await user_sess.get("/api/v1/auth/me")).status_code == 401
    assert (
        await anon.post(
            "/api/v1/auth/login", json={"identifier": "meera.k", "password": "OldPassword@1"}
        )
    ).status_code == 401

    me = await login(anon, "meera.k", "NewPassword@2")
    assert me["email"] == "meera@smish.test"


