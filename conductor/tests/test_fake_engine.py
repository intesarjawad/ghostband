import select
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from ghostband.engine_client import Command, EngineClient
from ghostband.fake_engine import FakeEngine


def test_fake_engine_samples_stop_and_restart_use_only_fixed_clock() -> None:
    now = [10.0]
    engine = FakeEngine(lambda: now[0], latency_samples=9600)
    engine.send({"cmd": "play"})
    engine.send({"cmd": "noteon", "pitch": 60})
    now[0] += 0.125
    assert engine.status()["played_samples"] == 6000
    engine.send({"cmd": "stop"})
    now[0] += 1
    assert engine.status()["played_samples"] == 6000
    assert not engine.active_notes
    engine.send({"cmd": "play"})
    assert engine.status()["played_samples"] == 0
    assert engine.commands[0].time_s == 10
    assert engine.commands[-1].time_s == now[0]


@pytest.mark.parametrize(
    "command",
    [
        {"cmd": "noteon", "pitch": -1},
        {"cmd": "noteoff", "pitch": 128},
        {"cmd": "noteon", "pitch": True},
        {"cmd": "noteon"},
        {"cmd": "blend", "weights": [1, 0]},
        {"cmd": "blend", "weights": [-1, 2, 0, 0, 0, 0]},
        {"cmd": "blend", "weights": [float("nan"), 1, 0, 0, 0, 0]},
        {"cmd": "blend", "weights": [True, 0, 0, 0, 0, 0]},
        {"cmd": "load"},
    ],
)
def test_fake_engine_rejects_invalid_or_unimplemented_commands(command: Command) -> None:
    engine = FakeEngine(lambda: 0)
    with pytest.raises(ValueError, match=r"pitch|weights|Unsupported"):
        engine.send(command)
    assert not engine.commands


def test_a_client_that_stops_reading_events_can_still_quit() -> None:
    now = [0.0]
    engine = FakeEngine(lambda: now[0])
    client_socket, server = socket.socketpair()
    # The sockets close before the server thread is joined, so a send it blocks in ends.
    with ThreadPoolExecutor() as executor, client_socket, server:
        serving = executor.submit(engine.serve, server, Event())
        client = EngineClient(client_socket)
        client.send({"cmd": "play"})
        client.send({"cmd": "tap", "on": True})
        client.request({"cmd": "hello"})
        # Two seconds of tap audio is far more than a socket buffers while nobody reads it.
        now[0] = 2.0
        assert select.select([client_socket], [], [], 2)[0]
        client.send({"cmd": "quit"})
        serving.result(timeout=2)


def test_socket_errors_and_disconnect_cannot_leave_a_note_on() -> None:
    engine = FakeEngine(lambda: 0)
    shutdown = Event()
    client_socket, server = socket.socketpair()
    with client_socket, server, ThreadPoolExecutor() as executor:
        serving = executor.submit(engine.serve, server, shutdown)
        client = EngineClient(client_socket)
        client.send({"cmd": "play"})
        client.send({"cmd": "noteon", "pitch": 60})
        client.request({"cmd": "hello"})
        assert engine.active_notes == {60}
        with pytest.raises(RuntimeError, match="Unsupported"):
            client.request({"cmd": "not-a-command"})
        client.close()
        serving.result(timeout=2)
    assert not engine.active_notes
    assert not engine.playing
