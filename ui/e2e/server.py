"""Serve the real app with a fake engine on an OS-assigned test port."""

import secrets
import socket

from aiohttp import web
from ghostband.server import create_app
from ghostband.session import Session

listener = socket.socket()
listener.bind(("127.0.0.1", 0))
token = secrets.token_urlsafe(32)
origin = f"http://127.0.0.1:{listener.getsockname()[1]}"


def ready(_message: str) -> None:
    print(f"{origin}/?t={token}", flush=True)


web.run_app(
    create_app(Session(fake_engine=True), token=token, dev_origin=origin),
    sock=listener,
    print=ready,
)
