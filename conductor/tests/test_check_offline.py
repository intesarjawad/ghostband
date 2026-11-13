import asyncio
import json
import runpy
import shutil
import socket
import subprocess
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from ghostband.server import PORT

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_offline.sh"


def run_check(*arguments: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("lsof") is None or shutil.which("uv") is None:
        pytest.skip("the offline check needs lsof and uv")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            pytest.skip(f"port {PORT} is in use")
    return subprocess.run(  # noqa: S603
        [str(SCRIPT), "--seconds", "3", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_session_stays_on_loopback_and_reports(tmp_path: Path) -> None:
    report = tmp_path / "offline_check.json"
    finished = run_check("--output", str(report))
    assert finished.returncode == 0, finished.stderr
    written = json.loads(report.read_text())
    assert written["result"] == "pass"
    assert (
        written["version"]
        == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    )
    assert written["violations"] == []
    assert f"127.0.0.1:{PORT}" in written["addresses"]


def test_outbound_connection_fails_the_check(tmp_path: Path) -> None:
    report = tmp_path / "offline_check.json"
    finished = run_check("--self-test", "--output", str(report))
    assert finished.returncode == 1
    assert "non-loopback address" in finished.stderr
    written = json.loads(report.read_text())
    assert written["result"] == "fail"
    assert written["violations"] != []


def test_playback_observation_starts_after_the_first_clock_under_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = SCRIPT.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    module = tmp_path / "offline.py"
    module.write_text(source.rsplit("sys.exit(main())", 1)[0])
    observe = runpy.run_path(str(module))["observe"]
    clock_at: list[float] = []
    sent_at: list[float] = []
    guard_started = threading.Event()
    heartbeat_ran = threading.Event()

    async def first_clock(*_args: object, **want: object) -> dict[str, str]:
        assert want == {"event": "clock"}
        await asyncio.sleep(0.06)
        clock_at.append(time.monotonic())
        return {"event": "clock"}

    def slow_guard(*_args: object) -> None:
        guard_started.set()
        assert heartbeat_ran.wait(timeout=1)

    async def clock(*_args: object) -> dict[str, str]:
        return {"event": "clock"}

    class Socket:
        async def send_json(self, _message: dict[str, object]) -> None:
            sent_at.append(time.monotonic())

    async def exercise() -> None:
        async def heartbeat() -> None:
            while not guard_started.is_set():
                await asyncio.sleep(0)
            heartbeat_ran.set()

        pulse = asyncio.create_task(heartbeat())
        assert await observe(Socket(), 0.05, None, None) == 1
        await pulse

    monkeypatch.setitem(observe.__globals__, "until", first_clock)
    monkeypatch.setitem(observe.__globals__, "guard", slow_guard)
    monkeypatch.setitem(observe.__globals__, "pump", clock)
    asyncio.run(exercise())
    assert sent_at[0] >= clock_at[0]
    assert time.monotonic() - clock_at[0] >= 0.05
    assert heartbeat_ran.is_set()


def test_printed_address_is_not_ready_until_the_socket_listens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = SCRIPT.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    module = tmp_path / "offline.py"
    module.write_text(source.rsplit("sys.exit(main())", 1)[0])
    read_token = runpy.run_path(str(module))["read_token"]
    polled = threading.Event()

    class Server:
        def poll(self) -> None:
            polled.set()

    log = tmp_path / "server.log"
    log.write_text("http://127.0.0.1/?t=local-token\n")
    with socket.socket() as listener, ThreadPoolExecutor() as executor:
        listener.bind(("127.0.0.1", 0))
        monkeypatch.setitem(read_token.__globals__, "PORT", listener.getsockname()[1])
        pending = executor.submit(read_token, log, Server())
        try:
            assert polled.wait(timeout=1)
            assert not pending.done()
        finally:
            listener.listen()
        assert pending.result(timeout=2) == "local-token"
