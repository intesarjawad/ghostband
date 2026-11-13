import asyncio
from importlib import import_module
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
from ghostband.chart import Chart
from ghostband.chords import Chord, ChordQuality, parse_chord
from ghostband.extractors.permissive import run_permissive

ROOT = Path(__file__).resolve().parents[2]
QUALITIES = {
    ChordQuality.MAJOR: "maj",
    ChordQuality.MINOR: "min",
    ChordQuality.DOMINANT_SEVENTH: "7",
    ChordQuality.MINOR_SEVENTH: "min7",
}


def labels(chart: Chart) -> list[str]:
    result: list[str] = []
    for event in chart.chords:
        chord = parse_chord(event.symbol)
        result.append(
            f"{chord.root}:{QUALITIES[chord.quality]}" if isinstance(chord, Chord) else "N"
        )
    return result


@pytest.mark.models
@pytest.mark.parametrize("wav", sorted((ROOT / "fixtures").glob("*.wav")), ids=lambda p: p.stem)
def test_permissive_fixture_majmin_at_least_060(wav: Path) -> None:
    reference = Chart.model_validate_json((ROOT / "charts" / f"{wav.stem}.chart.json").read_text())
    estimated = Chart.model_validate(asyncio.run(run_permissive("chart", wav)))
    reference_intervals = np.array(
        [[c.start_beat, c.end_beat] for c in reference.chords], dtype=np.float64
    ) * (60 / reference.tempo_bpm)
    last_beat = len(estimated.beat_times_s) - 1
    grid_beats = np.array([*range(len(estimated.beat_times_s)), estimated.duration_beats])
    grid_times = np.array(
        [
            *estimated.beat_times_s,
            estimated.beat_times_s[-1]
            + (estimated.duration_beats - last_beat) * 60 / estimated.tempo_bpm,
        ]
    )
    estimated_intervals = np.interp(
        np.array([[c.start_beat, c.end_beat] for c in estimated.chords]), grid_beats, grid_times
    )
    mir_chord = import_module("mir_eval.chord")
    scores: dict[str, float] = mir_chord.evaluate(
        reference_intervals, labels(reference), estimated_intervals, labels(estimated)
    )
    print(f"{wav.stem}: majmin={scores['majmin']:.6f}")
    if scores["majmin"] < 0.60:
        print("beat time_s reference predicted")
        for beat, (start, end) in enumerate(pairwise(grid_times)):
            midpoint = (start + end) / 2
            expected = next(
                (
                    event.symbol
                    for event, interval in zip(reference.chords, reference_intervals, strict=True)
                    if interval[0] <= midpoint < interval[1]
                ),
                "N.C.",
            )
            predicted = next(
                event.symbol
                for event in estimated.chords
                if event.start_beat <= beat < event.end_beat
            )
            print(f"{beat:3} {start:6.2f} {expected:5} {predicted:5}")
    assert scores["majmin"] >= 0.60
