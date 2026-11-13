"""Identity measurement scores harmony against the chart and melody against its notes."""

import json
import subprocess
import sys
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest
from ghostband.chart import Chart
from numpy.typing import NDArray

from scripts.measure_identity import (
    MelodyResult,
    Unit,
    beat_profile,
    ceiling_score,
    failing_presets,
    incomplete_cells,
    main,
    melody_f1,
    normalised_identity,
    pearson,
    read_take,
    score_take,
    summarise_identity,
    summarise_melody,
    write_claims,
)
from scripts.measure_sync import Timeline

sf = import_module("soundfile")
RATE = 22_050
C_MAJOR = (261.63, 329.63, 392.0)
F_SHARP_MAJOR = (185.0, 233.08, 277.18)
BEAT_S = 0.5


def chart(
    chords: Sequence[tuple[float, float, str]],
    melody: Sequence[tuple[float, float, int]] = (),
) -> Chart:
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
            "melody": [
                {"start_beat": start, "end_beat": stop, "pitch": pitch}
                for start, stop, pitch in melody
            ],
            "duration_beats": end,
        }
    )


def tone(frequencies: tuple[float, ...], seconds: float) -> NDArray[np.float64]:
    time = np.arange(round(seconds * RATE), dtype=np.float64) / RATE
    wave = np.zeros_like(time)
    for hz in frequencies:
        wave += np.sin(2 * np.pi * hz * time)
    return 0.2 * wave / len(frequencies)


def write_wav(path: Path, audio: NDArray[np.float64]) -> None:
    sf.write(str(path), audio, RATE, subtype="PCM_16", format="WAV")


def test_the_beat_profile_weights_chord_tones_and_melody_by_time_sounding() -> None:
    song = chart([(0, 2, "C"), (2, 4, "N.C.")], melody=[(0.5, 1.0, 62), (1.5, 3.0, 74)])
    expected = np.zeros(12)
    expected[[0, 4, 7]] = 1.0
    expected[2] = 0.5
    assert np.array_equal(beat_profile(song, 0), expected)
    # Beat 1: the C chord, and a D an octave above the first one for its second half.
    expected = np.zeros(12)
    expected[[0, 4, 7]] = 1.0
    expected[2] = 0.5
    assert np.array_equal(beat_profile(song, 1), expected)
    # Beat 2: N.C. leaves only the melody.
    expected = np.zeros(12)
    expected[2] = 1.0
    assert np.array_equal(beat_profile(song, 2), expected)
    assert not beat_profile(song, 3).any()


def test_pearson_centres_both_vectors_and_skips_flat_ones() -> None:
    left = np.array([1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    assert pearson(left, 3 * left + 2) == pytest.approx(1.0)
    assert pearson(left, -left) == pytest.approx(-1.0)
    # Two triads with no tone in common: dot product -3/4, norms 3/2 each.
    right = np.zeros(12)
    right[[1, 2, 5]] = 1.0
    assert pearson(left, right) == pytest.approx(-1 / 3)
    assert pearson(left, np.full(12, 0.4)) is None
    assert pearson(np.zeros(12), left) is None


def test_identity_is_the_share_of_the_gap_the_fed_notes_close() -> None:
    assert normalised_identity(0.6, 0.2, 0.8) == pytest.approx(2 / 3)
    assert normalised_identity(0.2, 0.2, 0.8) == 0.0
    assert normalised_identity(0.9, 0.5, 0.5) is None
    assert normalised_identity(0.9, 0.7, 0.5) is None


def test_melody_f1_matches_octave_blind_onsets_inside_the_window() -> None:
    song = chart([(0, 2, "C")], melody=[(0, 1, 60), (1, 2, 62)])
    timeline = Timeline.from_log([(0.0, 0.0), (2.0, 1.0)], 0.0)
    # The C an octave up lands 40 ms late; the D lands 120 ms late and misses.
    late = [(0.04, 0.5, 72), (0.62, 1.0, 74)]
    assert melody_f1(late, song, timeline) == pytest.approx(0.5)
    # The D now lands 90 ms late, inside the window; a wrong pitch class never matches.
    inside = [(0.04, 0.5, 72), (0.59, 1.0, 74)]
    assert melody_f1(inside, song, timeline) == pytest.approx(1.0)
    wrong = [(0.0, 0.5, 61), (0.5, 1.0, 63)]
    assert melody_f1(wrong, song, timeline) == 0.0
    assert melody_f1([], song, timeline) == 0.0
    assert melody_f1(inside, chart([(0, 2, "C")]), timeline) is None


def test_a_take_correlates_with_its_chart_and_the_fixture_sets_the_ceiling(tmp_path: Path) -> None:
    song = chart([(0, 8, "C")], melody=[(0, 1, 60)])
    chart_path = tmp_path / "test.chart.json"
    chart_path.write_text(song.model_dump_json(), encoding="utf-8")
    write_wav(tmp_path / "test.wav", tone(C_MAJOR, 4.0))
    write_wav(tmp_path / "match.wav", tone(C_MAJOR, 4.0))
    write_wav(tmp_path / "clash.wav", tone(F_SHARP_MAJOR, 4.0))
    sidecars = {"match": "chords_melody", "clash": "none"}
    for name, condition in sidecars.items():
        (tmp_path / f"{name}.take.json").write_text(
            json.dumps(
                {
                    "chart": str(chart_path),
                    "preset": "jazz",
                    "condition": condition,
                    "slider": 1,
                    "run": 2,
                    "recording": f"{name}.wav",
                    "play_time_s": 10.0,
                    "beat_audible": [[0, 10.0], [8, 14.0]],
                }
            ),
            encoding="utf-8",
        )
    heard: list[str] = []

    def transcribe(recording: Path) -> list[tuple[float, float, int]]:
        heard.append(recording.name)
        return [(0.0, 0.4, 72)]

    match = score_take(read_take(tmp_path / "match.take.json"), tmp_path, transcribe)
    clash = score_take(read_take(tmp_path / "clash.take.json"), tmp_path, transcribe)
    assert (match.chart, match.preset, match.condition, match.slider, match.run) == (
        "test",
        "jazz",
        "chords_melody",
        1,
        2,
    )
    assert match.harmony is not None
    assert match.harmony > 0.9
    assert clash.harmony is not None
    assert clash.harmony < 0.0
    assert match.melody_f1 == pytest.approx(1.0)
    # Takes fed no notes are never transcribed.
    assert clash.melody_f1 is None
    assert heard == ["match.wav"]
    ceiling = ceiling_score(str(chart_path), tmp_path)
    assert ceiling is not None
    assert ceiling == pytest.approx(match.harmony)


def test_a_sidecar_with_an_unknown_condition_is_rejected(tmp_path: Path) -> None:
    sidecar = tmp_path / "bad.take.json"
    sidecar.write_text(
        json.dumps(
            {
                "chart": "c.chart.json",
                "preset": "jazz",
                "condition": "melody",
                "slider": 0,
                "run": 1,
                "recording": "x.wav",
                "play_time_s": 0.0,
                "beat_audible": [[0, 0.0], [1, 1.0]],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="condition"):
        read_take(sidecar)


def take(
    preset: str, chart_name: str, condition: str, slider: int, harmony: Sequence[float]
) -> list[Unit]:
    return [
        Unit(chart_name, preset, condition, slider, run, score, None)
        for run, score in enumerate(harmony, start=1)
    ]


def harmony_units(preset: str, slider: int, fed: float, none: float) -> list[Unit]:
    units: list[Unit] = []
    for name, offset in (("song-a", -0.1), ("song-b", 0.1)):
        for condition, centre in (("chords_melody", fed), ("chords", none), ("none", none)):
            units.extend(
                take(
                    preset,
                    name,
                    condition,
                    slider,
                    [centre + offset, centre, centre - offset],
                )
            )
    return units


def test_identity_is_normalised_per_chart_then_pooled() -> None:
    # Run means: song-a and song-b both average 0.7 fed and 0.1 with no notes, so against a
    # ceiling of 0.9 each closes 0.6 / 0.8 = 0.75 of the gap.
    units = harmony_units("jazz", 0, fed=0.7, none=0.1)
    ceilings: dict[str, float | None] = {"song-a": 0.9, "song-b": 0.9}
    (result,) = summarise_identity(units, ceilings)
    assert result.charts == 2
    assert result.skipped == 0
    assert result.chords_melody == pytest.approx(0.7)
    assert result.chords == pytest.approx(0.1)
    assert result.none == pytest.approx(0.1)
    assert result.ceiling == pytest.approx(0.9)
    assert result.identity == pytest.approx((0.75, 0.75, 0.75))
    assert result.passed


def test_charts_without_a_usable_ceiling_are_skipped_not_scored_as_zero() -> None:
    units = harmony_units("jazz", 0, fed=0.7, none=0.1)
    ceilings: dict[str, float | None] = {"song-a": 0.9, "song-b": 0.1}
    (result,) = summarise_identity(units, ceilings)
    assert (result.charts, result.skipped) == (1, 1)
    assert result.identity == pytest.approx((0.75, 0.75, 0.75))
    (empty,) = summarise_identity(units, {})
    assert empty.identity is None
    assert not empty.passed


def test_a_preset_is_kept_only_when_both_slider_ends_clear_the_floor() -> None:
    ceilings: dict[str, float | None] = {"song-a": 0.9, "song-b": 0.9}
    units = [
        *harmony_units("jazz", 0, fed=0.7, none=0.1),
        *harmony_units("jazz", 1, fed=0.6, none=0.1),
        *harmony_units("metal", 0, fed=0.7, none=0.1),
        # Identity 0.2 / 0.8 = 0.25 is under the floor.
        *harmony_units("metal", 1, fed=0.3, none=0.1),
        *harmony_units("folk", 0, fed=0.7, none=0.1),
    ]
    results = summarise_identity(units, ceilings)
    # Jazz at t = 1 closes 0.5 / 0.8 = 0.625; folk has no t = 1 takes at all.
    assert failing_presets(results) == ["folk", "metal"]


def test_incomplete_cells_name_what_is_missing() -> None:
    units = [
        unit
        for slider in (0, 1)
        for condition in ("chords_melody", "chords", "none")
        for unit in take("jazz", "song-a", condition, slider, [0.5, 0.5, 0.5])
    ]
    assert incomplete_cells(units) == []
    short = [unit for unit in units if not (unit.condition == "none" and unit.run == 3)]
    assert incomplete_cells(short) == [
        "song-a / jazz / none / slider=0: 2 of 3 takes",
        "song-a / jazz / none / slider=1: 2 of 3 takes",
    ]


def melody_unit(condition: str, run: int, f1: float, preset: str = "jazz") -> Unit:
    return Unit("song-a", preset, condition, 0, run, 0.5, f1)


def test_melody_survives_when_the_paired_interval_clears_zero() -> None:
    fed = [melody_unit("chords_melody", run, f1) for run, f1 in enumerate((0.8, 0.7, 0.9), 1)]
    without = [melody_unit("chords", run, f1) for run, f1 in enumerate((0.2, 0.3, 0.1), 1)]
    result = summarise_melody([*fed, *without])
    assert result.pairs == 3
    assert result.difference is not None
    assert result.difference[0] == pytest.approx(0.6)
    assert result.difference[1] > 0
    assert result.survives
    swapped = summarise_melody(
        [
            *[melody_unit("chords", run, f1) for run, f1 in enumerate((0.8, 0.7, 0.9), 1)],
            *[melody_unit("chords_melody", run, f1) for run, f1 in enumerate((0.2, 0.3, 0.1), 1)],
        ]
    )
    assert not swapped.survives
    # Takes with the same F1 either way are a zero difference, which does not survive.
    flat = summarise_melody([melody_unit("chords_melody", 1, 0.4), melody_unit("chords", 1, 0.4)])
    assert flat.difference == (0.0, 0.0, 0.0)
    assert not flat.survives


def test_unpaired_melody_takes_are_not_a_result() -> None:
    result = summarise_melody([melody_unit("chords_melody", 1, 0.8), melody_unit("chords", 2, 0.1)])
    assert result == MelodyResult(0, None)
    assert not result.survives


def test_offline_identity_requires_diagnostic_and_preserves_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song = chart([(0, 4, "C")])
    chart_path = tmp_path / "test.chart.json"
    chart_path.write_text(song.model_dump_json())
    audio = tone(C_MAJOR, 2.0)
    write_wav(tmp_path / "test.wav", audio)
    data = {
        "chart": str(chart_path),
        "preset": "jazz",
        "condition": "none",
        "slider": 0,
        "run": 1,
        "recording": "test.wav",
        "play_time_s": 0,
        "beat_audible": [(0, 0), (4, 2)],
        "dropped_frames": 0,
        "mode": "offline",
    }
    (tmp_path / "test.take.json").write_text(json.dumps(data))
    claims = tmp_path / "claims.json"
    claims.write_text('{"existing": true}\n')
    report = tmp_path / "identity.md"
    arguments = [
        "measure_identity",
        str(tmp_path),
        "--fixtures",
        str(tmp_path),
        "--claims",
        str(claims),
        "--report",
        str(report),
    ]
    monkeypatch.setattr("sys.argv", arguments)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    monkeypatch.setattr("sys.argv", [*arguments, "--diagnostic"])
    assert main() == 1  # Missing conditions and repeats remain failures in diagnostic mode.
    assert report.read_text().startswith("Diagnostic only:")
    assert claims.read_text() == '{"existing": true}\n'


def test_claims_keep_other_entries_and_skip_empty_results(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "claims.json"
    assert not write_claims(path, MelodyResult(0, None))
    assert not path.exists()
    path.parent.mkdir()
    path.write_text(json.dumps({"heard_agreement": {"jazz": {"rate": 0.5}}}), encoding="utf-8")
    assert write_claims(path, MelodyResult(4, (0.5, 0.25, 0.75)))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "heard_agreement": {"jazz": {"rate": 0.5}},
        "melody_survives": True,
        "melody_difference": {"mean": 0.5, "low": 0.25, "high": 0.75, "pairs": 4},
    }
    assert write_claims(path, MelodyResult(4, (0.0, -0.25, 0.25)))
    assert json.loads(path.read_text(encoding="utf-8"))["melody_survives"] is False


@pytest.mark.parametrize("script", ["measure_identity.py", "record_takes.py"])
def test_script_runs_by_path_as_make_gate_calls_it(script: str) -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(  # noqa: S603 - this interpreter and a script in the checkout
        [sys.executable, str(root / "scripts" / script), "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
