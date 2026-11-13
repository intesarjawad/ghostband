import json
import logging
from pathlib import Path

import pytest
from ghostband.chart import Chart, ChordEvent, NoteEvent, Section
from pydantic import ValidationError


@pytest.fixture
def chart_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "title": "Test tune",
        "source": "user import",
        "rights": "own-work",
        "extractor": "manual",
        "tempo_bpm": 100,
        "beat_times_s": [],
        "time_signature": [4, 4],
        "key": "C",
        "sections": [{"name": "A", "start_beat": 0, "end_beat": 8}],
        "chords": [
            {"symbol": "C/E", "start_beat": 0, "end_beat": 4},
            {"symbol": "N.C.", "start_beat": 4, "end_beat": 8},
        ],
        "melody": [{"pitch": 60, "start_beat": 0, "end_beat": 1}],
        "duration_beats": 8,
    }


@pytest.mark.parametrize(
    ("signature", "beats"),
    [
        ((4, 4), 4),
        ((3, 4), 3),
        ((2, 2), 2),
        ((7, 8), 7),
        ((3, 8), 1),
        ((6, 8), 2),
        ((9, 8), 3),
        ((12, 8), 4),
        ((6, 4), 6),
    ],
)
def test_beats_per_bar(
    chart_data: dict[str, object], signature: tuple[int, int], beats: int
) -> None:
    chart_data["time_signature"] = signature
    assert Chart.model_validate(chart_data).beats_per_bar == beats


@pytest.mark.parametrize(
    ("first_section_end", "phase"),
    [(8.0, 0.0), (0.5, 0.5), (1.5, 0.5), (1.0, 0.0), (4.5, 0.0)],
)
def test_a_pickup_shorter_than_a_bar_sets_the_beat_phase(
    chart_data: dict[str, object], first_section_end: float, phase: float
) -> None:
    chart_data["sections"] = [
        {"name": "Pickup", "start_beat": 0, "end_beat": first_section_end},
        {"name": "A", "start_beat": first_section_end, "end_beat": 9},
    ]
    chart_data["duration_beats"] = 9
    assert Chart.model_validate(chart_data).beat_phase == phase


def test_json_round_trip(chart_data: dict[str, object]) -> None:
    chart = Chart.model_validate(chart_data)
    assert Chart.model_validate_json(chart.model_dump_json()) == chart
    assert chart.chords[1].symbol == "N.C."
    assert "beats_per_bar" not in chart.model_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("rights", "licensed"),
        ("extractor", "other"),
        ("title", ""),
        ("source", ""),
        ("key", ""),
        ("tempo_bpm", 0),
        ("tempo_bpm", -1),
        ("tempo_bpm", float("inf")),
        ("duration_beats", 0),
        ("duration_beats", -1),
        ("duration_beats", float("nan")),
        ("time_signature", [0, 4]),
        ("time_signature", [4, 0]),
        ("time_signature", [4, 3]),
        ("time_signature", [True, 4]),
        ("beat_times_s", [0, 0]),
        ("beat_times_s", [1, 0]),
        ("beat_times_s", [-1, 0]),
        ("beat_times_s", [0, float("inf")]),
        ("sections", []),
    ],
)
def test_invalid_chart_fields(chart_data: dict[str, object], field: str, value: object) -> None:
    chart_data[field] = value
    with pytest.raises(ValidationError):
        Chart.model_validate(chart_data)


@pytest.mark.parametrize(
    ("spans", "message"),
    [
        ([(1, 8)], "sections must start at beat 0"),
        ([(0, 3), (4, 8)], "sections must be contiguous"),
        ([(0, 5), (4, 8)], "sections must be contiguous"),
        ([(4, 8), (0, 4)], "sections must start at beat 0"),
        ([(0, 7)], "sections must end at duration_beats"),
        ([(0, 9)], "sections must end at duration_beats"),
    ],
)
def test_section_coverage(
    chart_data: dict[str, object], spans: list[tuple[int, int]], message: str
) -> None:
    chart_data["sections"] = [
        {"name": "A", "start_beat": start, "end_beat": end} for start, end in spans
    ]
    with pytest.raises(ValidationError, match=message):
        Chart.model_validate(chart_data)


@pytest.mark.parametrize(
    ("spans", "message"),
    [
        ([(4, 8), (0, 4)], "chords must be sorted"),
        ([(0, 5), (4, 8)], "chords must not overlap"),
        ([(0, 4), (0, 4)], "chords must not overlap"),
        ([(0, 9)], "chords must end within duration_beats"),
    ],
)
def test_chord_order_and_bounds(
    chart_data: dict[str, object], spans: list[tuple[int, int]], message: str
) -> None:
    chart_data["chords"] = [
        {"symbol": "C", "start_beat": start, "end_beat": end} for start, end in spans
    ]
    with pytest.raises(ValidationError, match=message):
        Chart.model_validate(chart_data)


@pytest.mark.parametrize("model", [Section, ChordEvent, NoteEvent])
@pytest.mark.parametrize("span", [(-1, 1), (1, 1), (2, 1), (0, float("inf"))])
def test_invalid_event_intervals(
    model: type[Section] | type[ChordEvent] | type[NoteEvent], span: tuple[float, float]
) -> None:
    fields: dict[str, object]
    if model is Section:
        fields = {"name": "A"}
    elif model is ChordEvent:
        fields = {"symbol": "C"}
    else:
        fields = {"pitch": 60}
    fields.update(start_beat=span[0], end_beat=span[1])
    with pytest.raises(ValidationError):
        model.model_validate(fields)


@pytest.mark.parametrize("pitch", [-1, 128, 60.5, True])
def test_invalid_midi_pitch(pitch: object) -> None:
    with pytest.raises(ValidationError):
        NoteEvent.model_validate({"pitch": pitch, "start_beat": 0, "end_beat": 1})


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (Section, {"name": "", "start_beat": 0, "end_beat": 1}),
        (ChordEvent, {"symbol": "", "start_beat": 0, "end_beat": 1}),
        (NoteEvent, {"pitch": 60, "start_beat": 0, "end_beat": 1, "unexpected": 1}),
    ],
)
def test_invalid_event_fields(
    model: type[Section] | type[ChordEvent] | type[NoteEvent], data: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_unknown_chart_fields_are_rejected(chart_data: dict[str, object]) -> None:
    chart_data["unexpected"] = 1
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Chart.model_validate(chart_data)


def test_missing_fields_are_rejected(chart_data: dict[str, object]) -> None:
    del chart_data["schema_version"]
    with pytest.raises(ValidationError, match="Field required"):
        Chart.model_validate(chart_data)


def test_melody_bounds(chart_data: dict[str, object]) -> None:
    chart_data["melody"] = [{"pitch": 60, "start_beat": 7, "end_beat": 9}]
    with pytest.raises(ValidationError, match="melody must end within duration_beats"):
        Chart.model_validate(chart_data)


def test_silence_and_chord_gaps_are_valid(chart_data: dict[str, object]) -> None:
    chart_data["melody"] = []
    chart_data["chords"] = [{"symbol": "N.C.", "start_beat": 2, "end_beat": 3}]
    chart_data["beat_times_s"] = [0, 0.6, 1.21]
    assert Chart.model_validate(chart_data).melody == []


def test_import_resets_rights_and_clamps_once(
    chart_data: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    chart_data["rights"] = "public-domain"
    pitches = [0, 35, 36, 60, 96, 97, 127]
    chart_data["melody"] = [
        {"pitch": pitch, "start_beat": i, "end_beat": i + 1} for i, pitch in enumerate(pitches)
    ]
    with caplog.at_level(logging.WARNING, logger="ghostband.chart"):
        imported = Chart.from_import(chart_data)
    assert imported.rights == "unknown"
    assert [note.pitch for note in imported.melody] == [36, 36, 36, 60, 96, 96, 96]
    assert len(caplog.records) == 1
    assert "4 melody pitches" in caplog.text
    assert [note.pitch for note in Chart.model_validate(chart_data).melody] == pitches
    assert chart_data["rights"] == "public-domain"


def test_import_in_range_is_quiet(
    chart_data: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    chart_data["rights"] = "own-work"
    imported = Chart.from_import(chart_data)
    assert imported.rights == "unknown"
    assert not caplog.records


def test_bundled_charts_validate() -> None:
    paths = sorted((Path(__file__).parents[2] / "charts").glob("*.chart.json"))
    assert {path.stem for path in paths} == {
        "greensleeves.chart",
        "londonderry-air.chart",
        "ode-to-joy.chart",
        "amazing-grace.chart",
        "oh-susanna.chart",
    }
    for path in paths:
        chart = Chart.model_validate_json(path.read_text())
        assert chart.rights == "public-domain"
        assert chart.extractor == "manual"
        assert not chart.beat_times_s
        assert chart.melody
        assert "https://" in chart.source
        assert "bars " in chart.source
        assert "p. " in chart.source
        assert json.loads(chart.model_dump_json()) == json.loads(path.read_text())


@pytest.mark.parametrize(
    ("slug", "pitch", "start", "end", "signature"),
    [
        ("greensleeves", 64, 0, 1 / 3, (6, 8)),
        ("londonderry-air", 62, 0, 0.5, (4, 4)),
        ("ode-to-joy", 54, 0, 2, (4, 4)),
        ("amazing-grace", 67, 2, 3, (3, 4)),
        ("oh-susanna", 67, 0, 0.25, (2, 4)),
    ],
)
def test_printed_openings(
    slug: str, pitch: int, start: float, end: float, signature: tuple[int, int]
) -> None:
    path = Path(__file__).parents[2] / "charts" / f"{slug}.chart.json"
    chart = Chart.model_validate_json(path.read_text())
    assert chart.time_signature == signature
    first_note = chart.melody[0]
    assert (first_note.pitch, first_note.start_beat, first_note.end_beat) == (pitch, start, end)
    if slug == "ode-to-joy":
        # The cello theme opens with a half note, unlike versions that repeat it.
        assert [note.pitch for note in chart.melody[:3]] == [54, 55, 57]
