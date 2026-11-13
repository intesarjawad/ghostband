"""Bounded JSON-lines transport for the local engine protocol."""

import json
import os
import select
import socket
from collections import deque
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Self

from pydantic import TypeAdapter

Command = dict[str, object]
SAMPLE_RATE = 48_000
CONTROL_FRAME_S = 0.040
BUFFER_FRAMES = 5
MAX_LINE_BYTES = 65_536
_MESSAGE = TypeAdapter(dict[str, object])


def encode(message: Command) -> bytes:
    """One protocol line, refusing a message over the line limit."""
    line = json.dumps(message, allow_nan=False).encode() + b"\n"
    if len(line) > MAX_LINE_BYTES:
        raise ValueError("Engine command exceeds the JSON line limit")
    return line


@dataclass(frozen=True)
class EngineStatus:
    """Audio callback position and latency, independent of the wall clock."""

    played_samples: int
    latency_samples: int

    def __post_init__(self) -> None:
        if self.played_samples < 0 or self.latency_samples < 0:
            raise ValueError("Engine sample counts must be nonnegative")

    @classmethod
    def from_message(cls, message: Command) -> Self:
        """Reject malformed status messages at the socket boundary."""
        played = message.get("played_samples")
        latency = message.get("latency_samples")
        if message.get("event") != "status" or type(played) is not int or type(latency) is not int:
            raise ValueError("Engine status requires integer played_samples and latency_samples")
        return cls(played, latency)

    @property
    def audible_time_s(self) -> float:
        """Audio callback position in seconds since play."""
        return self.played_samples / SAMPLE_RATE

    def lookahead_s(self, measured_p95_s: float | None = None) -> float:
        """Prefer a measured control-to-audible delay over the buffer estimate."""
        if measured_p95_s is not None:
            if not isfinite(measured_p95_s) or measured_p95_s < 0:
                raise ValueError("Measured latency must be finite and nonnegative")
            return measured_p95_s
        return self.latency_samples / SAMPLE_RATE + BUFFER_FRAMES * CONTROL_FRAME_S


class EngineBusyError(RuntimeError):
    """The engine serves one client at a time, and another process holds it."""

    def __init__(self, holder_pid: int | None, held_since_s: int) -> None:
        super().__init__("Engine is serving another client")
        self.holder_pid = holder_pid
        self.held_since_s = held_since_s


class EngineClient:
    """One synchronous socket client; asynchronous events survive reply requests."""

    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self._buffer = bytearray()
        self.events: deque[Command] = deque()

    @classmethod
    def connect(cls, path: Path | None = None, *, timeout_s: float = 2.0) -> Self:
        """Connect to a path or GHOSTBAND_ENGINE_SOCKET; require protocol version 1."""
        address = str(path) if path is not None else os.environ.get("GHOSTBAND_ENGINE_SOCKET")
        if address is None:
            raise ValueError("Supply a socket path or set GHOSTBAND_ENGINE_SOCKET")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(timeout_s)
        client = cls(connection)
        try:
            connection.connect(address)
            try:
                reply = client.request({"cmd": "hello"}, timeout_s=timeout_s)
            except BrokenPipeError:
                # A busy engine answers before reading and closes; the answer is still readable.
                client.receive(timeout_s)
                raise
            if reply.get("protocol") != 1:
                raise ValueError("Engine protocol must be version 1")
        except BaseException:
            connection.close()
            raise
        return client

    def send(self, command: Command) -> None:
        """Send one command without waiting for a fire-and-forget acknowledgement."""
        self.connection.sendall(encode(command))

    def receive(self, timeout_s: float = 2.0) -> Command:
        """Read one complete message, preserving fragments and following messages."""
        deadline_s = monotonic() + timeout_s
        while b"\n" not in self._buffer:
            remaining_s = max(0.0, deadline_s - monotonic())
            if not select.select([self.connection], [], [], remaining_s)[0]:
                raise TimeoutError("Timed out waiting for an engine message")
            chunk = self.connection.recv(MAX_LINE_BYTES + 1 - len(self._buffer))
            if not chunk:
                raise ConnectionError("Engine disconnected before the next message")
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_LINE_BYTES and b"\n" not in self._buffer:
                raise ValueError("Engine message exceeds the JSON line limit")
        line, _, remainder = self._buffer.partition(b"\n")
        if len(line) + 1 > MAX_LINE_BYTES:
            raise ValueError("Engine message exceeds the JSON line limit")
        self._buffer[:] = remainder
        message = _MESSAGE.validate_json(line)
        if message.get("ok") is False:
            holder_pid, held_since_s = message.get("holder_pid"), message.get("held_since")
            if (holder_pid is None or type(holder_pid) is int) and type(held_since_s) is int:
                raise EngineBusyError(holder_pid, held_since_s)
            raise RuntimeError(f"Engine rejected command: {message.get('error', 'unknown error')}")
        return message

    def request(self, command: Command, *, timeout_s: float = 2.0) -> Command:
        """Wait for an acknowledgement while retaining pushed events for the caller."""
        deadline_s = monotonic() + timeout_s
        self.send(command)
        while True:
            remaining_s = deadline_s - monotonic()
            if remaining_s <= 0:
                raise TimeoutError("Timed out waiting for an engine reply")
            message = self.receive(remaining_s)
            if "event" in message:
                self.events.append(message)
            elif message.get("ok") is True:
                return message
            else:
                raise ValueError("Engine reply requires an ok flag or event name")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
