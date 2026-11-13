"""The change detector finds where recorded frames switch triad, to a known error."""

from importlib import import_module
from pathlib import Path

import librosa
import numpy as np
import pytest
from ghostband.evaluation import chord_detector
from ghostband.evaluation.chord_detector import (
    CHANGE_AFTER_S,
    CHANGE_HOLD_S,
    FRAME_SECONDS,
    change_time,
    frames,
    majmin,
)
from ghostband.importing import read_chart
from numpy.typing import NDArray

sf = import_module("soundfile")
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = sorted((ROOT / "fixtures").glob("*.wav"))
MEDIAN_ERROR_S = 0.03
TOLERANCE_S = 0.12
RATE_FLOOR = 0.9


def triad_frames(
    runs: list[tuple[list[int], int]],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Build frame times and chroma where each run is a pitch-class list held for n frames."""
    count = sum(length for _, length in runs)
    chroma: NDArray[np.float64] = np.zeros((12, count), dtype=np.float64)
    start = 0
    for pitch_classes, length in runs:
        chroma[pitch_classes, start : start + length] = 1.0
        start += length
    return np.arange(count, dtype=np.float64) * FRAME_SECONDS, chroma


C_MAJOR, G_MAJOR = [0, 4, 7], [7, 11, 2]


def test_a_change_is_the_first_frame_of_the_new_triad() -> None:
    times, chroma = triad_frames([(C_MAJOR, 20), (G_MAJOR, 30)])
    found = change_time(times, chroma, "C", "G", at_s=20 * FRAME_SECONDS)
    assert found == pytest.approx(20 * FRAME_SECONDS)


def test_an_early_change_inside_the_window_gives_a_negative_error() -> None:
    times, chroma = triad_frames([(C_MAJOR, 20), (G_MAJOR, 30)])
    # Frame 20 sits 5 frames (116 ms) before the chart's 25th frame.
    found = change_time(times, chroma, "C", "G", at_s=25 * FRAME_SECONDS)
    assert found == pytest.approx(20 * FRAME_SECONDS)


def test_a_blip_shorter_than_100_ms_is_not_a_change() -> None:
    # Four frames last 93 ms, one frame short of the hold.
    times, chroma = triad_frames([(C_MAJOR, 20), (G_MAJOR, 4), (C_MAJOR, 30)])
    assert change_time(times, chroma, "C", "G", at_s=20 * FRAME_SECONDS) is None
    times, chroma = triad_frames([(C_MAJOR, 20), (G_MAJOR, 5), (C_MAJOR, 30)])
    assert change_time(times, chroma, "C", "G", at_s=20 * FRAME_SECONDS) == pytest.approx(
        20 * FRAME_SECONDS
    )


def test_a_change_outside_the_search_window_is_a_miss() -> None:
    times, chroma = triad_frames([(C_MAJOR, 100), (G_MAJOR, 50)])
    # The chart says 0.5 s into the take; the triad switches at 2.3 s, past +0.5 s.
    assert change_time(times, chroma, "C", "G", at_s=0.5) is None


def test_silent_frames_favour_neither_triad() -> None:
    times, chroma = triad_frames([([], 60)])
    assert change_time(times, chroma, "C", "G", at_s=0.5) is None


def test_the_run_may_end_with_the_recording() -> None:
    times, chroma = triad_frames([(C_MAJOR, 20), (G_MAJOR, 5)])
    assert change_time(times, chroma, "C", "G", at_s=20 * FRAME_SECONDS) == pytest.approx(
        20 * FRAME_SECONDS
    )


def test_a_non_triad_name_is_rejected() -> None:
    times, chroma = triad_frames([(C_MAJOR, 20)])
    with pytest.raises(ValueError, match="triad"):
        change_time(times, chroma, "C", "N.C.", at_s=0.2)


def test_the_fixture_detector_counts_every_change_and_lands_most_within_120_ms() -> None:
    errors: list[float] = []
    eligible = hits = misses = 0
    for path in FIXTURES:
        chart = read_chart(ROOT / "charts" / path.with_suffix(".chart.json").name)
        audio, rate = sf.read(str(path), dtype="float32")
        times, chroma = frames(
            np.asarray(
                librosa.resample(audio, orig_sr=rate, target_sr=chord_detector.ANALYSIS_RATE),
                dtype=np.float32,
            )
        )
        # A fixture is trimmed or padded to 30 s, so changes past its audio have no truth here.
        duration_s = len(audio) / rate
        seconds_per_beat = 60 / chart.tempo_bpm
        for previous, current in zip(chart.chords, chart.chords[1:], strict=False):
            before, after = majmin(previous.symbol), majmin(current.symbol)
            if before == after or "N.C." in (before, after):
                continue
            at_s = current.start_beat * seconds_per_beat
            if at_s + CHANGE_AFTER_S + CHANGE_HOLD_S > duration_s:
                continue
            eligible += 1
            found = change_time(times, chroma, before, after, at_s)
            if found is None:
                misses += 1
                continue
            error = abs(found - at_s)
            errors.append(error)
            hits += error <= TOLERANCE_S
    # Fixtures render the chart's chords with its melody on top, so this is the
    # detector under a harder mix than the band's chords alone; a change it cannot
    # find stays in the denominator.
    assert eligible >= 40
    assert hits / eligible >= RATE_FLOOR, f"{misses} misses of {eligible} changes"
    assert float(np.median(errors)) < MEDIAN_ERROR_S
