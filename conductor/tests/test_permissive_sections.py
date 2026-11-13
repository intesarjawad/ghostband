from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np
import pytest
from ghostband.chart import Chart

if TYPE_CHECKING:
    from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[2]
C_MAJOR = np.array([1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0], dtype=np.float64) / 3
G_MAJOR = np.array([0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1], dtype=np.float64) / 3


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(sys, "path", [str(ROOT / "extract/permissive"), *sys.path])
    return SimpleNamespace(sections=import_module("sections"), runtime=import_module("run"))


def beat_rows(*runs: tuple[NDArray[np.float64], int]) -> NDArray[np.float64]:
    return np.vstack([np.tile(chroma, (count, 1)) for chroma, count in runs])


def grid(
    beat_count: int, first_downbeat: int = 0
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    beats = np.arange(beat_count, dtype=np.float64) * 0.5
    return beats, beats[first_downbeat::4]


def summary(sections: list[dict[str, object]]) -> list[tuple[str, float, float]]:
    return [
        (str(s["name"]), float(str(s["start_beat"])), float(str(s["end_beat"]))) for s in sections
    ]


def test_a_b_a_form_gets_matching_letters_at_the_changes(modules: SimpleNamespace) -> None:
    features = beat_rows((C_MAJOR, 16), (G_MAJOR, 16), (C_MAJOR, 16))
    beats, downbeats = grid(48)
    actual = modules.sections.find_sections(features, beats, downbeats, 48.0)
    assert summary(actual) == [("A", 0.0, 16.0), ("B", 16.0, 32.0), ("A", 32.0, 48.0)]


def test_a_pickup_beat_joins_the_first_bar(modules: SimpleNamespace) -> None:
    features = beat_rows((C_MAJOR, 17), (G_MAJOR, 16), (C_MAJOR, 16))
    beats, downbeats = grid(49, first_downbeat=1)
    actual = modules.sections.find_sections(features, beats, downbeats, 49.0)
    assert summary(actual) == [("A", 0.0, 17.0), ("B", 17.0, 33.0), ("A", 33.0, 49.0)]


def test_unchanging_or_short_songs_stay_one_section(modules: SimpleNamespace) -> None:
    beats, downbeats = grid(48)
    flat = beat_rows((C_MAJOR, 48))
    assert summary(modules.sections.find_sections(flat, beats, downbeats, 48.5)) == [
        ("A", 0.0, 48.5)
    ]
    beats, downbeats = grid(24)
    short = beat_rows((C_MAJOR, 12), (G_MAJOR, 12))
    assert summary(modules.sections.find_sections(short, beats, downbeats, 24.0)) == [
        ("A", 0.0, 24.0)
    ]


def test_sections_are_never_shorter_than_four_bars(modules: SimpleNamespace) -> None:
    features = beat_rows((C_MAJOR, 16), (G_MAJOR, 8), (C_MAJOR, 24))
    beats, downbeats = grid(48)
    actual = modules.sections.find_sections(features, beats, downbeats, 48.0)
    spans = summary(actual)
    assert len(spans) >= 2
    assert all(end - start >= 16 for _, start, end in spans)
    assert [start for _, start, _ in spans[1:]] == [end for _, _, end in spans[:-1]]


def test_bar_edges_drop_a_stub_final_bar_only(modules: SimpleNamespace) -> None:
    beats, downbeats = grid(49)
    assert modules.sections.bar_edges(beats, downbeats, 49) == list(range(0, 48, 4))
    beats, downbeats = grid(50)
    assert modules.sections.bar_edges(beats, downbeats, 50) == list(range(0, 52, 4))


def test_sections_fit_the_chart_contract(modules: SimpleNamespace) -> None:
    features = beat_rows((C_MAJOR, 16), (G_MAJOR, 16), (C_MAJOR, 16))
    beats, downbeats = grid(48)
    data = modules.runtime.build_chart(Path("song.wav"), (beats, downbeats), 24.0, ["C"] * 48, "C")
    data["sections"] = modules.sections.find_sections(
        features, beats, downbeats, data["duration_beats"]
    )
    chart = Chart.model_validate(data)
    assert [section.name for section in chart.sections] == ["A", "B", "A"]
