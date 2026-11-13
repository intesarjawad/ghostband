"""A child process that takes the engine's place, serving the fake engine on its socket.

It is started the way the conductor starts the engine, `ENGINE --socket PATH`, through a
wrapper that `executable` writes. The wrapper records the process id beside the socket before
anything is imported, so a test can check the process is gone even when it was stopped early.
"""

import json
import os
import signal
import socket
import struct
import sys
import time
from pathlib import Path
from threading import Event, Thread
from typing import Literal

from ghostband.engine_client import Command
from ghostband.fake_engine import FakeEngine

# serve: answers like the engine; crash: dies by SIGKILL when play arrives;
# silent: runs without ever listening; exit: fails at start with status 1.
Mode = Literal["serve", "crash", "silent", "exit"]
# Longer than any test waits; the conductor kills the process well before.
SILENT_S = 60.0
# From <sys/un.h>: the option level and option that name a Unix socket's peer process.
SOL_LOCAL = 0
LOCAL_PEERPID = 0x002


class CrashingEngine(FakeEngine):
    def send(self, command: Command) -> None:
        if command.get("cmd") == "play":
            os.kill(os.getpid(), signal.SIGKILL)
        super().send(command)


def executable(directory: Path, mode: Mode) -> Path:
    """Write an engine executable that runs this file in the given mode."""
    path = directory / f"engine-{mode}"
    path.write_text(
        "#!/bin/sh\n"
        'printf %s "$$" > "${2%.sock}.pid"\n'
        f'exec "{sys.executable}" "{Path(__file__).resolve()}" {mode} "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def process_id(socket_path: Path) -> int:
    return int(socket_path.with_suffix(".pid").read_text(encoding="utf-8"))


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def serve(engine: FakeEngine, connection: socket.socket) -> None:
    with connection:
        engine.serve(connection, Event())


def main(mode: str, socket_path: Path) -> None:
    if mode == "exit":
        sys.exit(1)
    if mode == "silent":
        time.sleep(SILENT_S)
        return
    engine = (CrashingEngine if mode == "crash" else FakeEngine)(time.monotonic)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        listener.listen(1)
        serving: Thread | None = None
        busy: dict[str, object] = {}
        while True:
            connection, _ = listener.accept()
            if serving is not None and serving.is_alive():
                # Like the engine: one client at a time, and a newcomer learns who holds it.
                with connection:
                    connection.sendall(json.dumps(busy).encode() + b"\n")
                continue
            (holder_pid,) = struct.unpack("i", connection.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4))
            busy = {
                "ok": False,
                "error": "Engine is serving another client",
                "holder_pid": holder_pid,
                "held_since": int(time.time()),
            }
            serving = Thread(target=serve, args=(engine, connection), daemon=True)
            serving.start()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[3]))
