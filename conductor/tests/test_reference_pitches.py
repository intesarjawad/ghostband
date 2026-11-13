from pathlib import Path

import pytest
from ghostband.chart import Chart

# Bar numbers follow each chart's source, including written-out repeats.
# Londonderry Air's undotted quarter in bar 5 shortens later offsets by half a beat.
PRINTED_BARS = (
    ("oh-susanna", 3, 4.5, 6.5, (71, 71, 69, 67)),
    ("oh-susanna", 4, 6.5, 8.5, (69, 67, 69)),
    ("oh-susanna", 7, 12.5, 14.5, (71, 71, 69, 69)),
    ("oh-susanna", 11, 20.5, 22.5, (71, 71, 69, 67)),
    ("oh-susanna", 12, 22.5, 24.5, (69, 67, 69)),
    ("oh-susanna", 15, 28.5, 30.5, (71, 71, 69, 69)),
    ("oh-susanna", 17, 32.5, 34.5, (72, 72)),
    ("oh-susanna", 23, 44.5, 46.5, (71, 71, 69, 69)),
    ("oh-susanna", 25, 48.5, 50.5, (72, 72)),
    ("oh-susanna", 31, 60.5, 62.5, (71, 71, 69, 69)),
    ("amazing-grace", 2, 3.0, 6.0, (72, 76, 72)),
    ("greensleeves", 4, 19 / 3, 25 / 3, (66, 63, 59, 64)),
    ("greensleeves", 12, 67 / 3, 73 / 3, (66, 63, 59)),
    ("londonderry-air", 2, 5.5, 9.5, (65, 63, 60, 63, 67, 68)),
    ("londonderry-air", 8, 29.0, 33.0, (63, 70, 72, 74)),
    ("londonderry-air", 13, 49.0, 53.0, (79, 77, 77, 75, 72, 75)),
    ("londonderry-air", 15, 57.0, 61.0, (67, 72, 70, 67, 65, 63, 60, 62)),
    ("ode-to-joy", 3, 8.0, 12.0, (50, 52, 54)),
)


@pytest.mark.parametrize(
    ("name", "bar", "start", "end", "pitches"),
    PRINTED_BARS,
    ids=[f"{name}-bar-{bar}" for name, bar, *_ in PRINTED_BARS],
)
def test_printed_bar_pitches(
    name: str, bar: int, start: float, end: float, pitches: tuple[int, ...]
) -> None:
    path = Path(__file__).parents[2] / "charts" / f"{name}.chart.json"
    chart = Chart.model_validate_json(path.read_text())
    actual = tuple(n.pitch for n in chart.melody if start <= n.start_beat < end)
    assert actual == pitches, f"{chart.title} bar {bar} differs from the printed source"


@pytest.mark.parametrize(
    ("bar", "start", "symbols"),
    [
        (3, 4.5, ("G", "D7")),
        (4, 6.5, ("D", "D")),
        (7, 12.5, ("G", "D7")),
        (11, 20.5, ("G", "D7")),
        (12, 22.5, ("D", "D")),
        (15, 28.5, ("G", "D7")),
        (23, 44.5, ("G", "D7")),
        (31, 60.5, ("G", "D7")),
    ],
    ids=str,
)
def test_susanna_corrected_bar_harmony(bar: int, start: float, symbols: tuple[str, str]) -> None:
    path = Path(__file__).parents[2] / "charts" / "oh-susanna.chart.json"
    chart = Chart.model_validate_json(path.read_text())
    for offset, symbol in enumerate(symbols):
        chord = next(c for c in chart.chords if c.start_beat <= start + offset < c.end_beat)
        assert chord.symbol == symbol, f"Oh! Susanna bar {bar}, beat {offset + 1}"


def test_ode_corrected_bar_harmony() -> None:
    path = Path(__file__).parents[2] / "charts" / "ode-to-joy.chart.json"
    chart = Chart.model_validate_json(path.read_text())
    assert [
        (c.start_beat, c.end_beat, c.symbol) for c in chart.chords if 8 <= c.start_beat < 12
    ] == [
        (8, 10, "D"),
        (10, 11, "A"),
        (11, 12, "D"),
    ]
