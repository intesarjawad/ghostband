import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from itertools import count
from pathlib import Path
from time import localtime, monotonic, sleep, strftime, time

import pytest
from click.testing import CliRunner
from ghostband import engine_process
from ghostband.cli import main
from ghostband.engine_client import EngineBusyError, EngineClient
from ghostband.engine_process import EngineProcess, describe_exit
from ghostband.errors import ErrorCode, GhostbandError
from ghostband.session import Session

from conductor.tests.engine_stand_in import executable, is_running, process_id

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/greensleeves.chart.json"
SIGNAL_TEST_TIMEOUT_S = 10.0
CLI_PROGRAM = "from ghostband.cli import main; main(prog_name='ghostband')"


@pytest.fixture
def socket_path() -> Iterator[Path]:
    # macOS limits Unix socket paths to 104 bytes, which pytest's tmp_path can exceed.
    with tempfile.TemporaryDirectory(prefix="ghostband-", dir="/private/tmp") as directory:
        yield Path(directory) / "engine.sock"


@pytest.fixture
def engine_environment(socket_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the conductor at a private socket and resources; the test picks the engine."""
    monkeypatch.setenv("GHOSTBAND_ENGINE_SOCKET", str(socket_path))
    monkeypatch.setenv("GHOSTBAND_RESOURCES", str(tmp_path / "resources"))
    return socket_path


def test_an_absent_engine_is_started_then_stopped_without_leftovers(
    socket_path: Path, tmp_path: Path
) -> None:
    client, process = engine_process.connect(executable(tmp_path, "serve"), socket_path)
    with client:
        assert client.request({"cmd": "hello"})["protocol"] == 1
    assert process is not None
    pid = process_id(socket_path)
    assert is_running(pid)
    process.stop()
    assert not is_running(pid)
    assert not socket_path.exists()


def test_an_engine_already_listening_is_reused_and_left_running(
    socket_path: Path, tmp_path: Path
) -> None:
    running = EngineProcess.start(executable(tmp_path, "serve"), socket_path)
    try:
        client, process = engine_process.connect(tmp_path / "never-started", socket_path)
        client.close()
        assert process is None
        assert is_running(running.process.pid)
    finally:
        running.stop()


def test_a_second_client_is_told_which_process_holds_the_engine(
    socket_path: Path, tmp_path: Path
) -> None:
    running = EngineProcess.start(executable(tmp_path, "serve"), socket_path)
    try:
        connected_at = int(time())
        with (
            EngineClient.connect(socket_path),
            pytest.raises(EngineBusyError) as refused,
        ):
            engine_process.connect(tmp_path / "never-started", socket_path)
        assert refused.value.holder_pid == os.getpid()
        assert refused.value.held_since_s >= connected_at
        assert is_running(running.process.pid)
    finally:
        running.stop()


def held_since_text(held_since_s: int) -> str:
    return strftime("%Y-%m-%d %H:%M", localtime(held_since_s))


def test_the_holder_is_named_by_its_command_line_without_paths() -> None:
    since = int(time())
    description = engine_process.describe_holder(EngineBusyError(os.getpid(), since))
    name, _, rest = description.partition(f" (process {os.getpid()}) ")
    assert "pytest" in name
    assert "/" not in name
    assert rest == f"has held it since {held_since_text(since)}"


def test_a_holder_that_is_gone_or_unknown_is_still_described() -> None:
    gone = subprocess.Popen([sys.executable, "-c", ""])
    gone.wait(timeout=SIGNAL_TEST_TIMEOUT_S)
    since = int(time())
    assert engine_process.describe_holder(EngineBusyError(gone.pid, since)) == (
        f"process {gone.pid} has held it since {held_since_text(since)}"
    )
    assert engine_process.describe_holder(EngineBusyError(None, since)) == (
        f"another program has held it since {held_since_text(since)}"
    )


def test_a_socket_left_by_a_killed_engine_is_replaced(socket_path: Path, tmp_path: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as abandoned:
        abandoned.bind(str(socket_path))
    client, process = engine_process.connect(executable(tmp_path, "serve"), socket_path)
    client.close()
    assert process is not None
    process.stop()


def test_an_engine_that_never_listens_is_killed_after_the_timeout(
    socket_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_process, "ENGINE_START_TIMEOUT_S", 0.5)
    with pytest.raises(TimeoutError, match=r"^engine did not open its socket within 0\.5 s$"):
        engine_process.connect(executable(tmp_path, "silent"), socket_path)
    assert not is_running(process_id(socket_path))


def test_an_engine_that_exits_at_start_reports_its_status(
    socket_path: Path, tmp_path: Path
) -> None:
    with pytest.raises(
        RuntimeError, match=r"^engine exited with status 1 before opening its socket$"
    ):
        EngineProcess.start(executable(tmp_path, "exit"), socket_path)


def test_a_missing_engine_is_named_before_anything_starts(
    socket_path: Path, tmp_path: Path
) -> None:
    missing = tmp_path / "ghostband-engine"
    with pytest.raises(FileNotFoundError, match=f"engine executable {missing} is missing"):
        engine_process.connect(missing, socket_path)


def test_a_signal_is_named_rather_than_its_negative_status() -> None:
    assert describe_exit(-11) == "engine stopped by SIGSEGV"
    assert describe_exit(70) == "engine exited with status 70"


def test_the_default_socket_matches_the_engine_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHOSTBAND_ENGINE_SOCKET", raising=False)
    monkeypatch.setenv("TMPDIR", "/var/folders/xy/T")
    assert engine_process.default_socket() == Path("/var/folders/xy/T/ghostband/engine.sock")
    monkeypatch.setenv("GHOSTBAND_ENGINE_SOCKET", "/private/tmp/elsewhere.sock")
    assert engine_process.default_socket() == Path("/private/tmp/elsewhere.sock")


def test_a_session_starts_the_engine_and_stops_it_on_close(
    engine_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(executable(tmp_path, "serve")))
    session = Session()
    session.connect()
    pid = process_id(engine_environment)
    assert session.health["online"] is True
    session.close()
    assert not is_running(pid)
    assert not engine_environment.exists()


def test_play_starts_the_engine_and_stops_it_at_the_end(
    engine_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(executable(tmp_path, "serve")))
    result = CliRunner().invoke(main, ["play", str(CHART), "--seconds", "0.2"])
    assert result.exit_code == 0, result.output
    assert "ghostband: Stopped" in result.stdout
    assert not is_running(process_id(engine_environment))
    assert not engine_environment.exists()


def test_play_names_how_the_engine_died_mid_song(
    engine_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(executable(tmp_path, "crash")))
    result = CliRunner().invoke(main, ["play", str(CHART), "--seconds", "5"])
    assert result.exit_code == ErrorCode.ENGINE_FAILED, result.output
    assert result.stderr == (
        "ghostband: Engine failed: engine stopped by SIGKILL\n"
        "  next: Run ghostband doctor, then try again; ghostband starts a new engine.\n"
    )
    assert not engine_environment.exists()


def test_play_names_the_program_holding_the_engine(
    engine_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(tmp_path / "never-started"))
    running = EngineProcess.start(executable(tmp_path, "serve"), engine_environment)
    try:
        with EngineClient.connect(engine_environment):
            result = CliRunner().invoke(main, ["play", str(CHART), "--seconds", "1"])
        assert result.exit_code == ErrorCode.ENGINE_BUSY, result.output
        message = " ".join(result.stderr.split())
        assert message.startswith("ghostband: Engine busy: ")
        assert f"(process {os.getpid()}) has held it since " in message
        assert message.endswith(
            "next: Press Ctrl+C where that program runs, then try again; the engine serves "
            "one program at a time."
        )
        assert is_running(running.process.pid)
    finally:
        running.stop()


def test_the_interface_is_told_which_program_holds_the_engine(
    engine_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(tmp_path / "never-started"))
    running = EngineProcess.start(executable(tmp_path, "serve"), engine_environment)
    session = Session()
    try:
        with EngineClient.connect(engine_environment), pytest.raises(GhostbandError) as refused:
            session.connect()
        event = refused.value.event()
        assert event["code"] == "engine_busy"
        assert f"(process {os.getpid()}) has held it since " in str(event["message"])
        assert session.health == {"online": False}
    finally:
        running.stop()


def start_play(*arguments: str) -> subprocess.Popen[str]:
    """Run `ghostband play` in its own process, so a test can signal it as a terminal would."""
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", CLI_PROGRAM, "play", str(CHART), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_until(ready: Callable[[], bool], play: subprocess.Popen[str]) -> None:
    deadline = monotonic() + SIGNAL_TEST_TIMEOUT_S
    while not ready():
        assert play.poll() is None, play.communicate()
        assert monotonic() < deadline, "play never reached the state the test signals in"
        sleep(0.01)


def finish(play: subprocess.Popen[str]) -> tuple[int, str, str]:
    try:
        stdout, stderr = play.communicate(timeout=SIGNAL_TEST_TIMEOUT_S)
    finally:
        play.kill()
    return play.returncode, stdout, stderr


def playing(play: subprocess.Popen[str]) -> Callable[[], bool]:
    assert play.stdout is not None
    stdout = play.stdout
    return lambda: bool(select.select([stdout], [], [], 0)[0]) and "Playing" in stdout.readline()


@pytest.mark.parametrize(
    ("number", "status"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)], ids=["SIGINT", "SIGTERM"]
)
def test_a_signal_during_play_stops_the_take_and_the_engine_it_started(
    engine_environment: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    number: int,
    status: int,
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(executable(tmp_path, "serve")))
    take = tmp_path / "take.wav"
    play = start_play("--record", str(take))
    wait_until(playing(play), play)
    play.send_signal(number)
    code, stdout, stderr = finish(play)
    assert (code, stderr) == (status, "")
    assert "ghostband: Stopped" in stdout
    assert take.is_file()
    assert take.with_suffix(".take.json").is_file()
    assert not is_running(process_id(engine_environment))
    assert not engine_environment.exists()


def test_a_signal_during_play_leaves_a_reused_engine_running(
    engine_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(tmp_path / "never-started"))
    running = EngineProcess.start(executable(tmp_path, "serve"), engine_environment)
    try:
        play = start_play()
        wait_until(playing(play), play)
        play.send_signal(signal.SIGINT)
        code, _, stderr = finish(play)
        assert (code, stderr) == (130, "")
        assert is_running(running.process.pid)
        assert engine_environment.is_socket()
    finally:
        running.stop()


@pytest.mark.parametrize(
    ("number", "status"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)], ids=["SIGINT", "SIGTERM"]
)
def test_a_signal_during_engine_start_up_stops_the_engine_silently(
    engine_environment: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    number: int,
    status: int,
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(executable(tmp_path, "silent")))
    play = start_play()
    pid_file = engine_environment.with_suffix(".pid")
    wait_until(lambda: pid_file.is_file() and pid_file.stat().st_size > 0, play)
    pid = process_id(engine_environment)
    play.send_signal(number)
    assert finish(play) == (status, "", "")
    assert not is_running(pid)


def test_the_engine_start_timeout_is_bounded(
    engine_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(executable(tmp_path, "silent")))
    # Each clock reading advances 1 s, so the 10 s budget runs out in ten polls.
    monkeypatch.setattr(engine_process, "monotonic", count(0.0, 1.0).__next__)
    result = CliRunner().invoke(main, ["play", str(CHART), "--seconds", "1"])
    assert result.exit_code == ErrorCode.ENGINE_OFFLINE, result.output
    assert "Engine offline: engine did not open its socket within 10 s" in result.stderr
    assert not is_running(process_id(engine_environment))
