"""Exercise the live socket path and timestamp control, status and tap traffic."""

from __future__ import annotations

import argparse
import json
import math
import select
import socket
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None

SAMPLE_RATE_HZ = 48000
TOGGLE_SECONDS = 5
CHORD = (60, 64, 67)


def reference_wav(path: Path) -> None:
    """Make a quiet, two-second stereo float32 reference without model assets."""
    frames = SAMPLE_RATE_HZ * 2
    audio = b"".join(
        struct.pack("<ff", sample, sample)
        for index in range(frames)
        for sample in [0.05 * math.sin(2 * math.pi * 220 * index / SAMPLE_RATE_HZ)]
    )
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(audio),
        b"WAVE",
        b"fmt ",
        16,
        3,
        2,
        SAMPLE_RATE_HZ,
        SAMPLE_RATE_HZ * 8,
        8,
        32,
        b"data",
        len(audio),
    )
    path.write_bytes(header + audio)


class SessionClient:
    def __init__(
        self,
        connection: socket.socket,
        trace: TextIO,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.connection = connection
        self.trace = trace
        self.clock = clock
        self.started_s = clock()
        self.command_sequence = 0
        self.command_sent_s = self.started_s
        self.buffer = b""
        self.latest_status: dict[str, Json] | None = None
        self.status_count = 0
        self.tap_count = 0
        self.previous_status_s: float | None = None
        self.max_status_gap_s = 0.0
        self.max_underruns = 0
        self.max_tap_dropped = 0

    def log(self, kind: str, payload: dict[str, Json]) -> None:
        self.trace.write(
            json.dumps(
                {
                    "elapsed_s": self.clock() - self.started_s,
                    "kind": kind,
                    "command_sequence": self.command_sequence,
                    **payload,
                }
            )
            + "\n"
        )
        self.trace.flush()

    def send(self, command: dict[str, Json], phase: str) -> None:
        self.command_sequence += 1
        self.command_sent_s = self.clock()
        self.log("command", {"phase": phase, "payload": command})
        self.connection.sendall((json.dumps(command) + "\n").encode())
        self.log("sent", {})

    def receive(self, timeout_s: float) -> list[dict[str, Json]]:
        if not select.select([self.connection], [], [], max(0, timeout_s))[0]:
            return []
        chunk = self.connection.recv(65536)
        if not chunk:
            raise RuntimeError("Engine closed the socket before the session finished")
        self.buffer += chunk
        events: list[dict[str, Json]] = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            value: Json = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("Engine event must be a JSON object")
            self.observe(value)
            events.append(value)
        if len(self.buffer) > 1024 * 1024:
            raise ValueError("Engine event exceeded one MiB without a newline")
        return events

    def observe(self, event: dict[str, Json]) -> None:
        now_s = self.clock()
        details: dict[str, Json] = {"since_command_s": now_s - self.command_sent_s}
        if event.get("event") == "tap":
            self.tap_count += 1
            # Keep receipt timing and sample positions without copying PCM into the trace.
            details["payload"] = {key: value for key, value in event.items() if key != "pcm"}
        else:
            details["payload"] = event
        if event.get("event") == "status":
            if self.previous_status_s is not None:
                gap_s = now_s - self.previous_status_s
                details["status_gap_s"] = gap_s
                self.max_status_gap_s = max(self.max_status_gap_s, gap_s)
            underruns = counter(event, "underruns")
            details["underrun_delta"] = underruns - self.max_underruns
            self.max_underruns = max(self.max_underruns, underruns)
            self.max_tap_dropped = max(self.max_tap_dropped, counter(event, "tap_dropped"))
            self.previous_status_s = now_s
            self.latest_status = event
            self.status_count += 1
        self.log("event", details)
        if event.get("ok") is False:
            raise RuntimeError(f"Engine rejected command: {event.get('error')}")

    def reply(self, timeout_s: float = 180) -> dict[str, Json]:
        deadline_s = self.clock() + timeout_s
        while self.clock() < deadline_s:
            for event in self.receive(deadline_s - self.clock()):
                if event.get("ok") is True:
                    return event
        raise TimeoutError("Engine command reply timed out")

    def status(self, timeout_s: float = 30) -> dict[str, Json]:
        previous_count = self.status_count
        deadline_s = self.clock() + timeout_s
        while self.clock() < deadline_s:
            self.receive(deadline_s - self.clock())
            if self.status_count > previous_count and self.latest_status is not None:
                return self.latest_status
        raise TimeoutError("Engine stopped publishing status events")


def counter(event: dict[str, Json], name: str) -> int:
    value = event.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Status {name} must be a nonnegative integer")
    return value


def toggle(client: SessionClient, index: int, played_samples: int) -> None:
    weight = 0.2 if index % 2 else 0.8
    client.send({"cmd": "blend", "weights": [weight, 1 - weight, 0, 0, 0, 0]}, "slider")
    # Protocol v1 has no loop command; replay the scheduler's note reset and reference seek.
    for pitch in CHORD:
        client.send({"cmd": "noteoff", "pitch": pitch}, "loop")
    for pitch in CHORD:
        client.send({"cmd": "noteon", "pitch": pitch}, "loop")
    at_sample = played_samples + SAMPLE_RATE_HZ
    client.send({"cmd": "ref_play", "at_sample": at_sample, "from_sample": 0}, "loop")
    client.send({"cmd": "click_at", "sample": at_sample, "accent": True}, "loop")
    client.send({"cmd": "ref_mix", "band_gain": index % 2, "ref_gain": 1 - index % 2}, "reference")


@dataclass(frozen=True)
class Summary:
    played_samples: int
    status_events: int
    tap_events: int
    underruns: int
    tap_dropped: int
    max_status_gap_s: float


def exercise(client: SessionClient, resources: Path, reference: Path, seconds: float) -> Summary:
    client.send({"cmd": "hello"}, "setup")
    if client.reply().get("protocol") != 1:
        raise ValueError("Expected engine protocol 1")
    client.send({"cmd": "load", "model": "mrt2_small", "resources": str(resources)}, "setup")
    client.reply()
    client.send({"cmd": "ref_load", "path": str(reference)}, "setup")
    client.reply()
    for slot, prompt in enumerate(("a jazz piano trio", "disco funk")):
        client.send({"cmd": "prompt", "slot": slot, "text": prompt, "weight": 0.5}, "setup")
    client.send({"cmd": "tap", "on": True}, "setup")
    client.send({"cmd": "play"}, "setup")
    first = client.status()
    first_sample = counter(first, "played_samples")
    # Wait for both text encodings before testing slider changes.
    deadline_s = client.clock() + 180
    while True:
        prompts = first.get("prompt_status")
        if not isinstance(prompts, list) or len(prompts) != 6:
            raise ValueError("Expected six prompt statuses")
        if 3 in prompts[:2]:
            raise RuntimeError("Text prompt encoding failed")
        if prompts[:2] == [2, 2]:
            break
        if client.clock() >= deadline_s:
            raise TimeoutError("Text prompt encoding timed out")
        first = client.status()
    for pitch in CHORD:
        client.send({"cmd": "noteon", "pitch": pitch}, "play")
    started_sample = counter(first, "played_samples")
    target_sample = started_sample + math.ceil(seconds * SAMPLE_RATE_HZ)
    next_toggle_sample = started_sample
    index = 0
    deadline_s = client.clock() + seconds + 30
    while counter(first, "played_samples") < target_sample:
        if client.clock() > deadline_s:
            raise TimeoutError("Audio sample clock did not cover the requested duration")
        if counter(first, "played_samples") >= next_toggle_sample:
            toggle(client, index, counter(first, "played_samples"))
            index += 1
            next_toggle_sample += TOGGLE_SECONDS * SAMPLE_RATE_HZ
        first = client.status()
    if client.tap_count == 0:
        raise RuntimeError("No tap events received while the tap was enabled")
    summary = Summary(
        counter(first, "played_samples") - first_sample,
        client.status_count,
        client.tap_count,
        client.max_underruns,
        client.max_tap_dropped,
        client.max_status_gap_s,
    )
    client.send({"cmd": "stop"}, "shutdown")
    client.send({"cmd": "hello"}, "shutdown")
    client.reply()
    client.send({"cmd": "quit"}, "shutdown")
    client.log(
        "summary",
        {
            "played_samples": summary.played_samples,
            "status_events": summary.status_events,
            "tap_events": summary.tap_events,
            "underruns": summary.underruns,
            "tap_dropped": summary.tap_dropped,
            "max_status_gap_s": summary.max_status_gap_s,
        },
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--seconds", default=120.0, type=float)
    args = parser.parse_args()
    socket_path = Path(args.socket)
    resources = Path(args.resources).resolve(strict=True)
    trace = Path(args.trace)
    seconds = float(args.seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        parser.error("--seconds must be finite and positive")
    with tempfile.TemporaryDirectory(prefix="ghostband-reference-") as directory:
        reference = Path(directory) / "reference.wav"
        reference_wav(reference)
        with socket.socket(socket.AF_UNIX) as connection, trace.open("w") as output:
            deadline_s = time.monotonic() + 30
            while True:
                try:
                    connection.connect(str(socket_path))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() >= deadline_s:
                        raise TimeoutError("Engine socket did not become available") from None
                    time.sleep(0.05)
            connection.settimeout(30)
            result = exercise(SessionClient(connection, output), resources, reference, seconds)
            print(f"Live session: {result}")


if __name__ == "__main__":
    main()
