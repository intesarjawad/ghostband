import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from ghostband.chart import Chart
from ghostband.engine_client import MAX_LINE_BYTES, EngineBusyError, EngineClient, EngineStatus
from ghostband.fake_engine import FakeEngine
from ghostband.scheduler import Scheduler
from ghostband.style import Slew

ROOT = Path(__file__).resolve().parents[2]
# A client that never connects fails the test here instead of leaving accept waiting forever.
ACCEPT_TIMEOUT_S = 5.0


def test_scheduler_and_slew_through_unix_socket_with_fake_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    engine = FakeEngine(lambda: now[0], latency_samples=4800)
    shutdown = Event()
    # A relative path keeps within the macOS socket length limit and is private to this test.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GHOSTBAND_ENGINE_SOCKET", "engine.sock")
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
        ThreadPoolExecutor() as executor,
    ):
        listener.bind("engine.sock")
        listener.listen(1)
        listener.settimeout(ACCEPT_TIMEOUT_S)

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                engine.serve(connection, shutdown)

        serving = executor.submit(serve)
        try:
            with EngineClient.connect() as client:
                chart = Chart.model_validate_json(
                    (ROOT / "charts/greensleeves.chart.json").read_text()
                )
                scheduler = Scheduler(
                    chart,
                    client.send,
                    lookahead_s=EngineStatus.from_message(engine.status()).lookahead_s(),
                )
                slew = Slew(client.send)
                slew.tick(0)
                slew.set_target(1)
                scheduler.play(0)
                for tick in range(601):
                    now[0] = tick / 1000
                    scheduler.step(now[0])
                    slew.tick(now[0])
                assert client.request({"cmd": "hello"})["protocol"] == 1
                assert engine.active_notes
                assert engine.weights[1] > 0.8
                statuses = [EngineStatus.from_message(event) for event in client.events]
                assert statuses
                assert all(status.latency_samples == 4800 for status in statuses)
                scheduler.stop(now[0])
                client.request({"cmd": "hello"})
                assert not engine.active_notes
                client.send({"cmd": "quit"})
            serving.result(timeout=2)
        finally:
            shutdown.set()


def test_receive_preserves_fragmented_and_batched_lines() -> None:
    client_socket, server = socket.socketpair()
    with client_socket, server:
        client = EngineClient(client_socket)
        server.sendall(b'{"event":"sta')
        with pytest.raises(TimeoutError):
            client.receive(timeout_s=0.001)
        server.sendall(b'tus","played_samples":48000,"latency_samples":9600}\n{"ok":true}\n')
        status = EngineStatus.from_message(client.receive())
        assert status.audible_time_s == 1
        assert status.lookahead_s() == pytest.approx(0.4)
        assert status.lookahead_s(0.123) == 0.123
        assert client.receive() == {"ok": True}


def test_client_surfaces_errors_disconnects_and_line_limits() -> None:
    client_socket, server = socket.socketpair()
    with client_socket, server:
        client = EngineClient(client_socket)
        server.sendall(b'{"ok":false,"error":"queue full"}\n')
        with pytest.raises(RuntimeError, match="queue full"):
            client.receive()
        with pytest.raises(ValueError, match="limit"):
            client.send({"cmd": "prompt", "text": "x" * MAX_LINE_BYTES})
        with ThreadPoolExecutor() as executor:
            sending = executor.submit(server.sendall, b"x" * (MAX_LINE_BYTES + 1))
            with pytest.raises(ValueError, match="limit"):
                client.receive()
            sending.result(timeout=2)
    client_socket, server = socket.socketpair()
    with client_socket, server:
        client = EngineClient(client_socket)
        server.sendall(b'{"ok":')
        server.close()
        with pytest.raises(ConnectionError, match="disconnected"):
            client.receive()


@pytest.mark.parametrize(
    "message",
    [
        {"event": "status", "played_samples": True, "latency_samples": 0},
        {"event": "tap", "played_samples": 0, "latency_samples": 0},
        {"event": "status", "played_samples": 0.5, "latency_samples": 0},
        {"event": "status", "played_samples": -1, "latency_samples": 0},
        {"event": "status", "played_samples": 0},
    ],
)
def test_status_rejects_invalid_sample_counts(message: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="sample"):
        EngineStatus.from_message(message)


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_measured_latency_must_be_finite_and_nonnegative(value: float) -> None:
    with pytest.raises(ValueError, match="latency"):
        EngineStatus(0, 0).lookahead_s(value)


def test_handshake_rejects_protocol_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "engine.sock"
    # Use a short relative path to respect the macOS socket limit.
    monkeypatch.chdir(tmp_path)
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
        ThreadPoolExecutor() as executor,
    ):
        listener.bind("engine.sock")
        listener.listen(1)
        listener.settimeout(ACCEPT_TIMEOUT_S)

        def reply() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                connection.sendall(b'{"event":"status"}\n{"ok":true,"protocol":2}\n')

        serving = executor.submit(reply)
        with pytest.raises(ValueError, match="version 1"):
            EngineClient.connect(Path(path.name))
        serving.result(timeout=2)


def test_a_busy_reply_is_read_after_the_engine_hung_up_before_hello(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    hung_up = Event()
    send = EngineClient.send

    def send_after_hang_up(client: EngineClient, command: dict[str, object]) -> None:
        assert hung_up.wait(2)
        send(client, command)

    monkeypatch.setattr(EngineClient, "send", send_after_hang_up)
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
        ThreadPoolExecutor() as executor,
    ):
        listener.bind("engine.sock")
        listener.listen(1)
        listener.settimeout(ACCEPT_TIMEOUT_S)

        def refuse() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.sendall(b'{"ok":false,"holder_pid":4242,"held_since":1762700000}\n')
            hung_up.set()

        refusing = executor.submit(refuse)
        with pytest.raises(EngineBusyError) as busy:
            EngineClient.connect(Path("engine.sock"))
        assert (busy.value.holder_pid, busy.value.held_since_s) == (4242, 1762700000)
        refusing.result(timeout=2)


def test_request_timeout_includes_pushed_events(monkeypatch: pytest.MonkeyPatch) -> None:
    client_socket, server = socket.socketpair()
    now = [0.0]
    monkeypatch.setattr("ghostband.engine_client.monotonic", lambda: now[0])
    with client_socket, server:
        client = EngineClient(client_socket)
        server.sendall(b'{"event":"status"}\n' * 10)

        def advance_clock() -> float:
            now[0] += 0.1
            return now[0]

        monkeypatch.setattr("ghostband.engine_client.monotonic", advance_clock)
        with pytest.raises(TimeoutError, match="reply"):
            client.request({"cmd": "hello"}, timeout_s=0.5)
        assert client.events
        assert len(client.events) < 10


def test_oversized_message_with_newline_is_rejected() -> None:
    client_socket, server = socket.socketpair()
    with client_socket, server, ThreadPoolExecutor() as executor:
        client = EngineClient(client_socket)
        sending = executor.submit(server.sendall, b"x" * MAX_LINE_BYTES + b"\n")
        with pytest.raises(ValueError, match="limit"):
            client.receive()
        sending.result(timeout=2)


def test_missing_engine_address_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHOSTBAND_ENGINE_SOCKET", raising=False)
    with pytest.raises(ValueError, match="GHOSTBAND_ENGINE_SOCKET"):
        EngineClient.connect()
