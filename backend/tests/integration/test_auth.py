"""Login end to end: real app routes + the test database (no lifespan, no GPU)."""

import httpx
import pytest_asyncio
from sqlalchemy import select

from app.api.auth import ensure_admin
from app.config import Settings
from app.db.models import User
from app.db.session import get_session
from app.main import app
from tests.integration.test_crawl_and_answer import sessions  # noqa: F401 - pytest fixture

EMAIL, PASSWORD = "admin@hudsmersolutions.com", "Hudsmers@123"
ORIGIN = "http://localhost:3000"


@pytest_asyncio.fixture
async def client(sessions):  # noqa: F811 - the imported fixture
    async def override():
        async with sessions() as s:
            yield s

    async with sessions() as db:
        await ensure_admin(db, Settings(_env_file=None, admin_email=EMAIL, admin_password=PASSWORD))
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test", headers={"origin": ORIGIN}) as c:
        yield c
    app.dependency_overrides.clear()


async def test_protected_routes_need_login(client):
    assert (await client.get("/api/v1/sites")).status_code == 401
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/health")).status_code == 200  # stays public


async def test_login_me_logout(client):
    bad = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "nope"})
    assert bad.status_code == 401 and "Wrong email or password" in bad.json()["detail"]
    unknown = await client.post("/api/v1/auth/login", json={"email": "x@y.z", "password": "nope"})
    assert unknown.status_code == 401  # same answer: no hint which part was wrong

    ok = await client.post("/api/v1/auth/login",
                           json={"email": "  ADMIN@hudsmersolutions.com ", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["email"] == EMAIL
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie

    assert (await client.get("/api/v1/auth/me")).json()["email"] == EMAIL
    assert (await client.get("/api/v1/sites")).status_code == 200

    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    client.cookies.clear()
    assert (await client.get("/api/v1/sites")).status_code == 401


async def test_old_session_token_is_dead_after_logout(client):
    await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    token = client.cookies.get("nullish_session")
    await client.post("/api/v1/auth/logout")
    client.cookies.set("nullish_session", token)  # replaying the old cookie
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_lockout_after_repeated_failures(client):
    for _ in range(5):
        await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong"})
    locked = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert locked.status_code == 429  # even the right password is refused while locked


async def test_change_password(client, sessions):  # noqa: F811
    await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    url = "/api/v1/auth/change-password"
    wrong = await client.post(url, json={"current_password": "x", "new_password": "Another@456"})
    assert wrong.status_code == 400
    short = await client.post(url, json={"current_password": PASSWORD, "new_password": "short"})
    assert short.status_code == 400
    ok = await client.post(url, json={"current_password": PASSWORD, "new_password": "Another@456"})
    assert ok.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 200  # this browser stays in
    client.cookies.clear()
    old = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert old.status_code == 401
    new = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "Another@456"})
    assert new.status_code == 200
    async with sessions() as db:
        stored = await db.scalar(select(User.password_hash))
    assert stored.startswith("$argon2id$") and "Another@456" not in stored


async def test_cross_site_writes_refused(client):
    evil = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD},
                             headers={"origin": "https://evil.example"})
    assert evil.status_code == 403


async def test_admin_seeded_once(sessions):  # noqa: F811
    s = Settings(_env_file=None, admin_email=EMAIL, admin_password=PASSWORD)
    async with sessions() as db:
        assert await ensure_admin(db, s) is True
        again = Settings(_env_file=None, admin_email=EMAIL, admin_password="Other@999")
        assert await ensure_admin(db, again) is False  # never overwrites an existing account
        assert len(list(await db.scalars(select(User)))) == 1
