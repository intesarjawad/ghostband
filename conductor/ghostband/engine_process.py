"""Start the local engine on demand, or reuse one already listening on its socket."""

import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Self

from ghostband.engine_client import EngineBusyError, EngineClient

ENGINE_START_TIMEOUT_S = 10.0
ENGINE_STOP_TIMEOUT_S = 10.0
SOCKET_POLL_S = 0.05
# A crashed engine closes its socket a moment before the kernel reports its exit.
EXIT_REPORT_WAIT_S = 0.5
HOLDER_LOOKUP_TIMEOUT_S = 2.0


def default_socket() -> Path:
    """GHOSTBAND_ENGINE_SOCKET, else the path the engine itself listens on by default."""
    configured = os.environ.get("GHOSTBAND_ENGINE_SOCKET")
    if configured:
        return Path(configured)
    # Mirrors the engine's default, so an engine started by hand without --socket is reused.
    temporary = os.environ.get("TMPDIR") or "/tmp"  # noqa: S108
    return Path(temporary) / "ghostband" / "engine.sock"


def describe_exit(status: int) -> str:
    if status < 0:
        return f"engine stopped by {signal.Signals(-status).name}"
    return f"engine exited with status {status}"


def describe_holder(busy: EngineBusyError) -> str:
    """Name the process holding the engine, as ps shows its command line, and since when."""
    since = datetime.fromtimestamp(busy.held_since_s, UTC).astimezone()
    held = f"has held it since {since:%Y-%m-%d %H:%M}"
    if busy.holder_pid is None:
        return f"another program {held}"
    try:
        command = subprocess.run(  # noqa: S603
            ["/bin/ps", "-o", "command=", "-p", str(busy.holder_pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=HOLDER_LOOKUP_TIMEOUT_S,
        ).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        command = []
    # Full paths bury the program's name; the process id still identifies it exactly.
    name = " ".join(Path(word).name for word in command)
    process = f"process {busy.holder_pid}"
    return f"{name} ({process}) {held}" if name else f"{process} {held}"


class EngineProcess:
    """An engine this conductor started; stopping it leaves no process and no socket file."""

    def __init__(self, process: subprocess.Popen[bytes], socket_path: Path) -> None:
        self.process = process
        self.socket_path = socket_path

    @classmethod
    def start(cls, engine: Path, socket_path: Path) -> Self:
        """Launch the engine on socket_path and wait until it listens there."""
        if not engine.is_file():
            raise FileNotFoundError(f"engine executable {engine} is missing")
        # Its own process group keeps Ctrl+C at the conductor, which still has to stop a
        # recording through the engine before stopping the engine itself.
        process = subprocess.Popen(  # noqa: S603
            [str(engine), "--socket", str(socket_path)], process_group=0
        )
        started = cls(process, socket_path)
        try:
            deadline = monotonic() + ENGINE_START_TIMEOUT_S
            while not socket_path.is_socket():
                if process.poll() is not None:
                    raise RuntimeError(
                        f"{describe_exit(process.returncode)} before opening its socket"
                    )
                if monotonic() >= deadline:
                    raise TimeoutError(
                        f"engine did not open its socket within {ENGINE_START_TIMEOUT_S:g} s"
                    )
                sleep(SOCKET_POLL_S)
        except BaseException:
            started.stop()
            raise
        return started

    def exit_reason(self) -> str | None:
        """How the engine ended, or None while it is still running."""
        try:
            status = self.process.wait(timeout=EXIT_REPORT_WAIT_S)
        except subprocess.TimeoutExpired:
            return None
        return describe_exit(status)

    def stop(self) -> None:
        # The engine's own process group never sees Ctrl+C, so a second one pressed while it
        # stops is held until it is gone rather than leaving it running.
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=ENGINE_STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            # A signalled engine skips its cleanup; the leftover file would refuse the next bind.
            self.socket_path.unlink(missing_ok=True)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)


def connect(engine: Path, socket_path: Path) -> tuple[EngineClient, EngineProcess | None]:
    """Reuse the engine listening on socket_path, or start one there; the process is ours."""
    try:
        return EngineClient.connect(socket_path), None
    except (FileNotFoundError, ConnectionRefusedError):
        if socket_path.is_socket():
            # Nothing listens on it: an engine was killed before it could remove the file.
            socket_path.unlink()
    process = EngineProcess.start(engine, socket_path)
    try:
        return EngineClient.connect(socket_path), process
    except BaseException:
        process.stop()
        raise
