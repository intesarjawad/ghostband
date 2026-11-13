"""Tempo measurement scores detected beats against the audible chart beats per factor."""

import csv
import json
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest
from ghostband.chart import Chart
from ghostband.importing import read_chart
from pydantic import ValidationError

from scripts import measure_tempo
from scripts.measure_sync import ROOT, Timeline
from scripts.measure_tempo import (
    FACTORS,
    RENDERS,
    FactorResult,
    TakeFile,
    Unit,
    beat_f_measure,
    missing_cells,
    output_bpm,
    reference_beats,
    render_positions,
    render_report,
    score_take,
    summarise,
    tempo_range,
    write_claims,
    write_csv,
)

sf = import_module("soundfile")
PRESET = "Warm jazz / chiptune"
# A render in which the tracker finds every counted beat of the four-beat test chart.
EVERY_BEAT = np.arange(5.0)


def test_reference_beats_are_the_render_positions_the_log_covers() -> None:
    timeline = Timeline.from_log([(0.0, 11.0), (4.0, 13.0)], play_time_s=10.0)
    positions = np.array([0.5, 2.5, 4.5])
    assert reference_beats(timeline, positions).tolist() == [1.25, 2.25]


def test_output_tempo_is_the_median_beat_gap() -> None:
    assert output_bpm(np.array([1.0, 1.5, 2.0, 2.5])) == pytest.approx(120.0)
    assert output_bpm(np.array([0.0, 0.5, 1.0, 2.0])) == pytest.approx(120.0)
    assert output_bpm(np.array([1.0])) is None


def test_the_f_measure_uses_a_70_ms_window() -> None:
    reference = np.array([1.0, 2.0, 3.0, 4.0])
    # Three of four land inside 70 ms; 3.2 s is 200 ms late, so precision and recall are 0.75.
    detected = np.array([1.02, 2.0, 3.2, 4.06])
    assert beat_f_measure(reference, detected) == pytest.approx(0.75)
    assert beat_f_measure(reference, np.array([])) == 0.0


def chart_json(tempo: float) -> str:
    return Chart.model_validate(
        {
            "schema_version": 1,
            "title": "Test",
            "source": "test",
            "rights": "own-work",
            "extractor": "manual",
            "tempo_bpm": tempo,
            "beat_times_s": [],
            "time_signature": [4, 4],
            "key": "C",
            "sections": [{"name": "A", "start_beat": 0, "end_beat": 4}],
            "chords": [{"start_beat": 0, "end_beat": 4, "symbol": "C"}],
            "melody": [],
            "duration_beats": 4,
        }
    ).model_dump_json()


def write_recording(folder: Path, seconds: float) -> None:
    rate = 8000
    sf.write(folder / "take.wav", np.zeros(round(seconds * rate)), rate)


def take(chart: Path, factor: float) -> TakeFile:
    return TakeFile(
        chart=str(chart),
        preset=PRESET,
        onset_mode=0,
        tempo_factor=factor,
        run=1,
        recording="take.wav",
        play_time_s=10.0,
        beat_audible=[(0.0, 11.0), (4.0, 13.0)],
    )


def test_a_take_ignores_detected_beats_outside_the_logged_span(tmp_path: Path) -> None:
    chart = tmp_path / "test.chart.json"
    chart.write_text(chart_json(120), encoding="utf-8")
    write_recording(tmp_path, 4.0)
    seen: list[Path] = []

    def detect(recording: Path) -> list[float]:
        seen.append(recording)
        # 0.2 and 5.0 lie outside the audible span 1.0 to 3.0; 2.01 is 10 ms late.
        return [5.0, 0.2, 1.0, 1.5, 2.01, 2.5, 3.0]

    unit = score_take(take(chart, 1.3), tmp_path, EVERY_BEAT, detect)
    assert seen == [tmp_path / "take.wav"]
    assert (unit.chart, unit.preset, unit.onset_mode, unit.factor, unit.run) == (
        "test",
        PRESET,
        0,
        1.3,
        1,
    )
    assert (unit.reference_beats, unit.detected_beats) == (5, 5)
    assert unit.f_measure == 1.0
    # Gaps are 0.5, 0.51, 0.49 and 0.5 s: the median gap is 0.5 s, 120 bpm.
    assert unit.output_bpm == pytest.approx(120.0)
    assert unit.expected_bpm == pytest.approx(156.0)


def test_a_take_with_no_detected_beats_scores_zero(tmp_path: Path) -> None:
    chart = tmp_path / "test.chart.json"
    chart.write_text(chart_json(120), encoding="utf-8")
    write_recording(tmp_path, 4.0)
    unit = score_take(take(chart, 1.0), tmp_path, EVERY_BEAT, lambda _: [9.0])
    assert (unit.f_measure, unit.detected_beats, unit.output_bpm) == (0.0, 0, None)


def test_beats_after_the_recording_ends_are_not_scored(tmp_path: Path) -> None:
    chart = tmp_path / "test.chart.json"
    chart.write_text(chart_json(120), encoding="utf-8")
    # The take stopped at 2.8 s, before the last counted beat at 3.0 s sounded.
    write_recording(tmp_path, 2.8)
    unit = score_take(take(chart, 1.0), tmp_path, EVERY_BEAT, lambda _: [1.0, 1.5, 2.0, 2.5])
    assert (unit.reference_beats, unit.detected_beats, unit.f_measure) == (4, 4, 1.0)


def test_a_render_scores_one_against_itself_at_the_level_the_tracker_reads(
    tmp_path: Path,
) -> None:
    chart = tmp_path / "test.chart.json"
    chart.write_text(chart_json(120), encoding="utf-8")
    write_recording(tmp_path, 2.1)
    # The tracker hears the 120 bpm render in half notes, offset by one beat.
    render_beats = [0.5, 1.5]
    positions = render_positions(
        Chart.model_validate_json(chart_json(120)), tmp_path / "take.wav", lambda _: render_beats
    )
    assert positions.tolist() == [1.0, 3.0]
    render_as_take = take(chart, 1.0).model_copy(
        update={"play_time_s": 0.0, "beat_audible": [(0.0, 0.0), (4.0, 2.0)]}
    )
    unit = score_take(render_as_take, tmp_path, positions, lambda _: render_beats)
    assert unit.f_measure == 1.0
    assert unit.output_bpm == pytest.approx(60.0)
    assert unit.expected_bpm == pytest.approx(60.0)


def test_a_render_without_beats_is_reported() -> None:
    chart = Chart.model_validate_json(chart_json(120))
    with pytest.raises(ValueError, match="make fixtures"):
        render_positions(chart, Path("render.wav"), lambda _: [1.0])


def test_a_sidecar_with_an_untested_factor_is_rejected() -> None:
    fields = {
        "chart": "a.chart.json",
        "preset": PRESET,
        "onset_mode": 1,
        "run": 1,
        "recording": "a.wav",
        "play_time_s": 0.0,
        "beat_audible": [[0, 1], [1, 2]],
    }
    assert TakeFile.model_validate({**fields, "tempo_factor": 0.7}).onset_mode == 1
    with pytest.raises(ValidationError, match="one of"):
        TakeFile.model_validate({**fields, "tempo_factor": 0.8})
    with pytest.raises(ValidationError, match="onset_mode"):
        TakeFile.model_validate({**fields, "tempo_factor": 1.0, "onset_mode": 2})


def unit(
    mode: int, factor: float, score: tuple[float, float], run: int, chart: str = "song"
) -> Unit:
    f, ratio = score
    expected = 100 * factor
    return Unit(chart, PRESET, mode, factor, run, f, 16, 16, expected * ratio, expected)


def mode_zero_units() -> list[Unit]:
    # 0.5 fails on the F-measure, 1.5 fails on tempo: its output is 20% slow.
    cases = {0.5: (0.3, 1.0), 0.7: (0.9, 1.0), 1.0: (0.9, 1.0), 1.3: (0.9, 1.02), 1.5: (0.9, 0.8)}
    return [unit(0, factor, score, run) for factor, score in cases.items() for run in (1, 2, 3)]


def test_each_factor_passes_on_beats_and_tempo_in_each_onset_mode() -> None:
    results = summarise([*mode_zero_units(), *(unit(1, 1.0, (0.95, 1.0), run) for run in (1, 2))])
    by_key = {(result.onset_mode, result.factor): result for result in results}
    assert [(m, f) for m, f in by_key if by_key[(m, f)].passed] == [
        (0, 0.7),
        (0, 1.0),
        (0, 1.3),
    ]
    assert by_key[(0, 1.3)].tempo_error == pytest.approx(0.02)
    assert by_key[(0, 1.5)].tempo_error == pytest.approx(0.2)
    assert by_key[(0, 0.5)].f_measure == pytest.approx((0.3, 0.3, 0.3))
    # Mode 1 holds 1.0 on its numbers, but one run is missing, and its other factors have none.
    assert by_key[(1, 1.0)].units == 2
    assert not by_key[(1, 1.0)].complete
    assert not by_key[(1, 1.0)].passed
    assert by_key[(1, 0.5)].f_measure is None


def test_missing_runs_are_listed_per_mode_chart_and_factor() -> None:
    units = [unit(0, factor, (0.9, 1.0), run) for factor in FACTORS for run in (1, 2, 3)]
    units.extend(unit(1, factor, (0.9, 1.0), run) for factor in FACTORS for run in (1, 2, 3))
    assert missing_cells(units) == []
    units.pop()
    units.extend(unit(0, 1.0, (0.9, 1.0), run, chart="other") for run in (1, 2))
    assert missing_cells(units) == [
        (0, "other", 0.5, 0),
        (0, "other", 0.7, 0),
        (0, "other", 1.0, 2),
        (0, "other", 1.3, 0),
        (0, "other", 1.5, 0),
        (1, "other", 0.5, 0),
        (1, "other", 0.7, 0),
        (1, "other", 1.0, 0),
        (1, "other", 1.3, 0),
        (1, "other", 1.5, 0),
        (1, "song", 1.5, 2),
    ]


def test_the_range_is_the_consecutive_passing_run_around_one() -> None:
    assert tempo_range([0.5, 0.7, 1.0, 1.3, 1.5]) == (0.5, 1.5)
    assert tempo_range([0.7, 1.0, 1.3]) == (0.7, 1.3)
    # 0.5 passes but is cut off from 1.0 by a failing 0.7.
    assert tempo_range([0.5, 1.0, 1.3, 1.5]) == (1.0, 1.5)
    assert tempo_range([1.0]) == (1.0, 1.0)
    assert tempo_range([0.7, 1.3]) is None


def test_claims_keep_other_entries_and_name_the_shipping_mode(tmp_path: Path) -> None:
    results = summarise([*mode_zero_units()])
    path = tmp_path / "docs" / "claims.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"melody_survives": True}), encoding="utf-8")
    assert write_claims(path, results, shipping_mode=0) == (0.7, 1.3)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "melody_survives": True,
        "tempo_factors_pass": [0.7, 1.0, 1.3],
        "tempo_range": [0.7, 1.3],
    }
    # Mode 1 has no takes here, so nothing passes and no range exists.
    assert write_claims(path, results, shipping_mode=1) is None
    assert json.loads(path.read_text(encoding="utf-8"))["tempo_range"] is None
    assert json.loads(path.read_text(encoding="utf-8"))["tempo_factors_pass"] == []


def test_the_report_states_the_range_and_the_csv_has_a_row_per_take(tmp_path: Path) -> None:
    units = mode_zero_units()
    report = render_report(units, summarise(units), 0, PRESET)
    lines = report.splitlines()
    assert lines[0] == f"Preset: {PRESET}. Shipping onset mode: 0."
    assert lines[1] == "Passing factors: 0.7, 1, 1.3."
    assert lines[2] == "Tempo range: 0.7 to 1.3, narrower than the 0.5 to 1.5 offered so far."
    assert "| 0 | 1.5 | 3 | 0.900 (0.900 to 0.900) | 1/1 | 20.0% | yes | fail |" in lines
    assert "| song | 0 | 1.3 | 3 | 130.0 | 132.6 | 0.900 |" in lines
    path = tmp_path / "tempo.csv"
    write_csv(path, units)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 15
    assert rows[0] == {
        "chart": "song",
        "preset": PRESET,
        "onset_mode": "0",
        "tempo_factor": "0.5",
        "run": "1",
        "f_measure": "0.300000",
        "reference_beats": "16",
        "detected_beats": "16",
        "output_bpm": "50.000",
        "expected_bpm": "50.000",
    }


def test_a_factor_result_needs_every_part_to_pass() -> None:
    charts = (("song", 0.8),)
    good = FactorResult(0, 1.0, 3, True, (0.8, 0.7, 0.9), 0.05, charts)
    assert good.passed
    assert not FactorResult(0, 1.0, 3, True, (0.8, 0.7, 0.9), 0.05, (("song", 0.79),)).passed
    assert not FactorResult(0, 1.0, 3, True, (0.8, 0.7, 0.9), 0.06, charts).passed
    assert not FactorResult(0, 1.0, 3, True, (0.8, 0.7, 0.9), None, charts).passed
    assert not FactorResult(0, 1.0, 3, False, (0.8, 0.7, 0.9), 0.0, charts).passed
    # Without per-chart data there is no evidence that every chart reached the floor.
    assert not FactorResult(0, 1.0, 3, True, (0.8, 0.7, 0.9), 0.0, ()).passed


def test_one_chart_below_the_floor_fails_even_when_the_pool_is_above_it() -> None:
    units = [unit(0, 1.0, (1.0, 1.0), run, chart="song-a") for run in range(1, 10)]
    units += [unit(0, 1.0, (0.5, 1.0), run, chart="song-b") for run in range(1, 4)]
    result = next(
        factor for factor in summarise(units) if (factor.onset_mode, factor.factor) == (0, 1.0)
    )
    assert result.f_measure is not None
    assert result.f_measure[0] == pytest.approx(0.875)
    assert dict(result.chart_f_measure) == {"song-a": 1.0, "song-b": 0.5}
    assert not result.passed


@pytest.mark.parametrize(
    ("stderr", "beats"),
    [("Two beats and two downbeats are required to infer tempo and meter", []), ("crash", None)],
)
def test_a_take_without_a_meter_has_no_beats_but_other_failures_stop(
    stderr: str, beats: list[float] | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def extractor(_command: str, _recording: Path) -> dict[str, object]:
        raise RuntimeError(f"the permissive extractor exited with status 1: {stderr}")

    monkeypatch.setattr(measure_tempo, "run_permissive", extractor)
    if beats is None:
        with pytest.raises(RuntimeError, match="crash"):
            measure_tempo.detect_beats(tmp_path / "take.wav")
    else:
        assert measure_tempo.detect_beats(tmp_path / "take.wav") == beats


@pytest.mark.models
@pytest.mark.parametrize("name", [path.stem for path in sorted(RENDERS.glob("*.wav"))])
def test_each_bundled_render_scores_one_against_itself(name: str) -> None:
    chart = read_chart(ROOT / "charts" / f"{name}.chart.json")
    render = RENDERS / f"{name}.wav"
    seconds_per_beat = 60 / chart.tempo_bpm
    render_as_take = TakeFile(
        chart=f"charts/{name}.chart.json",
        preset=PRESET,
        onset_mode=1,
        tempo_factor=1.0,
        run=1,
        recording=render.name,
        play_time_s=0.0,
        beat_audible=[(0.0, 0.0), (chart.duration_beats, chart.duration_beats * seconds_per_beat)],
    )
    unit = score_take(render_as_take, RENDERS, render_positions(chart, render))
    assert unit.f_measure == 1.0
    assert unit.output_bpm == pytest.approx(unit.expected_bpm)
