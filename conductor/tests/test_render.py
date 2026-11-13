import json
import subprocess
from pathlib import Path

import pytest
from ghostband.chart import Chart, ChordEvent, NoteEvent, Section
from ghostband.errors import ErrorCode, GhostbandError
from ghostband.presets import Guidance
from ghostband.render import (
    MAX_FRAMES,
    OfflineEngine,
    Render,
    Run,
    engine_arguments,
    plan,
    render_chart,
    write_take,
)

from scripts.measure_sync import TakeFile, validate_protocol


def short_chart(*, duration_beats: float = 4) -> Chart:
    return Chart(
        schema_version=1,
        title="Render test",
        source="test",
        rights="own-work",
        extractor="manual",
        tempo_bpm=120,
        beat_times_s=[],
        time_signature=(4, 4),
        key="C",
        sections=[Section(name="A", start_beat=0, end_beat=duration_beats)],
        chords=[ChordEvent(start_beat=0, end_beat=duration_beats, symbol="C")],
        melody=[
            NoteEvent(start_beat=0, end_beat=1, pitch=72),
            NoteEvent(start_beat=1, end_beat=3, pitch=74),
        ],
        duration_beats=duration_beats,
    )


def melody_frames(chart: Chart, tempo_factor: float = 1.0) -> list[tuple[int, str, object]]:
    return [
        (item.frame, str(item.command["cmd"]), item.command.get("pitch"))
        for item in plan(chart, tempo_factor).commands
        if item.command.get("pitch") in {72, 74}
    ]


def test_commands_land_on_the_first_frame_at_or_after_their_audible_time() -> None:
    # At 120 bpm a beat is 0.5 s, so beats 0, 1, 3 are 0 s, 0.5 s and 1.5 s: frames 0, 12.5 and
    # 37.5 of 40 ms, which round up to 0, 13 and 38.
    assert melody_frames(short_chart()) == [
        (0, "noteon", 72),
        (13, "noteoff", 72),
        (13, "noteon", 74),
        (38, "noteoff", 74),
    ]


def test_render_covers_the_whole_chart_and_releases_every_chord_note() -> None:
    result = plan(short_chart())
    assert result.frames == 50
    assert result.seconds == 2.0
    assert result.commands[0].frame == 0
    assert any(item.frame == 0 and item.command == {"cmd": "play"} for item in result.commands)
    opened = {
        item.command["pitch"]
        for item in result.commands
        if item.command["cmd"] == "noteon" and item.frame == 0
    } - {72}
    closed = {
        item.command["pitch"]
        for item in result.commands
        if item.command["cmd"] == "noteoff" and item.frame == 50
    }
    assert opened
    assert closed <= opened
    active: set[object] = set()
    for item in result.commands:
        if item.command["cmd"] == "noteon":
            active.add(item.command["pitch"])
        elif item.command["cmd"] == "noteoff":
            active.remove(item.command["pitch"])
    assert not active
    assert result.commands[-1].time_s == 2.0


def test_tempo_factor_shortens_the_render_without_any_waiting() -> None:
    # Twice the tempo halves the beat to 0.25 s: beats 1 and 3 are 0.25 s and 0.75 s, which are
    # frames 6.25 and 18.75, and the 4 beats end at 1 s, frame 25.
    result = plan(short_chart(), 2.0)
    assert result.frames == 25
    assert melody_frames(short_chart(), 2.0) == [
        (0, "noteon", 72),
        (7, "noteoff", 72),
        (7, "noteon", 74),
        (19, "noteoff", 74),
    ]


def test_offline_uses_shipped_pulse_guidance_and_smoothed_prompt_blend() -> None:
    result = plan(short_chart(), guidance=Guidance(cfg_notes=10), sweep=True)
    assert result.commands[0].command == {"cmd": "onset_mode", "mode": 1}
    assert result.commands[1].command == {
        "cmd": "params",
        "cfg_notes": 10.0,
        "cfg_musiccoca": 3.0,
        "temperature": 1.0,
    }
    bass = [item for item in result.commands if item.command == {"cmd": "noteon", "pitch": 36}]
    assert [item.frame for item in bass] == [0, 13, 25, 38]
    blends = [item for item in result.commands if item.command["cmd"] == "blend"]
    assert blends[0].command["weights"] == [1, 0, 0, 0, 0, 0]
    assert blends[-1].command != blends[0].command
    assert result.beat_audible == ((0, 0), (1, 0.5), (2, 1), (3, 1.5), (4, 2))


def test_offline_take_uses_scheduler_log_and_cannot_pass_live_gate(tmp_path: Path) -> None:
    output = tmp_path / "take.wav"
    engine = OfflineEngine(tmp_path / "engine", tmp_path, "folk", preset="Folk / metal")
    path = write_take(output, plan(short_chart()), engine, Path("chart.json"), 2)
    take = TakeFile.model_validate_json(path.read_text())
    assert take.mode == "offline"
    assert take.dropped_frames == 0
    assert take.play_time_s == 0
    assert take.beat_audible == [(0, 0), (1, 0.5), (2, 1), (3, 1.5), (4, 2)]
    with pytest.raises(ValueError, match="offline"):
        validate_protocol([take])


@pytest.mark.parametrize("factor", [0, -1, float("nan"), float("inf")])
def test_plan_rejects_unusable_tempo_factors(factor: float) -> None:
    with pytest.raises(ValueError, match="Tempo factor"):
        plan(short_chart(), factor)


def test_a_chart_too_long_for_one_wav_is_rejected() -> None:
    # MAX_FRAMES is 279 620 frames, 11 184.8 s; at 120 bpm that is a little over 22 369 beats.
    assert MAX_FRAMES == 279_620
    with pytest.raises(ValueError, match="WAV limit"):
        plan(short_chart(duration_beats=22_400))


def test_engine_arguments_use_the_offline_flags_the_engine_accepts() -> None:
    assert engine_arguments(
        Path("/bin/engine"),
        50,
        Path("/out/take.wav"),
        Path("/models"),
        "a jazz piano trio",
        commands=Path("/out/take.wav.json"),
        model="mrt2_base",
    ) == [
        "/bin/engine",
        "--offline",
        "--frames",
        "50",
        "--prompt",
        "a jazz piano trio",
        "--out",
        "/out/take.wav",
        "--model-dir",
        "/models",
        "--model",
        "mrt2_base",
        "--commands",
        "/out/take.wav.json",
    ]


@pytest.mark.parametrize(("frames", "prompt"), [(0, "x"), (MAX_FRAMES + 1, "x"), (5, "  ")])
def test_engine_arguments_reject_values_the_engine_would_refuse(frames: int, prompt: str) -> None:
    with pytest.raises(ValueError, match=r"Frames|Prompt"):
        engine_arguments(
            Path("e"),
            frames,
            Path("o.wav"),
            Path("m"),
            prompt,
            commands=Path("o.wav.json"),
            model="mrt2_small",
        )


def test_render_chart_runs_the_engine_once_with_the_planned_frame_count(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.write_text("")
    calls: list[list[str]] = []

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    result = render_chart(
        short_chart(),
        tmp_path / "take.wav",
        OfflineEngine(
            engine,
            tmp_path,
            "a quartet",
            model="mrt2_base",
            preset="Folk / metal",
            prompt_b="metal",
            seed_rotation=2,
        ),
        run=run,
    )
    assert result.frames == 50
    sidecar = tmp_path / "take.wav.json"
    assert calls == [
        [
            str(engine),
            "--offline",
            "--frames",
            "50",
            "--prompt",
            "a quartet",
            "--out",
            str(tmp_path / "take.wav"),
            "--model-dir",
            str(tmp_path),
            "--model",
            "mrt2_base",
            "--commands",
            str(sidecar),
        ]
    ]
    written = json.loads(sidecar.read_text())
    assert written["mode"] == "offline"
    assert written["model"] == "mrt2_base"
    assert written["preset"] == "Folk / metal"
    assert written["frames"] == 50
    assert written["seconds"] == 2.0
    assert written["tempo_factor"] == 1.0
    assert written["seed_rotation"] == 2
    assert [item["command"]["text"] for item in written["commands"][:2]] == ["a quartet", "metal"]
    assert [item["command"]["cmd"] for item in written["commands"]] == [
        "prompt",
        "prompt",
        *[
            item.command["cmd"]
            for item in result.commands
            if item.command["cmd"] != "play" and item.frame < result.frames
        ],
    ]
    # The first frame holds the downbeat's notes, so the engine can sound them in frame 0.
    assert written["commands"][0]["frame"] == 0
    assert written["commands"][0]["time_s"] == 0


def test_render_chart_reports_the_engine_failure_and_missing_files(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.write_text("")

    def failing(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(arguments, 1, "", "ghostband-engine: no model\nUsage")

    def timing_out(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(arguments, 1)

    def render(*, output: Path, engine_path: Path, run: Run) -> Render:
        return render_chart(
            short_chart(), output, OfflineEngine(engine_path, tmp_path, "x"), run=run
        )

    with pytest.raises(GhostbandError, match="no model") as failed:
        render(output=tmp_path / "a.wav", engine_path=engine, run=failing)
    assert failed.value.code == ErrorCode.ENGINE_FAILED
    with pytest.raises(GhostbandError, match="timed out"):
        render(output=tmp_path / "a.wav", engine_path=engine, run=timing_out)
    with pytest.raises(GhostbandError, match="not a folder") as no_folder:
        render(output=tmp_path / "missing" / "a.wav", engine_path=engine, run=failing)
    assert no_folder.value.code == ErrorCode.IO_FAILED
    with pytest.raises(GhostbandError, match="engine not found") as no_engine:
        render(output=tmp_path / "a.wav", engine_path=tmp_path / "none", run=failing)
    assert no_engine.value.code == ErrorCode.UNAVAILABLE
