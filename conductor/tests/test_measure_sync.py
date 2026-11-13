"""Sync measurement scores recorded takes against the chart and a shifted-chart null."""

import json
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest
from ghostband.chart import Chart, Section
from ghostband.presets import PRESETS
from numpy.typing import NDArray

from scripts.measure_sync import (
    ROOT,
    Change,
    PresetResult,
    Score,
    TakeFile,
    Timeline,
    Unit,
    bootstrap_mean,
    chord_changes,
    detector_ceiling,
    main,
    pooled,
    render_report,
    score_take,
    summarise,
    validate_protocol,
    write_claims,
)

sf = import_module("soundfile")
RATE = 22_050
FREQUENCY = {
    "C": (261.63, 329.63, 392.0),
    "G": (196.0, 246.94, 293.66),
    "Am": (220.0, 261.63, 329.63),
}
FREQUENCY["F"] = (174.61, 220.0, 261.63)
PRE_ROLL_S = 1.0
BEAT_S = 0.5


def chart(chords: Sequence[tuple[float, float, str]]) -> Chart:
    end = chords[-1][1]
    return Chart.model_validate(
        {
            "schema_version": 1,
            "title": "Test",
            "source": "test",
            "rights": "own-work",
            "extractor": "manual",
            "tempo_bpm": 120,
            "beat_times_s": [],
            "time_signature": [4, 4],
            "key": "C",
            "sections": [{"name": "A", "start_beat": 0, "end_beat": end}],
            "chords": [
                {"start_beat": start, "end_beat": stop, "symbol": symbol}
                for start, stop, symbol in chords
            ],
            "melody": [],
            "duration_beats": end,
        }
    )


def tone(frequencies: tuple[float, ...], seconds: float) -> NDArray[np.float64]:
    time = np.arange(round(seconds * RATE), dtype=np.float64) / RATE
    wave = np.zeros_like(time)
    for hz in frequencies:
        wave += np.sin(2 * np.pi * hz * time)
    return 0.2 * wave / len(frequencies)


def test_the_timeline_maps_beats_to_recording_seconds() -> None:
    timeline = Timeline.from_log([(4.0, 13.0), (2.0, 12.0), (2.0, 12.0)], play_time_s=10.0)
    assert timeline.at(2.0) == 2.0
    assert timeline.at(3.0) == 2.5
    assert timeline.at(1.9) is None
    assert timeline.at(4.1) is None
    with pytest.raises(ValueError, match="forward"):
        Timeline.from_log([(0.0, 5.0), (1.0, 4.0)], play_time_s=0.0)


def test_changes_skip_shared_triads_and_silence() -> None:
    result = chord_changes(
        chart(
            [
                (0, 4, "C"),
                (4, 8, "G"),
                (8, 10, "G7"),
                (10, 12, "Em"),
                (12, 14, "Gmaj7"),
                (14, 16, "N.C."),
            ]
        )
    )
    assert result.items == (
        Change(4, "C", "G"),
        Change(10, "G", "Em"),
        Change(12, "Em", "G"),
    )
    assert (result.shared, result.no_chord) == (1, 1)


def test_the_bootstrap_interval_is_deterministic_and_bounded() -> None:
    assert bootstrap_mean([0.25] * 6) == (0.25, 0.25, 0.25)
    mean, low, high = bootstrap_mean([0.0, 1.0, 1.0, 1.0])
    assert mean == 0.75
    assert 0.0 <= low <= 0.75 <= high <= 1.0
    assert bootstrap_mean([0.0, 1.0, 1.0, 1.0]) == (mean, low, high)
    with pytest.raises(ValueError, match="unit"):
        bootstrap_mean([])


def test_a_take_lands_on_the_chart_and_beats_the_shifted_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = [
        (0, 4, "C"),
        (4, 8, "G"),
        (8, 10, "G7"),
        (10, 14, "Am"),
        (14, 18, "F"),
        (18, 20, "N.C."),
    ]
    sounding = [("C", 2.0), ("G", 3.0), ("Am", 2.0), ("F", 2.0), (None, 1.0)]
    audio = [np.zeros(round(PRE_ROLL_S * RATE))]
    audio.extend(
        tone(FREQUENCY[name], seconds) if name else np.zeros(RATE) for name, seconds in sounding
    )
    sf.write(
        str(tmp_path / "take.wav"), np.concatenate(audio), RATE, subtype="PCM_16", format="WAV"
    )
    chart_path = tmp_path / "test.chart.json"
    chart_path.write_text(chart(layout).model_dump_json(), encoding="utf-8")
    (tmp_path / "a.take.json").write_text(
        json.dumps(
            {
                "chart": str(chart_path),
                "preset": "jazz",
                "run": 1,
                "recording": "take.wav",
                "play_time_s": 40.0,
                # The first note is audible one second after play, as the scheduler's pre-roll.
                "beat_audible": [[0, 41.0], [20, 51.0]],
                "dropped_frames": 0,
            }
        ),
        encoding="utf-8",
    )
    unit = score_take(tmp_path / "a.take.json")
    assert (unit.chart, unit.preset, unit.run) == ("test", "jazz", 1)
    assert (unit.shared, unit.no_chord) == (1, 1)
    assert unit.real.eligible == 3
    assert unit.real.misses == 0
    assert unit.real.within(0.12) == 1.0
    # A half bar later the chart is mid-chord, where the new triad has long led.
    assert unit.null.within(0.12) == 0.0
    # Beats 0 to 17 carry a chart triad; beats 18 and 19 are N.C. and not compared.
    assert unit.heard_compared == 18
    assert unit.heard_agreed == 18
    assert max(abs(error) for error in unit.real.errors_s) < 0.06
    claims = tmp_path / "claims.json"
    claims.write_text('{"existing": true}\n')
    report = tmp_path / "report.md"
    arguments = ["measure_sync", str(tmp_path), "--claims", str(claims), "--report", str(report)]
    monkeypatch.setattr("sys.argv", [*arguments, "--diagnostic"])
    assert main() == 0
    assert report.read_text().startswith("Diagnostic only:")
    assert claims.read_text() == '{"existing": true}\n'
    monkeypatch.setattr("sys.argv", arguments)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert claims.read_text() == '{"existing": true}\n'


@pytest.mark.parametrize("offset_s", [-0.2, -0.1, 0.0, 0.08, 0.2, 0.4, 0.7])
def test_recorded_changes_recover_known_signed_offsets(tmp_path: Path, offset_s: float) -> None:
    chart_path = tmp_path / "offset.chart.json"
    chart_path.write_text(chart([(0, 4, "C"), (4, 8, "G")]).model_dump_json())
    transition_s = PRE_ROLL_S + 4 * BEAT_S + offset_s
    audio = np.concatenate(
        [tone(FREQUENCY["C"], transition_s), tone(FREQUENCY["G"], 6 - transition_s)]
    )
    sf.write(str(tmp_path / "offset.wav"), audio, RATE)
    sidecar = tmp_path / "offset.take.json"
    sidecar.write_text(
        json.dumps(
            {
                "chart": str(chart_path),
                "preset": "synthetic",
                "run": 1,
                "recording": "offset.wav",
                "play_time_s": 40.0,
                "beat_audible": [[0, 41.0], [8, 45.0]],
                "dropped_frames": 0,
            }
        )
    )
    result = score_take(sidecar)
    assert result.real.eligible == 1
    if offset_s > 0.5:
        assert result.real.misses == 1
    else:
        assert result.real.errors_s == pytest.approx((offset_s,), abs=0.03)
        assert result.real.within(0.12) == (abs(offset_s) <= 0.12)
    assert result.null.within(0.12) == 0.0


def test_a_gapped_take_is_rejected_before_its_audio_is_scored(tmp_path: Path) -> None:
    sidecar = tmp_path / "gapped.take.json"
    sidecar.write_text(
        json.dumps(
            {
                "chart": "missing.chart.json",
                "preset": "jazz",
                "run": 1,
                "recording": "missing.wav",
                "play_time_s": 0.0,
                "beat_audible": [[0, 0.2], [4, 2.2]],
                "dropped_frames": 1,
            }
        )
    )
    with pytest.raises(ValueError, match="dropped 1 frames"):
        score_take(sidecar)


def test_the_gate_requires_the_complete_verified_protocol() -> None:
    takes = [
        TakeFile(
            chart=str(path),
            preset=preset.name,
            run=run,
            recording="take.wav",
            play_time_s=0,
            beat_audible=[(0, 0.2), (4, 2.2)],
            dropped_frames=0,
        )
        for path in sorted((ROOT / "charts").glob("*.chart.json"))
        for preset in PRESETS
        for run in (1, 2, 3)
    ]
    validate_protocol(takes)
    with pytest.raises(ValueError, match="incomplete"):
        validate_protocol(takes[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        validate_protocol([*takes, takes[0]])
    with pytest.raises(ValueError, match="dropped_frames"):
        validate_protocol([takes[0].model_copy(update={"dropped_frames": None}), *takes[1:]])


def test_after_a_pickup_heard_beats_are_counted_from_the_bar_line(tmp_path: Path) -> None:
    # Bars start on the half beat, so every chord change falls on n + 0.5.
    pickup = chart([(0, 0.5, "C"), (0.5, 4.5, "C"), (4.5, 9, "G")]).model_copy(
        update={
            "sections": [
                Section(name="Pickup", start_beat=0, end_beat=0.5),
                Section(name="A", start_beat=0.5, end_beat=9),
            ]
        }
    )
    audio = [
        np.zeros(round(PRE_ROLL_S * RATE)),
        tone(FREQUENCY["C"], 2.0),
        tone(FREQUENCY["G"], 2.5),
    ]
    sf.write(
        str(tmp_path / "take.wav"), np.concatenate(audio), RATE, subtype="PCM_16", format="WAV"
    )
    chart_path = tmp_path / "test.chart.json"
    chart_path.write_text(pickup.model_dump_json(), encoding="utf-8")
    (tmp_path / "a.take.json").write_text(
        json.dumps(
            {
                "chart": str(chart_path),
                "preset": "jazz",
                "run": 1,
                "recording": "take.wav",
                "play_time_s": 40.0,
                "beat_audible": [[0.5, 41.0], [8.5, 45.0]],
            }
        ),
        encoding="utf-8",
    )
    unit = score_take(tmp_path / "a.take.json")
    # Beats 0.5 to 7.5 each hold one chord; counted from 1, beat 4 would hold both.
    assert (unit.heard_agreed, unit.heard_compared) == (8, 8)


def score(errors: tuple[float, ...], misses: int = 0) -> Score:
    return Score(errors, misses, 0)


def unit(preset: str, run: int, real: Score, null: Score, heard: tuple[int, int]) -> Unit:
    return Unit("song", preset, run, real, null, 0, 0, heard[0], heard[1])


def test_a_preset_passes_only_when_the_interval_clears_zero() -> None:
    good = [
        unit("jazz", run, score((0.01, -0.03, 0.05, 0.02)), score((0.2, 0.3), 2), (9, 10))
        for run in (1, 2, 3)
    ]
    # Same rates as the real takes: the paired difference is zero, so it fails.
    flat = [
        unit(
            "funk", run, score((0.01, -0.03, 0.05, 0.02)), score((0.01, 0.02, 0.03, 0.04)), (5, 10)
        )
        for run in (1, 2, 3)
    ]
    funk, jazz = summarise([*good, *flat])
    assert jazz.preset == "jazz"
    assert jazz.real_within_tolerance == 1.0
    assert jazz.real_within_target == 1.0
    assert jazz.null_within_tolerance == 0.0
    assert jazz.median_ms == pytest.approx(25.0)
    assert jazz.difference == (1.0, 1.0, 1.0)
    assert jazz.heard == pytest.approx((0.9, 0.9, 0.9))
    assert jazz.passed
    assert funk.difference == (0.0, 0.0, 0.0)
    assert not funk.passed


def test_misses_count_against_the_rate_and_the_floor() -> None:
    takes = [
        unit("jazz", run, score((0.01,) * 8, misses=2), score((0.3,), 1), (1, 1)) for run in (1, 2)
    ]
    (result,) = summarise(takes)
    assert result.real_within_tolerance == 0.8
    assert not result.passed


def test_the_median_must_be_strictly_below_60_ms() -> None:
    result = PresetResult("jazz", 15, 1.0, 1.0, 0.0, 60.0, (1.0, 0.9, 1.0), (0.8, 0.7, 0.9))
    assert not result.passed


def test_claims_are_merged_into_the_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "claims.json"
    path.write_text(json.dumps({"tempo": {"bpm": 1}}), encoding="utf-8")
    result = PresetResult("jazz", 3, 1.0, 1.0, 0.0, 10.0, (1.0, 0.9, 1.0), (0.8, 0.7, 0.9))
    write_claims(path, [result])
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "tempo": {"bpm": 1},
        "heard_agreement": {"jazz": {"rate": 0.8, "low": 0.7, "high": 0.9, "takes": 3}},
        "sync": {
            "jazz": {
                "evidence": "zero_drop_corpus",
                "passed": True,
                "within_120_ms": 1.0,
                "median_error_ms": 10.0,
                "null_within_120_ms": 0.0,
                "difference": [1.0, 0.9, 1.0],
                "takes": 3,
            }
        },
    }


def test_the_report_has_a_row_per_chart_and_preset() -> None:
    takes = [unit("jazz", 1, score((0.01, 0.2), 1), score((0.3,)), (1, 2))]
    report = render_report(takes, summarise(takes))
    assert "| song | jazz | 1 | 3 | 0 / 0 / 0 | 1 | 105 | 33.3% | 33.3% | 0.0% |" in report
    assert "| jazz | 1 | 105 | 33.3% | 33.3% | 0.0% |" in report


def test_fluidsynth_detector_ceiling_is_computed_from_all_five_fixtures() -> None:
    scores = detector_ceiling()
    assert len(scores) == 5
    assert sum(score.eligible for score in scores) == 56
    assert sum(score.misses for score in scores) == 3
    assert pooled(scores, 0.12) == pytest.approx(51 / 56)
    assert "| Detector ceiling (FluidSynth) | 56 | 3 | 20 | 91.1% |" in render_report(
        [], [], scores
    )
