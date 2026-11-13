import asyncio
from collections.abc import Mapping

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from ghostband.server import security_policy


async def accepted(_request: web.Request) -> web.Response:
    return web.Response(text="accepted")


@pytest.mark.parametrize(
    ("path", "headers", "status"),
    [
        ("/api/upload", {"X-Ghostband-Token": "secret"}, 200),
        ("/api/upload", {"X-Ghostband-Token": "wrong"}, 401),
        ("/api/upload", {}, 401),
        ("/api/upload", {"X-Ghostband-Token": "secret", "Origin": "https://evil.test"}, 403),
        ("/", {"Host": "evil.test:7777"}, 403),
        ("/", {"Host": "localhost:7777.evil.test"}, 403),
        ("/", {"Host": "localhost:7777", "Origin": "http://localhost:7777"}, 200),
        ("/", {"Origin": "null"}, 403),
        ("/", {"Origin": "http://localhost:5173"}, 403),
        ("/ws", {"Sec-WebSocket-Protocol": "ghostband.secret"}, 200),
        ("/ws", {"Sec-WebSocket-Protocol": "other, ghostband.secret"}, 200),
        ("/ws", {"Sec-WebSocket-Protocol": "ghostband.wrong"}, 401),
        ("/ws", {"X-Ghostband-Token": "secret"}, 401),
        ("/?t=secret", {}, 200),
        ("/api/upload?t=secret", {}, 401),
        (
            "/api/upload",
            {"X-Ghostband-Token": "secret", "Content-Length": "209715201"},
            413,
        ),
    ],
)
def test_request_security(path: str, headers: Mapping[str, str], status: int) -> None:
    async def exercise() -> None:
        app = web.Application(middlewares=[security_policy("secret")])
        app.router.add_route("*", "/{path:.*}", accepted)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(path, headers={"Host": "127.0.0.1:7777", **headers})
            assert response.status == status

    asyncio.run(exercise())


def test_development_origin_requires_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHOSTBAND_DEV", raising=False)
    with pytest.raises(ValueError, match="GHOSTBAND_DEV=1"):
        security_policy("secret", dev_origin="http://localhost:5173")


@pytest.mark.parametrize(
    "origin",
    [
        "https://localhost:5173",
        "http://evil.test:5173",
        "http://localhost:5173/",
        "http://localhost:5173?x=1",
        "http://localhost:5173#fragment",
        "http://user@localhost:5173",
        "http://localhost",
    ],
)
def test_development_origin_stays_on_loopback(origin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHOSTBAND_DEV", "1")
    with pytest.raises(ValueError, match="HTTP loopback origin"):
        security_policy("secret", dev_origin=origin)


def test_development_origin_allows_only_configured_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHOSTBAND_DEV", "1")

    async def exercise() -> None:
        app = web.Application(
            middlewares=[security_policy("secret", dev_origin="http://localhost:5173")]
        )
        app.router.add_get("/", accepted)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(
                "/", headers={"Host": "localhost:5173", "Origin": "http://localhost:5173"}
            )
            assert response.status == 200
            response = await client.get(
                "/", headers={"Host": "localhost:5174", "Origin": "http://localhost:5174"}
            )
            assert response.status == 403

    asyncio.run(exercise())
