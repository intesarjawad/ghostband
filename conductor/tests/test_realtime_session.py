from __future__ import annotations

import io
import json
import select
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.realtime_session import Json, SessionClient, exercise, reference_wav


def fake_session(connection: socket.socket) -> list[dict[str, Json]]:
    commands: list[dict[str, Json]] = []
    buffer = bytearray()
    playing = False
    sample = 0
    while True:
        if select.select([connection], [], [], 0.002)[0]:
            chunk = connection.recv(65536)
            if not chunk:
                return commands
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, remainder = buffer.partition(b"\n")
                buffer[:] = remainder
                command: dict[str, Json] = json.loads(line)
                commands.append(command)
                name = command["cmd"]
                if name in ("hello", "load", "ref_load"):
                    connection.sendall(b'{"ok":true,"protocol":1}\n')
                elif name == "play":
                    playing = True
                elif name == "stop":
                    playing = False
                elif name == "quit":
                    return commands
        if playing:
            status = {
                "event": "status",
                "played_samples": sample,
                "prompt_status": [2, 2, 0, 0, 0, 0],
                "underruns": 27,
                "tap_dropped": 0,
            }
            connection.sendall((json.dumps(status) + "\n").encode())
            connection.sendall(b'{"event":"tap","sample":0,"pcm":"AAAA"}\n')
            sample += 48000


def test_live_driver_exercises_controls_and_records_underruns() -> None:
    trace = io.StringIO()
    client, server = socket.socketpair()
    with client, server, ThreadPoolExecutor() as executor:
        client.settimeout(2)
        server.settimeout(2)
        simulation = executor.submit(fake_session, server)
        result = exercise(SessionClient(client, trace), Path("/resources"), Path("/ref.wav"), 6)
        commands = simulation.result(timeout=2)
    assert result.played_samples >= 6 * 48000
    assert result.underruns == 27
    assert result.tap_events > 0
    names = [command["cmd"] for command in commands]
    assert names.count("blend") == 2
    assert names.count("noteoff") == 6
    assert names.count("ref_play") == 2
    assert names.count("ref_mix") == 2
    assert {"cmd": "tap", "on": True} in commands
    assert names[-3:] == ["stop", "hello", "quit"]
    rows: list[dict[str, Json]] = [json.loads(line) for line in trace.getvalue().splitlines()]
    assert rows[-1]["kind"] == "summary"
    assert any(row.get("underrun_delta") == 27 for row in rows)
    assert any("status_gap_s" in row for row in rows)
    assert all("elapsed_s" in row and "command_sequence" in row for row in rows)
    assert '"pcm"' not in trace.getvalue()


def test_trace_attributes_status_to_latest_command_with_elapsed_time() -> None:
    now = [10.0]
    trace = io.StringIO()
    client, server = socket.socketpair()
    with client, server:
        session = SessionClient(client, trace, lambda: now[0])
        session.send({"cmd": "play"}, "setup")
        assert server.recv(4096) == b'{"cmd": "play"}\n'
        now[0] = 10.25
        session.observe({"event": "status", "underruns": 0, "tap_dropped": 0})
        now[0] = 11.0
        session.send({"cmd": "blend", "weights": [1, 0, 0, 0, 0, 0]}, "slider")
        now[0] = 11.5
        session.observe({"event": "status", "underruns": 2, "tap_dropped": 0})
    rows: list[dict[str, Json]] = [json.loads(line) for line in trace.getvalue().splitlines()]
    assert rows[-1]["command_sequence"] == 2
    assert rows[-1]["elapsed_s"] == 1.5
    assert rows[-1]["since_command_s"] == 0.5
    assert rows[-1]["status_gap_s"] == 1.25
    assert rows[-1]["underrun_delta"] == 2


def test_receive_preserves_fragmented_events_and_surfaces_errors() -> None:
    client, server = socket.socketpair()
    with client, server:
        session = SessionClient(client, io.StringIO())
        server.sendall(b'{"ok":tr')
        assert session.receive(0.1) == []
        server.sendall(b'ue}\n{"ok":false,"error":"queue full"}\n')
        with pytest.raises(RuntimeError, match="queue full"):
            session.receive(0.1)


def test_generated_reference_has_float_stereo_samples(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    reference_wav(reference)
    data = reference.read_bytes()
    assert struct.unpack_from("<HHI", data, 20) == (3, 2, 48000)
    assert len(data) == 44 + 96000 * 8
    samples = list(struct.iter_unpack("<ff", data[44:]))
    peak_float32 = struct.unpack("<f", struct.pack("<f", 0.05))[0]
    assert all(left == right and abs(left) <= peak_float32 for left, right in samples)
    assert any(left > 0.04 for left, _ in samples)
