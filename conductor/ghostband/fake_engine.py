"""Deterministic note and blend engine, without audio, assets or model downloads."""

import base64
import socket
import wave
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import cache
from math import isfinite
from pathlib import Path
from threading import Event

import librosa
import numpy as np
from numpy.typing import NDArray
from pydantic import TypeAdapter

from ghostband.engine_client import SAMPLE_RATE, Command, EngineClient, encode
from ghostband.heard import BLOCK_SAMPLES

STATUS_INTERVAL_S = 0.100
FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures"
DEFAULT_TAP_FIXTURE = FIXTURE_ROOT / "greensleeves.wav"
_WEIGHTS = TypeAdapter(list[float])


@cache
def _fixture_audio(path: Path) -> NDArray[np.float32]:
    """Decode a rendered fixture at the engine rate so tap indices are real."""
    audio: NDArray[np.float32] = np.asarray(
        librosa.load(path, sr=SAMPLE_RATE, mono=True)[0], dtype=np.float32
    )
    return audio


def _blend_weights(value: object) -> tuple[float, ...]:
    try:
        weights = _WEIGHTS.validate_python(value, strict=True)
    except ValueError as error:
        raise ValueError("Blend requires six nonnegative finite weights") from error
    if len(weights) != 6 or any(not isfinite(weight) or weight < 0 for weight in weights):
        raise ValueError("Blend requires six nonnegative finite weights")
    return tuple(weights)


@dataclass(frozen=True)
class ReceivedCommand:
    time_s: float
    command: Command


class FakeEngine:
    """Run in memory or on a connected Unix socket with an injected clock."""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        latency_samples: int = 0,
        tap_fixture: Path | None = None,
    ) -> None:
        if latency_samples < 0:
            raise ValueError("Latency samples must be nonnegative")
        self.clock = clock
        self.latency_samples = latency_samples
        self.playing = False
        self.tap_enabled = False
        self.tap_fixture = DEFAULT_TAP_FIXTURE if tap_fixture is None else tap_fixture
        self.active_notes: set[int] = set()
        self.weights: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.onset_mode = 0
        self.commands: list[ReceivedCommand] = []
        self.replies: deque[Command] = deque()
        self.record_path: Path | None = None
        self.record_start_s = 0.0
        self.recorded_samples = 0
        self._play_time_s = 0.0
        self._played_samples = 0
        self._last_status_s = float("-inf")
        self._tap_sample = 0

    @property
    def played_samples(self) -> int:
        """Freeze the callback position on stop and reset it on play."""
        if self.playing:
            return round((self.clock() - self._play_time_s) * SAMPLE_RATE)
        return self._played_samples

    def status(self) -> Command:
        """Expose the same sample-position fields as the audio engine."""
        return {
            "event": "status",
            "played_samples": self.played_samples,
            "latency_samples": self.latency_samples,
            "ms_per_frame": 0.0,
            "underruns": 0,
            "prompt_status": [0, 0, 0, 0, 0, 0],
            "recording": self.record_path is not None,
            "recorded_samples": (
                round((self.clock() - self.record_start_s) * SAMPLE_RATE)
                if self.record_path
                else self.recorded_samples
            ),
            "tap_dropped": 0,
        }

    def send(self, command: Command) -> None:  # noqa: PLR0912
        """Apply supported controls immediately, as the real note engine does."""
        name = command.get("cmd")
        if not isinstance(name, str):
            raise ValueError("Engine command requires a string cmd field")
        now_s = self.clock()
        if name == "hello":
            self.replies.append({"ok": True, "protocol": 1, "engine_version": "fake"})
        elif name in {"load", "ref_load"}:
            self._load(command)
        elif name == "record":
            self._record(command)
        elif name in {
            "prompt",
            "prompt_audio",
            "drumless",
            "params",
            "click_at",
            "ref_mix",
            "ref_play",
        }:
            self.commands.append(ReceivedCommand(now_s, command.copy()))
            return
        elif name == "play":
            self._play_time_s = now_s
            self._played_samples = 0
            self._last_status_s = float("-inf")
            self._tap_sample = 0
            self.playing = True
        elif name == "tap":
            on = command.get("on")
            if type(on) is not bool:
                raise ValueError("Tap requires a boolean on field")
            self.tap_enabled = on
        elif name in {"stop", "quit"}:
            self._played_samples = self.played_samples
            self.playing = False
            self.active_notes.clear()
        elif name in {"noteon", "noteoff"}:
            self._note(command, name)
        elif name == "onset_mode":
            mode = command.get("mode")
            if type(mode) is not int or mode not in {0, 1}:
                raise ValueError("Onset mode must be 0 or 1")
            self.onset_mode = mode
        elif name == "blend":
            self.weights = _blend_weights(command.get("weights"))
        else:
            raise ValueError(f"Unsupported fake engine command: {name!r}")
        self.commands.append(ReceivedCommand(now_s, command.copy()))

    def _load(self, command: Command) -> None:
        if command["cmd"] == "load":
            if command.get("model") not in {"mrt2_small", "mrt2_base"} or not isinstance(
                command.get("resources"), str
            ):
                raise ValueError("Unsupported load: supply model and resources")
        elif not isinstance(command.get("path"), str):
            raise ValueError("Unsupported ref_load: supply a path")
        self.replies.append({"ok": True})

    def poll_taps(self) -> list[Command]:
        """Replay the fixture as tap blocks up to the audible position."""
        if not self.tap_enabled or not self.playing:
            return []
        audio = _fixture_audio(self.tap_fixture)
        blocks: list[Command] = []
        while self._tap_sample + BLOCK_SAMPLES <= self.played_samples:
            window = np.zeros(BLOCK_SAMPLES, np.float32)
            chunk = audio[self._tap_sample : self._tap_sample + BLOCK_SAMPLES]
            window[: len(chunk)] = chunk
            blocks.append(
                {
                    "event": "tap",
                    "sample": self._tap_sample,
                    "pcm": base64.b64encode(window.astype("<f4").tobytes()).decode("ascii"),
                }
            )
            self._tap_sample += BLOCK_SAMPLES
        return blocks

    def _note(self, command: Command, name: str) -> None:
        pitch = command.get("pitch")
        if type(pitch) is not int or not 0 <= pitch <= 127:
            raise ValueError("Note pitch must be an integer between 0 and 127")
        if name == "noteon":
            self.active_notes.add(pitch)
        else:
            self.active_notes.discard(pitch)

    def _record(self, command: Command) -> None:
        if command.get("action") == "start" and isinstance(command.get("path"), str):
            self.record_path = Path(str(command["path"]))
            self.record_start_s = self.clock()
        elif command.get("action") == "stop" and self.record_path is not None:
            self.recorded_samples = round((self.clock() - self.record_start_s) * SAMPLE_RATE)
            with wave.open(str(self.record_path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                remaining = self.recorded_samples
                while remaining:
                    frames = min(remaining, SAMPLE_RATE)
                    output.writeframesraw(bytes(frames * 4))
                    remaining -= frames
            self.record_path = None
        else:
            raise ValueError("Record requires start with a path or an active recording to stop")
        self.replies.append({"ok": True})

    def serve(self, connection: socket.socket, shutdown: Event) -> None:
        """Serve one connection; the caller owns the listener and thread lifetime.

        Recording writes silence; model loading acknowledges without reading assets.
        No model inference or audio device is simulated.
        """
        # Like the engine, a client slow to read its events can still send commands.
        connection.setblocking(False)
        transport = EngineClient(connection)
        outgoing = bytearray()
        try:
            while not shutdown.is_set():
                try:
                    command = transport.receive(timeout_s=0.010)
                except TimeoutError:
                    command = None
                if command is not None:
                    try:
                        self.send(command)
                    except ValueError as error:
                        self.replies.append({"ok": False, "error": str(error)})
                    if command.get("cmd") == "quit":
                        break
                while self.replies:
                    outgoing += encode(self.replies.popleft())
                now_s = self.clock()
                if self.playing and now_s - self._last_status_s >= STATUS_INTERVAL_S:
                    outgoing += encode(self.status())
                    self._last_status_s = now_s
                for block in self.poll_taps():
                    outgoing += encode(block)
                with suppress(BlockingIOError):
                    del outgoing[: connection.send(outgoing)]
        except ConnectionError:
            # A client disconnect ends the session just like a stop command.
            return
        finally:
            self.active_notes.clear()
            self.playing = False
