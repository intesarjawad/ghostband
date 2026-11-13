#!/usr/bin/env bash
# Prove a session never leaves the machine: import, play with slider, loop and
# record, then export, while sampling every socket of this check and the
# ghostband server it starts.
set -euo pipefail

cd "$(dirname "$0")/.."
command -v lsof >/dev/null || { echo 'check_offline.sh needs lsof.' >&2; exit 1; }

exec uv run --frozen python - "$@" <<'PY'
import argparse
import asyncio
import errno
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import aiohttp

ROOT = Path.cwd()
PORT = 7777
LOOPBACK = {"127.0.0.1", "::1"}
SAMPLE_S = 0.2
STEP_S = 5
# TEST-NET-1 is reserved for documentation, so a stray probe reaches no real host.
PROBE_ADDRESS = ("192.0.2.1", 9)
SIGNATURES = {"wav": b"RIFF", "midi": b"MThd", "chart": b"{"}


class Violation(RuntimeError):
    pass


def foreign_endpoints(name: str) -> list[str]:
    """Return the endpoints of an lsof name, such as a->b, that are not loopback."""
    return [
        endpoint
        for endpoint in name.split("->")
        if endpoint.rsplit(":", 1)[0].strip("[]") not in LOOPBACK
    ]


def descendants(root: int) -> list[int]:
    listing = subprocess.run(
        ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True
    ).stdout
    children: dict[int, list[int]] = defaultdict(list)
    for line in listing.splitlines():
        pid, parent = (int(field) for field in line.split())
        children[parent].append(pid)
    found = [root]
    for pid in found:
        found.extend(children[pid])
    return found


def sockets(pids: list[int]) -> list[str]:
    # lsof exits 1 when the processes hold no internet sockets, so the status is not an error.
    output = subprocess.run(
        ["lsof", "-i", "-n", "-P", "-a", "-p", ",".join(map(str, pids)), "-F", "n"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [line[1:] for line in output.splitlines() if line.startswith("n")]


class Monitor(threading.Thread):
    """Sample the internet sockets of this process and everything it starts."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.done = threading.Event()
        self.samples = 0
        self.seen: set[str] = set()
        self.violations: set[str] = set()
        self.error: str | None = None

    def run(self) -> None:
        while not self.done.is_set():
            started = time.monotonic()
            try:
                names = sockets(descendants(os.getpid()))
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                self.error = f"cannot sample sockets: {error}"
                return
            self.samples += 1
            for name in names:
                self.seen.add(name)
                self.violations.update(foreign_endpoints(name))
            self.done.wait(max(0.0, SAMPLE_S - (time.monotonic() - started)))


def deliberate_connection() -> socket.socket:
    """Open one outbound connection so the check has something to catch."""
    probe = socket.socket()
    probe.setblocking(False)
    if probe.connect_ex(PROBE_ADDRESS) != errno.EINPROGRESS:
        # With no route out the connect fails at once, so listen on every interface instead.
        probe.close()
        probe = socket.socket()
        probe.bind(("", 0))
        probe.listen()
    return probe


def start_server(log: Path) -> subprocess.Popen[bytes]:
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError(f"port {PORT} is already in use; stop that server first")
    binary = Path(sys.executable).with_name("ghostband")
    with log.open("wb") as output:
        return subprocess.Popen(
            [str(binary), "--fake-engine"],
            stdout=output,
            stderr=subprocess.STDOUT,
            env={**os.environ, "COLUMNS": "1000"},
        )


def read_token(log: Path, server: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        found = re.search(r"\?t=(\S+)", log.read_text(errors="replace"))
        if found:
            with socket.socket() as probe:
                probe.settimeout(0.1)
                if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                    return found.group(1)
        if server.poll() is not None:
            raise RuntimeError("ghostband exited before it began listening")
        time.sleep(0.1)
    raise TimeoutError("ghostband did not print its address within 60 s")


def stop_server(server: subprocess.Popen[bytes]) -> None:
    if server.poll() is None:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def guard(monitor: Monitor, server: subprocess.Popen[bytes]) -> None:
    if monitor.violations:
        raise Violation(", ".join(sorted(monitor.violations)))
    if monitor.error:
        raise RuntimeError(monitor.error)
    if server.poll() is not None:
        raise RuntimeError(f"ghostband exited with status {server.returncode}")


async def pump(
    socket_: aiohttp.ClientWebSocketResponse, timeout_s: float
) -> dict[str, object] | None:
    try:
        message = await socket_.receive(timeout=timeout_s)
    except TimeoutError:
        return None
    if message.type != aiohttp.WSMsgType.TEXT:
        raise RuntimeError(f"WebSocket ended: {message.type.name}")
    event = json.loads(message.data)
    if event.get("event") == "error":
        raise RuntimeError(f"ghostband reported {event.get('code')}: {event.get('message')}")
    return event


async def until(
    socket_: aiohttp.ClientWebSocketResponse, monitor: Monitor, server: subprocess.Popen[bytes], **want: object
) -> dict[str, object]:
    async with asyncio.timeout(60):
        while True:
            await asyncio.to_thread(guard, monitor, server)
            event = await pump(socket_, 0.25)
            if event is not None and all(event.get(key) == value for key, value in want.items()):
                return event


async def observe(
    socket_: aiohttp.ClientWebSocketResponse, seconds: float, monitor: Monitor, server: subprocess.Popen[bytes]
) -> int:
    await until(socket_, monitor, server, event="clock")
    clock_events = 1
    started = time.monotonic()
    step = 0
    while (remaining := started + seconds - time.monotonic()) > 0:
        if time.monotonic() >= started + step * STEP_S:
            await socket_.send_json({"type": "slider", "value": 0.8 if step % 2 == 0 else 0.2})
            await socket_.send_json(
                {"type": "loop", "start": 0, "end": 8 if step % 2 == 0 else 4}
            )
            step += 1
        await asyncio.to_thread(guard, monitor, server)
        event = await pump(socket_, min(0.25, remaining))
        if event is not None and event.get("event") == "clock":
            clock_events += 1
    if clock_events < 2:
        raise RuntimeError("no clock events arrived during playback observation")
    return step


async def drive(token: str, seconds: float, monitor: Monitor, server: subprocess.Popen[bytes]) -> int:
    base = f"http://127.0.0.1:{PORT}"
    async with aiohttp.ClientSession(headers={"X-Ghostband-Token": token}) as http:
        form = aiohttp.FormData()
        form.add_field(
            "file", (ROOT / "fixtures/greensleeves.mid").read_bytes(), filename="greensleeves.mid"
        )
        async with http.post(f"{base}/api/upload", data=form) as response:
            response.raise_for_status()
            upload_id = (await response.json())["upload_id"]
        async with http.ws_connect(f"{base}/ws", protocols=[f"ghostband.{token}"]) as socket_:

            async def command(message: dict[str, object], **want: object) -> dict[str, object]:
                await socket_.send_json(message)
                return await until(socket_, monitor, server, event="state", **want)

            await until(socket_, monitor, server, event="state")
            await command({"type": "import", "upload_id": upload_id})
            await command({"type": "preset", "value": 0})
            await command({"type": "record", "action": "start"}, recording=True)
            await command({"type": "transport", "action": "play"}, transport="playing")

            step = await observe(socket_, seconds, monitor, server)
            await command({"type": "loop"})
            await command(
                {"type": "transport", "action": "stop"}, transport="stopped", recording=False
            )
        for kind, signature in SIGNATURES.items():
            async with http.get(f"{base}/api/export/{kind}") as response:
                response.raise_for_status()
                if not (await response.read()).startswith(signature):
                    raise RuntimeError(f"export {kind} is not a {kind} file")
    return step


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="check_offline.sh",
        description="Fail if a ghostband session touches any address but 127.0.0.1 or ::1.",
    )
    parser.add_argument("--self-test", action="store_true", help="open an outbound connection; must fail")
    parser.add_argument("--seconds", type=float, default=60, help="how long to play")
    parser.add_argument("--output", type=Path, help="report path; --self-test writes none without it")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    output = args.output or (None if args.self_test else ROOT / "conductor/ghostband/offline_check.json")

    monitor = Monitor()
    monitor.start()
    server: subprocess.Popen[bytes] | None = None
    probe: socket.socket | None = None
    error: str | None = None
    log_text = ""
    with tempfile.TemporaryDirectory(prefix="ghostband-offline-") as directory:
        log = Path(directory) / "server.log"
        try:
            server = start_server(log)
            token = read_token(log, server)
            if args.self_test:
                probe = deliberate_connection()
            asyncio.run(drive(token, args.seconds, monitor, server))
        except Violation:
            pass
        except Exception as failure:  # noqa: BLE001 - every failure must reach the report
            error = f"{type(failure).__name__}: {failure}"
        finally:
            if probe is not None:
                probe.close()
            if server is not None:
                stop_server(server)
            time.sleep(SAMPLE_S)
            monitor.done.set()
            monitor.join()
            if log.is_file():
                log_text = log.read_text(errors="replace")

    listening = f"127.0.0.1:{PORT}" in monitor.seen
    if error is None and not monitor.violations and not listening:
        error = f"never saw the server listening on 127.0.0.1:{PORT}; the sampler is blind"
    passed = error is None and not monitor.violations
    report = {
        "version": version("ghostband"),
        "result": "pass" if passed else "fail",
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seconds": args.seconds,
        "samples": monitor.samples,
        "addresses": sorted(monitor.seen),
        "violations": sorted(monitor.violations),
        "error": error,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if passed:
        print(f"PASS: {monitor.samples} samples, only loopback addresses: {', '.join(report['addresses'])}")
        return 0
    for violation in report["violations"]:
        print(f"FAIL: non-loopback address {violation}", file=sys.stderr)
    if error is not None:
        print(f"FAIL: {error}", file=sys.stderr)
        print(log_text[-2000:], file=sys.stderr)
    return 1


sys.exit(main())
PY
