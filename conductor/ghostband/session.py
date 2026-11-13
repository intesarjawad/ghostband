"""Own one chart, transport and engine connection for local clients."""

import os
from collections.abc import Callable
from importlib.resources import as_file, files
from math import floor, isfinite
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ghostband import engine_process, first_run
from ghostband.bench import LOAD_TIMEOUT_S, base_model_status
from ghostband.chart import Chart, ChordEvent, Section
from ghostband.chords import parse_chord
from ghostband.engine_client import (
    SAMPLE_RATE,
    Command,
    EngineBusyError,
    EngineClient,
    EngineStatus,
)
from ghostband.errors import ErrorCode, GhostbandError
from ghostband.evaluation import live as live_report
from ghostband.evaluation.chord_detector import majmin
from ghostband.fake_engine import FakeEngine
from ghostband.heard import HeardEvent, HeardRow, decode_tap
from ghostband.loudness import TapLoudness, measure_loudness, reference_gain
from ghostband.presets import Preset, load_presets
from ghostband.scheduler import Scheduler
from ghostband.style import Slew

# The widest range the rehearsal control may offer, before any measured range narrows it.
TEMPO_MIN = 0.5
TEMPO_MAX = 1.5
# Onset mode 1 with the bass struck on every beat is the only tempo cue MRT2 takes; ADR 0014.
SHIPPED_ONSET_MODE = 1


def measured_tempo_range() -> tuple[float, float]:
    """Use checkout or packaged measurements; missing or invalid evidence permits only 100%."""
    folder = live_report.default_directory()
    source = (
        folder / live_report.CLAIMS_NAME
        if folder
        else files("ghostband").joinpath("tempo_claims.json")
    )
    try:
        with as_file(source) as path:
            claims = live_report.read_claims(path)
    except (ValueError, OSError):
        return 1.0, 1.0
    if claims is None:
        return 1.0, 1.0
    if claims.tempo_range is None:
        # Measured with no passing range means the band holds only the chart tempo.
        return 1.0, 1.0
    low, high = (max(TEMPO_MIN, claims.tempo_range[0]), min(TEMPO_MAX, claims.tempo_range[1]))
    return (low, high) if low <= 1.0 <= high else (1.0, 1.0)


class Control(BaseModel):
    """Validate untrusted control fields before changing transport state."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal[
        "transport",
        "slider",
        "preset",
        "prompt",
        "loop",
        "tempo_factor",
        "record",
        "set_rights",
        "toggle",
        "edit_chord",
        "rename_section",
        "undo",
        "redo",
    ]
    name: str = ""
    symbol: str = ""
    enabled: bool = True
    index: int = Field(default=0, ge=0, strict=True)
    action: Literal["play", "stop", "start", "seek"] | None = None
    value: float | None = None
    start: float | None = None
    end: float | None = None
    slot: int = Field(default=0, ge=0, le=5, strict=True)
    text: str = Field(default="", max_length=4096)
    rights: Literal["public-domain", "own-work", "unknown"] = "unknown"


class Session:
    """Use the same scheduler and state for HTTP, WebSocket and terminal playback."""

    def __init__(
        self,
        *,
        fake_engine: bool = False,
        clock: Callable[[], float] = monotonic,
        tempo_range: tuple[float, float] | None = None,
        onset_mode: int = SHIPPED_ONSET_MODE,
    ) -> None:
        self.clock = clock
        self.fake_engine = fake_engine
        self.onset_mode = onset_mode
        self.tempo_range = tempo_range if tempo_range is not None else measured_tempo_range()
        if not TEMPO_MIN <= self.tempo_range[0] <= 1.0 <= self.tempo_range[1] <= TEMPO_MAX:
            raise GhostbandError(
                ErrorCode.INVALID_INPUT,
                f"Tempo range must contain 1.0 inside {TEMPO_MIN:g} to {TEMPO_MAX:g}",
            )
        self.base_model = base_model_status().as_state()
        self.engine: EngineClient | FakeEngine | None = None
        self._engine_process: engine_process.EngineProcess | None = None
        self.chart: Chart | None = None
        self.scheduler: Scheduler | None = None
        self.slew = Slew(self.send)
        self.loop: tuple[float, float] | None = None
        self.tempo_factor = 1.0
        self.presets: tuple[Preset, ...] = load_presets()[0]
        self.preset = 0
        self.slots = [self.presets[0].a, self.presets[0].b, "", "", "", ""]
        self.recording = False
        self._record_started_s = 0.0
        self._last_click_beat = -1
        self._stopped_beat = 0.0
        self.record_path: Path | None = None
        self.health: Command = {"online": False}
        self._undo: list[Chart] = []
        self._redo: list[Chart] = []
        self.toggles = {"melody": True, "drums": True, "click": False, "reference": False}
        self._reference_path: Path | None = None
        self.reference_loudness: float | None = None
        self.reference_gain = 1.0
        self.tap_loudness = TapLoudness()
        self._loaded_reference: Path | None = None
        self._reference_wrap_s: float | None = None
        self._last_now_s = 0.0
        self._play_at_s = 0.0
        self._status_at_s = 0.0
        self._audio_time_s = 0.0
        self._heard: HeardRow | None = None
        self._heard_offset = 0.0
        self.heard_events: list[dict[str, object]] = []
        self._underruns = 0
        self._underruns_at_play = 0
        # Frames the engine failed to generate in time since the last play; a take with any
        # measures how busy the Mac was, not the band.
        self.dropped_frames = 0

    @property
    def reference_path(self) -> Path | None:
        """The decoded recording kept inside the import workspace."""
        return self._reference_path

    @reference_path.setter
    def reference_path(self, path: Path | None) -> None:
        """Measure the take once when the import points the session at it."""
        self._reference_path = path
        loudness = measure_loudness(path) if path is not None else None
        self.reference_loudness = loudness if loudness is not None and isfinite(loudness) else None
        if path != self._loaded_reference:
            self._loaded_reference = None
        self._reference_wrap_s = None

    def connect(self) -> None:
        if self.engine is not None:
            return
        resources = os.environ.get("GHOSTBAND_RESOURCES")
        if not self.fake_engine and not resources:
            raise GhostbandError(
                ErrorCode.ENGINE_OFFLINE, "set GHOSTBAND_RESOURCES to the model resources directory"
            )
        try:
            if self.fake_engine:
                self.engine = FakeEngine(self.clock)
            else:
                self.engine, self._engine_process = engine_process.connect(
                    first_run.Paths.defaults().engine_binary, engine_process.default_socket()
                )
                # A first load after boot reads the weights from disk and compiles kernels.
                self.engine.request(
                    {"cmd": "load", "model": "mrt2_small", "resources": resources},
                    timeout_s=LOAD_TIMEOUT_S,
                )
            self.engine.send({"cmd": "onset_mode", "mode": self.onset_mode})
            self.engine.send({"cmd": "params", **self.presets[self.preset].guidance.model_dump()})
            for slot, text in enumerate(self.slots):
                if text:
                    self.send({"cmd": "prompt", "slot": slot, "text": text, "weight": 0.0})
            self.slew.jump(self.slew.target)
            self.health = {"online": True, "fake": self.fake_engine}
        except EngineBusyError as busy:
            self._release_engine()
            raise GhostbandError(
                ErrorCode.ENGINE_BUSY, engine_process.describe_holder(busy)
            ) from busy
        except (OSError, ValueError, RuntimeError) as error:
            self._release_engine()
            raise GhostbandError(ErrorCode.ENGINE_OFFLINE, str(error)) from error

    def _release_engine(self) -> None:
        """Disconnect, and stop the engine process only if this session started it."""
        if isinstance(self.engine, EngineClient):
            self.engine.close()
        self.engine = None
        if self._engine_process is not None:
            self._engine_process.stop()
            self._engine_process = None

    def _failure(self, error: Exception) -> str:
        """Prefer how the engine process ended to the socket error its exit caused."""
        # A rejected command comes from a live engine; only a lost socket is worth the wait.
        if isinstance(error, OSError) and self._engine_process is not None:
            reason = self._engine_process.exit_reason()
            if reason is not None:
                return reason
        return str(error)

    def send(self, command: Command) -> None:
        if self.engine is None:
            raise GhostbandError(ErrorCode.ENGINE_OFFLINE, "no engine connection")
        if command.get("cmd") == "play":
            self._audio_time_s = self.now()
            self._play_at_s = self._audio_time_s
            self._status_at_s = self.clock()
            self._last_click_beat = -1
        try:
            self.engine.send(command)
        except (OSError, ValueError, RuntimeError) as error:
            raise GhostbandError(ErrorCode.ENGINE_FAILED, self._failure(error)) from error

    def request(self, command: Command) -> Command:
        if self.engine is None:
            raise GhostbandError(ErrorCode.ENGINE_OFFLINE, "no engine connection")
        try:
            if isinstance(self.engine, FakeEngine):
                self.engine.send(command)
                return self.engine.replies.popleft()
            return self.engine.request(command)
        except (OSError, ValueError, RuntimeError) as error:
            raise GhostbandError(ErrorCode.ENGINE_FAILED, self._failure(error)) from error

    def load_chart(self, chart: Chart) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.stop()
        self._undo.clear()
        self._redo.clear()
        self.chart = chart
        self.toggles["melody"] = True
        self.toggles["reference"] = False
        self.reference_path = None
        self.reference_gain = 1.0
        self._stopped_beat = 0.0
        self.scheduler = Scheduler(chart, self.send, lookahead_s=0.2, pulse=self.onset_mode == 1)
        self.loop = None
        self.tempo_factor = 1.0

    def play(self) -> None:
        if self.scheduler is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "import a chart before playing")
        self.connect()
        self._play_at_s = self.clock()
        self._status_at_s = self._play_at_s
        self._audio_time_s = self._play_at_s
        self._underruns_at_play = self._underruns
        self.dropped_frames = 0
        self._start_heard(self.loop[0] if self.loop else 0.0)
        self.scheduler.play(self._play_at_s)
        self._reference_wrap_s = None
        if self.toggles["reference"]:
            self._reference_resync(at_s=self._play_at_s + self.scheduler.lookahead_s)

    def _start_heard(self, offset_beat: float) -> None:
        """Ask for tapped audio and start a row from the next played beat."""
        chart = self._require_scheduler().chart
        self._heard = HeardRow(beat_seconds=60 / chart.tempo_bpm / self.tempo_factor)
        self._heard_offset = offset_beat
        self.tap_loudness.reset()
        self.heard_events.clear()
        self.send({"cmd": "tap", "on": True})

    def stop(self) -> None:
        if self.scheduler is not None and self.engine is not None:
            self._stopped_beat = self.scheduler.audible_beat(self.now())
            self.scheduler.stop(self.now())
        if self.recording:
            self.record("stop")

    def now(self) -> float:
        self._last_now_s = max(
            self._last_now_s, self._audio_time_s + max(0.0, self.clock() - self._status_at_s)
        )
        return self._last_now_s

    @property
    def play_time_s(self) -> float:
        """The scheduler clock when `play` was sent, the origin of a take's beat log."""
        return self._play_at_s

    def beat_log(self) -> list[tuple[float, float]]:
        """The counted beats the scheduler made audible, for a take's sidecar."""
        return self.scheduler.beat_log() if self.scheduler is not None else []

    def _poll_status(self) -> None:
        if self.engine is not None:
            messages: list[Command] = []
            if isinstance(self.engine, FakeEngine):
                messages.append(self.engine.status())
                messages.extend(self.engine.poll_taps())
            else:
                while self.engine.events:
                    messages.append(self.engine.events.popleft())
                try:
                    while True:
                        messages.append(self.engine.receive(timeout_s=0))
                except TimeoutError:
                    pass
                except (OSError, ValueError, RuntimeError) as error:
                    raise GhostbandError(ErrorCode.ENGINE_FAILED, self._failure(error)) from error
            for message in messages:
                if message.get("event") == "tap":
                    self._accept_tap(message)
                elif message.get("event") == "status":
                    try:
                        status = EngineStatus.from_message(message)
                    except ValueError as error:
                        raise GhostbandError(ErrorCode.ENGINE_FAILED, str(error)) from error
                    self.health = {"online": True, **message}
                    self._audio_time_s = self._play_at_s + status.audible_time_s
                    underruns = message.get("underruns")
                    if type(underruns) is int:
                        self._underruns = underruns
                        self.dropped_frames = max(0, underruns - self._underruns_at_play)
                    self._status_at_s = self.clock()

    def _accept_tap(self, message: Command) -> None:
        try:
            sample, pcm = decode_tap(message)
        except ValueError as error:
            raise GhostbandError(ErrorCode.ENGINE_FAILED, str(error)) from error
        if self._heard is None:
            raise GhostbandError(ErrorCode.ENGINE_FAILED, "tap audio arrived outside playback")
        self.tap_loudness.push(sample, pcm)
        for event in self._heard.push(sample, pcm):
            self.heard_events.append(self._heard_event(event))

    def _heard_event(self, event: HeardEvent) -> dict[str, object]:
        expected = self.chart.symbol_at(self._heard_offset + event.beat) if self.chart else None
        return {
            "event": "heard",
            "beat": self._heard_offset + event.beat,
            "symbol": event.symbol,
            "confidence": event.confidence,
            "expected": expected,
            "matches": None if expected is None else majmin(expected) == event.symbol,
        }

    def drain_heard(self) -> list[dict[str, object]]:
        """Hand out the row updates completed since the last call, one per beat."""
        events, self.heard_events = self.heard_events, []
        return events

    def tick(self) -> dict[str, object]:
        self._poll_status()
        now = self.now()
        self._reference_wrap()
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.step(now)
            if not self.scheduler.running:
                self.stop()
        if self.engine is not None:
            self.slew.tick(self.clock())
        if self.recording and self.clock() - self._record_started_s >= 300:
            self.record("stop")
        self._click(now)
        return {
            "event": "clock",
            "audible_beat": (
                self.scheduler.audible_beat(now)
                if self.scheduler and self.scheduler.running
                else self._stopped_beat
            ),
            "now_s": self.clock(),
        }

    def record(self, action: str, path: Path | None = None) -> None:
        if action == "start":
            self.connect()
            if self.recording:
                raise GhostbandError(ErrorCode.INVALID_INPUT, "recording already started")
            self.record_path = path
            if path is None:
                raise GhostbandError(ErrorCode.INVALID_INPUT, "a recording output path is required")
            self.request({"cmd": "record", "action": "start", "path": str(path.resolve())})
            self.recording = True
            self._record_started_s = self.clock()
        elif action == "stop" and self.recording:
            self.request({"cmd": "record", "action": "stop"})
            self.recording = False
        else:
            raise GhostbandError(
                ErrorCode.INVALID_INPUT, "record requires start or an active recording to stop"
            )

    def control(self, data: dict[str, object], *, record_path: Path) -> None:
        try:
            command = Control.model_validate(data)
            handlers: dict[str, Callable[[Control], None]] = {
                "transport": self._transport,
                "slider": self._slider,
                "preset": self._preset,
                "prompt": self._prompt,
                "loop": self._loop,
                "tempo_factor": self._tempo,
                "record": lambda control: self.record(control.action or "", record_path),
                "set_rights": self._rights,
                "toggle": self._toggle,
                "edit_chord": self._edit,
                "rename_section": self._edit,
                "undo": self._history,
                "redo": self._history,
            }
            handlers[command.type](command)
        except (ValueError, IndexError) as error:
            raise GhostbandError(ErrorCode.INVALID_INPUT, str(error)) from error

    def _transport(self, command: Control) -> None:
        if command.action == "play":
            self.play()
        elif command.action == "stop":
            self.stop()
        elif command.action == "seek" and command.value is not None:
            self.connect()
            self._start_heard(command.value)
            scheduler = self._require_scheduler()
            scheduler.seek(command.value, self.now())
            if self.toggles["reference"]:
                self._reference_wrap_s = None
                self._reference_resync(at_s=self._play_at_s + scheduler.lookahead_s)
        else:
            raise GhostbandError(
                ErrorCode.INVALID_INPUT, "transport requires play, stop or seek with value"
            )

    def _slider(self, command: Control) -> None:
        if command.value is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "slider requires value")
        self.slew.set_target(command.value)

    def _preset(self, command: Control) -> None:
        if command.value is None or command.value not in range(len(self.presets)):
            last = len(self.presets) - 1
            raise GhostbandError(
                ErrorCode.INVALID_INPUT, f"preset must be an integer from 0 to {last}"
            )
        self.preset = int(command.value)
        held = self.presets[self.preset]
        self.slots[:2] = [held.a, held.b]
        if self.engine is None:
            self.connect()
            return
        self.send({"cmd": "params", **held.guidance.model_dump()})
        for slot, text in enumerate(self.slots[:2]):
            self.send(
                {
                    "cmd": "prompt",
                    "slot": slot,
                    "text": text,
                    "weight": (1 - self.slew.target) if slot == 0 else self.slew.target,
                }
            )

        self.slew.jump(self.slew.target)

    def _prompt(self, command: Control) -> None:
        self.connect()
        self.send({"cmd": "prompt", "slot": command.slot, "text": command.text, "weight": 1.0})
        self.slots[command.slot] = command.text

    def _require_scheduler(self) -> Scheduler:
        if self.scheduler is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "import a chart first")
        return self.scheduler

    def _loop(self, command: Control) -> None:
        if (command.start is None) != (command.end is None):
            raise GhostbandError(ErrorCode.INVALID_INPUT, "loop needs both start and end")
        selected = (
            (command.start, command.end)
            if command.start is not None and command.end is not None
            else None
        )
        scheduler = self._require_scheduler()
        scheduler.set_loop(selected, self.now())
        self.loop = selected
        if self.toggles["reference"]:
            self._reference_wrap_s = None
            if scheduler.running:
                self._reference_resync(at_s=self._play_at_s + scheduler.lookahead_s)

    def _tempo(self, command: Control) -> None:
        if command.value is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "tempo_factor requires value")
        low, high = self.tempo_range
        if not low <= command.value <= high:
            raise GhostbandError(
                ErrorCode.INVALID_INPUT,
                f"tempo_factor must be between {low:g} and {high:g}",
            )
        self._require_scheduler().set_tempo(command.value, self.now())
        self.tempo_factor = command.value
        if command.value != 1.0 and self.toggles["reference"]:
            self.toggles["reference"] = False
            self._reference_mix(False)
        if self._heard is not None and self.chart is not None:
            self._heard.beat_seconds = 60 / self.chart.tempo_bpm / command.value

    def _replace_chart(self, chart: Chart) -> None:
        scheduler = self._require_scheduler()
        running = scheduler.running
        now = self.now()
        beat = scheduler.audible_beat(now)
        if running:
            scheduler.stop(now)
        self.chart = chart
        audible = chart if self.toggles["melody"] else chart.model_copy(update={"melody": []})
        self.scheduler = Scheduler(
            audible, self.send, lookahead_s=scheduler.lookahead_s, pulse=scheduler.pulse
        )
        self.scheduler.set_tempo(self.tempo_factor, now)
        self.scheduler.set_loop(self.loop, now)
        if running:
            self._start_heard(beat)
            self.scheduler.play(now, beat)
            if self.toggles["reference"]:
                self._reference_wrap_s = None
                self._reference_resync(at_s=self._play_at_s + self.scheduler.lookahead_s)

    def _click(self, now: float) -> None:
        scheduler = self.scheduler
        if not scheduler or not scheduler.running or not self.toggles["click"]:
            return
        chart = scheduler.chart
        phase = chart.beat_phase
        whole = floor(scheduler.audible_beat(now + scheduler.lookahead_s) - phase + 1e-9)
        if whole == self._last_click_beat:
            return
        self._last_click_beat = whole
        position = whole + phase
        if position < (self.loop[0] if self.loop else 0):
            return
        beat_at_s = scheduler.beat_time(position)
        if beat_at_s < now:
            return
        self.send(
            {
                "cmd": "click_at",
                "sample": round((beat_at_s - self._play_at_s) * SAMPLE_RATE),
                "accent": (whole - floor(chart.bar_origin)) % chart.beats_per_bar == 0,
            }
        )

    def _load_reference(self) -> None:
        if self.reference_path is None:
            raise GhostbandError(
                ErrorCode.INVALID_INPUT, "import audio before enabling reference playback"
            )
        if self._loaded_reference != self.reference_path:
            self.request({"cmd": "ref_load", "path": str(self.reference_path)})
            self._loaded_reference = self.reference_path

    def _reference_resync(self, *, at_s: float | None = None) -> None:
        """Start the recording where the chart clock will be when `at_s` sounds."""
        scheduler = self.scheduler
        if scheduler is None or not scheduler.running:
            return
        self._load_reference()
        start_s = max(self.now(), self._play_at_s) if at_s is None else at_s
        position = scheduler.audible_beat(start_s)
        chart = scheduler.chart
        self.send(
            {
                "cmd": "ref_play",
                "at_sample": max(0, round((start_s - self._play_at_s) * SAMPLE_RATE)),
                "from_sample": max(0, round(position * 60 / chart.tempo_bpm * SAMPLE_RATE)),
            }
        )

    def _reference_mix(self, enabled: bool, *, measure: bool = False) -> None:
        if enabled and measure:
            band = self.tap_loudness.loudness()
            reference = self.reference_loudness
            if band is not None and reference is not None:
                # The engine protocol caps ref_gain at unity, so a quieter take plays unboosted.
                self.reference_gain = min(1.0, reference_gain(band, reference))
            else:
                self.reference_gain = 1.0
        self.send(
            {
                "cmd": "ref_mix",
                "band_gain": 0.0 if enabled else 1.0,
                "ref_gain": self.reference_gain if enabled else 0.0,
            }
        )

    def _reference_wrap(self) -> None:
        """Queue the recording's jump back to the loop start before it sounds."""
        scheduler = self.scheduler
        if (
            scheduler is None
            or not scheduler.running
            or not self.toggles["reference"]
            or self.loop is None
        ):
            return
        end_s = scheduler.beat_time(self.loop[1])
        if self._reference_wrap_s is not None and end_s <= self._reference_wrap_s + 1e-9:
            return
        if end_s > self.now() + scheduler.lookahead_s + 1e-9:
            return
        self._reference_resync(at_s=end_s)
        self._reference_wrap_s = end_s

    def _rights(self, command: Control) -> None:
        self._require_scheduler()
        if self.chart is not None:
            self._replace_chart(self.chart.model_copy(update={"rights": command.rights}))

    def _toggle(self, command: Control) -> None:
        if command.name not in self.toggles:
            raise GhostbandError(
                ErrorCode.INVALID_INPUT, "toggle requires melody, drums, click or reference"
            )
        chart = self.chart
        if chart is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "import a chart first")
        self.connect()
        if command.name == "reference":
            if command.enabled and self.tempo_factor != 1.0:
                raise GhostbandError(
                    ErrorCode.INVALID_INPUT, "Reference plays at 100 percent tempo"
                )
            if command.enabled and self.reference_path is None:
                raise GhostbandError(
                    ErrorCode.INVALID_INPUT, "import audio before enabling reference playback"
                )
            if command.enabled:
                self._load_reference()
                if self.scheduler is not None and self.scheduler.running:
                    self._reference_wrap_s = None
                    self._reference_resync()
            self._reference_mix(command.enabled, measure=command.enabled)
        elif command.name == "drums":
            self.send({"cmd": "drumless", "on": not command.enabled})
        self.toggles[command.name] = command.enabled
        if command.name == "melody":
            self._replace_chart(chart)

    def _edit(self, command: Control) -> None:
        chart = self.chart
        if chart is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "import a chart first")
        data = chart.model_dump()
        if command.type == "edit_chord":
            chords = chart.chords.copy()
            if command.index >= len(chords):
                raise GhostbandError(
                    ErrorCode.INVALID_INPUT,
                    f"no chord {command.index}; the chart has {len(chords)}",
                )
            original = chords[command.index]
            symbol = command.symbol.strip()
            parse_chord(symbol)
            chords[command.index] = ChordEvent(
                start_beat=original.start_beat, end_beat=original.end_beat, symbol=symbol
            )
            data["chords"] = [chord.model_dump() for chord in chords]
        else:
            sections = chart.sections.copy()
            if command.index >= len(sections):
                raise GhostbandError(
                    ErrorCode.INVALID_INPUT,
                    f"no section {command.index}; the chart has {len(sections)}",
                )
            original_section = sections[command.index]
            sections[command.index] = Section(
                start_beat=original_section.start_beat,
                end_beat=original_section.end_beat,
                name=command.name.strip(),
            )
            data["sections"] = [section.model_dump() for section in sections]
        replacement = Chart.model_validate(data)
        if replacement == chart:
            return
        self._replace_chart(replacement)
        self._undo.append(chart)
        self._redo.clear()

    def _history(self, command: Control) -> None:
        source, destination = (
            (self._undo, self._redo) if command.type == "undo" else (self._redo, self._undo)
        )
        if not source or self.chart is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "no chart edit to undo or redo")
        current = self.chart
        self._replace_chart(source[-1])
        source.pop()
        destination.append(current)

    def state(self) -> dict[str, object]:
        return {
            "event": "state",
            "chart": self.chart.model_dump(mode="json") if self.chart else None,
            "chart_geometry": self.chart.geometry().model_dump() if self.chart else None,
            "transport": "playing" if self.scheduler and self.scheduler.running else "stopped",
            "tempo_factor": self.tempo_factor,
            "tempo_range": list(self.tempo_range),
            "loop": self.loop,
            "slider": self.slew.target,
            "preset": self.preset,
            "slots": self.slots.copy(),
            **self.toggles,
            "recording": self.recording,
            "engine_health": self.health,
            "base_model": self.base_model,
            "extractor": self.chart.extractor if self.chart else None,
        }

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self._release_engine()
            # Without an engine nothing records; a later close must not ask it to stop.
            self.recording = False
            self._heard = None
            self._loaded_reference = None
            self._reference_wrap_s = None
            self.tap_loudness.reset()
            self.health = {"online": False}
            if self.scheduler is not None:
                self.scheduler = Scheduler(
                    self.scheduler.chart,
                    self.send,
                    lookahead_s=self.scheduler.lookahead_s,
                    pulse=self.scheduler.pulse,
                )
                self.scheduler.set_loop(self.loop, self.now())
                self.scheduler.set_tempo(self.tempo_factor, self.now())
