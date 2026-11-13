"""Studio render: a chart's band timeline on a virtual clock, then an offline engine run.

Nothing here waits for wall time. The scheduler advances one engine frame per
tick of a virtual clock with no lookahead, so a command lands on the first frame
boundary at or after its audible time, up to one frame (40 ms) late. The frame
count covers the whole chart, so the length of the WAV follows from the chart
and the tempo factor alone. The command timeline is written to a sidecar beside
the WAV with `mode: offline`; the engine reads that timeline and applies each
note command before it generates the frame the note belongs to.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

from ghostband.chart import Chart
from ghostband.engine_client import CONTROL_FRAME_S, SAMPLE_RATE, Command
from ghostband.errors import ErrorCode, GhostbandError
from ghostband.presets import Guidance
from ghostband.scheduler import Scheduler
from ghostband.style import Slew

FRAME_SAMPLES = round(CONTROL_FRAME_S * SAMPLE_RATE)
# The engine writes a RIFF WAV of 32-bit stereo float frames, so the 4 GiB size field caps a take.
MAX_FRAMES = (2**32 - 1 - 36) // (FRAME_SAMPLES * 8)
ENGINE_TIMEOUT_S = 3600.0

Run = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class OfflineEngine:
    """The engine binary and what it renders with: a model folder and a text prompt."""

    binary: Path
    model_dir: Path
    prompt: str
    model: str = "mrt2_small"
    preset: str = ""
    prompt_b: str = ""
    guidance: Guidance = field(default_factory=Guidance)
    seed_rotation: int = 0


@dataclass(frozen=True)
class ScheduledCommand:
    """An engine command and the model frame whose generation it first affects."""

    frame: int
    command: Command

    @property
    def time_s(self) -> float:
        return self.frame * CONTROL_FRAME_S


@dataclass(frozen=True)
class Render:
    """The frames the engine must generate and the commands due along the way."""

    frames: int
    commands: tuple[ScheduledCommand, ...]
    beat_audible: tuple[tuple[float, float], ...] = ()

    @property
    def seconds(self) -> float:
        return self.frames * FRAME_SAMPLES / SAMPLE_RATE


def plan(
    chart: Chart,
    tempo_factor: float = 1.0,
    *,
    guidance: Guidance | None = None,
    slider: float = 0.0,
    sweep: bool = False,
) -> Render:
    """Run the scheduler over the whole chart on a virtual clock, one frame per tick."""
    if not isfinite(tempo_factor) or tempo_factor <= 0:
        raise ValueError("Tempo factor must be finite and positive")
    frame = 0
    scheduled: list[ScheduledCommand] = []

    def send(command: Command) -> None:
        scheduled.append(ScheduledCommand(frame, command))

    send({"cmd": "onset_mode", "mode": 1})
    send({"cmd": "params", **(guidance or Guidance()).model_dump()})
    slew = Slew(send)
    slew.jump(slider)
    slew.tick(0.0)
    scheduler = Scheduler(chart, send, lookahead_s=0.0, pulse=True)
    scheduler.set_tempo(tempo_factor, 0.0)
    scheduler.play(0.0)
    scheduler.step(0.0)
    while scheduler.running:
        frame += 1
        if sweep:
            slew.set_target(
                min(
                    1.0,
                    frame
                    * CONTROL_FRAME_S
                    / (chart.duration_beats * 60 / chart.tempo_bpm / tempo_factor),
                )
            )
            slew.tick(frame * CONTROL_FRAME_S)
        scheduler.step(frame * CONTROL_FRAME_S)
    frames = max(frame, 1)
    if frames > MAX_FRAMES:
        raise ValueError(f"Render of {frames} frames exceeds the {MAX_FRAMES}-frame WAV limit")
    return Render(frames, tuple(scheduled), tuple(scheduler.beat_log()))


def engine_arguments(  # noqa: PLR0913
    engine: Path,
    frames: int,
    output: Path,
    model_dir: Path,
    prompt: str,
    *,
    commands: Path,
    model: str,
) -> list[str]:
    """The offline invocation: the engine writes the WAV itself, without a socket."""
    if not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"Frames must be between 1 and {MAX_FRAMES}")
    if not prompt.strip():
        raise ValueError("Prompt must not be empty")
    return [
        str(engine),
        "--offline",
        "--frames",
        str(frames),
        "--prompt",
        prompt,
        "--out",
        str(output),
        "--model-dir",
        str(model_dir),
        "--model",
        model,
        "--commands",
        str(commands),
    ]


def sidecar_path(output: Path) -> Path:
    """The JSON beside a render, holding its mode and the command timeline."""
    return output.with_name(output.name + ".json")


def write_sidecar(
    output: Path,
    result: Render,
    engine: OfflineEngine,
    tempo_factor: float,
) -> Path:
    """Write the offline sidecar and the per-frame note timeline the engine reads."""
    data: dict[str, object] = {
        "mode": "offline",
        "model": engine.model,
        "preset": engine.preset,
        "prompt": engine.prompt,
        "seed_rotation": engine.seed_rotation,
        "frames": result.frames,
        "seconds": result.seconds,
        "tempo_factor": tempo_factor,
        "commands": [
            {
                "frame": 0,
                "time_s": 0,
                "command": {
                    "cmd": "prompt",
                    "slot": slot,
                    "text": text,
                    "weight": 1.0 if slot == 0 else 0.0,
                },
            }
            for slot, text in enumerate((engine.prompt, engine.prompt_b))
            if text
        ]
        + [
            {"frame": item.frame, "time_s": item.time_s, "command": item.command}
            for item in result.commands
            if item.command["cmd"] != "play" and item.frame < result.frames
        ],
    }
    path = sidecar_path(output)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        arguments, capture_output=True, text=True, timeout=ENGINE_TIMEOUT_S, check=False
    )


def render_chart(  # noqa: PLR0913
    chart: Chart,
    output: Path,
    engine: OfflineEngine,
    *,
    tempo_factor: float = 1.0,
    run: Run = _run,
    slider: float = 0.0,
    sweep: bool = False,
) -> Render:
    """Render the chart to a WAV with the offline engine and return what was scheduled."""
    if not output.parent.is_dir():
        raise GhostbandError(ErrorCode.IO_FAILED, f"{output.parent} is not a folder")
    if not engine.binary.is_file():
        raise GhostbandError(ErrorCode.UNAVAILABLE, f"engine not found at {engine.binary}")
    result = plan(chart, tempo_factor, guidance=engine.guidance, slider=slider, sweep=sweep)
    sidecar = sidecar_path(output)
    arguments = engine_arguments(
        engine.binary,
        result.frames,
        output,
        engine.model_dir,
        engine.prompt,
        commands=sidecar,
        model=engine.model,
    )
    write_sidecar(output, result, engine, tempo_factor)
    try:
        completed = run(arguments)
    except subprocess.TimeoutExpired as error:
        raise GhostbandError(ErrorCode.ENGINE_FAILED, "offline render timed out") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise GhostbandError(
            ErrorCode.ENGINE_FAILED,
            detail[0] if detail else f"exit status {completed.returncode}",
        )
    return result


def write_take(  # noqa: PLR0913
    output: Path,
    result: Render,
    engine: OfflineEngine,
    chart_path: Path,
    repeat: int,
    *,
    condition: str | None = None,
    slider: int | None = None,
) -> Path:
    """Adapt the virtual scheduler log for diagnostic scoring, never live acceptance."""
    data: dict[str, object] = {
        "mode": "offline",
        "chart": str(chart_path),
        "preset": engine.preset,
        "run": repeat,
        "recording": output.name,
        "play_time_s": 0.0,
        "beat_audible": result.beat_audible,
        "dropped_frames": 0,
    }
    if condition is not None:
        data.update(condition=condition, slider=slider)
    path = output.with_suffix(".take.json")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
