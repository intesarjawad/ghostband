from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from ghostband.chart import Chart
from ghostband.export import chart_to_midi
from ghostband.extractors.sheetsage2_output import chart_from_output

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).resolve().parents[2]


def write_midi(path: Path, notes: Sequence[tuple[float, float, int]]) -> None:
    mido = import_module("mido")
    midi = mido.MidiFile(ticks_per_beat=480)
    track: list[object] = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000))
    previous = 0
    for tick, change, midi_pitch in sorted(
        event
        for start, end, pitch in notes
        for event in [(round(start * 960), 1, pitch), (round(end * 960), 0, pitch)]
    ):
        track.append(
            mido.Message(
                "note_on" if change else "note_off",
                note=midi_pitch,
                velocity=80 if change else 0,
                time=tick - previous,
            )
        )
        previous = tick
    midi.save(str(path))


def write_timing(path: Path, duration_s: float = 2, device: str = "cpu") -> None:
    (path / "timing.json").write_text(
        json.dumps(
            {
                "device": device,
                "sheetsage2_revision": "test-sheet",
                "mert_revision": "test-parent",
                "audio_duration_s": duration_s,
                "load_s": 1,
                "prepare_s": 1,
                "inference_s": 6,
                "total_s": 8,
                "realtime_factor": 8 / duration_s,
                "exceeds_3x": True,
            }
        )
    )


@pytest.fixture
def model_output(tmp_path: Path) -> Path:
    events: list[dict[str, object]] = [
        {"time": index * 0.5, "values": {"rhythm": {"meter": [4, 4], "eighth_position": index * 2}}}
        for index in range(4)
    ]
    events.extend(
        [
            {"time": 0, "values": {"key": "C:maj", "chord": "C:maj", "structure": "verse"}},
            {"time": 0.76, "values": {"chord": "G:7/3"}},
            {"time": 1.26, "values": {"structure": "chorus"}},
        ]
    )
    events.sort(key=lambda event: float(str(event["time"])))
    (tmp_path / "events.json").write_text(json.dumps({"events": events}))
    write_timing(tmp_path)
    write_midi(tmp_path / "melody_vocal.mid", [(0, 0.5, 60), (3, 4, 90), (4, 5, 91)])
    write_midi(tmp_path / "melody_instrumental.mid", [(0.25, 1, 67), (1, 2.5, 69)])
    return tmp_path


def test_sheetsage2_conversion_quantizes_chords_and_selects_in_song_melody(
    model_output: Path,
) -> None:
    chart = chart_from_output(model_output, title="Example", source="example.wav")
    assert chart.extractor == "sheetsage2"
    assert chart.rights == "unknown"
    assert chart.tempo_bpm == 120
    assert chart.beat_times_s == [0, 0.5, 1, 1.5]
    assert chart.duration_beats == 4
    assert chart.key == "C"
    assert [(c.start_beat, c.end_beat, c.symbol) for c in chart.chords] == [
        (0, 2, "C"),
        (2, 4, "G7/B"),
    ]
    assert [(s.name, s.start_beat, s.end_beat) for s in chart.sections] == [
        ("verse", 0, 3),
        ("chorus", 3, 4),
    ]
    assert [(n.start_beat, n.end_beat, n.pitch) for n in chart.melody] == [(0.5, 2, 67), (2, 4, 69)]


@pytest.mark.parametrize("golden", sorted((ROOT / "fixtures").glob("*.golden.json")))
def test_sheetsage2_conversion_on_reference_timelines(golden: Path, tmp_path: Path) -> None:
    reference = Chart.model_validate_json(golden.read_text())
    period_s = 60 / reference.tempo_bpm
    numerator, denominator = reference.time_signature
    compound = denominator == 8 and numerator % 3 == 0
    subdivision = 3 if compound else 1
    duration_s = reference.duration_beats * period_s
    events: list[dict[str, object]] = [
        {
            "time": index * period_s / subdivision,
            "values": {
                "rhythm": {
                    "meter": list(reference.time_signature),
                    "eighth_position": (index % numerator) * 8 // denominator,
                }
            },
        }
        for index in range(round(reference.duration_beats * subdivision))
    ]
    events.append({"time": 0, "values": {"key": "E:min", "structure": "verse", "chord": "E:min"}})
    events.sort(key=lambda event: float(str(event["time"])))
    (tmp_path / "events.json").write_text(json.dumps({"events": events}))
    write_timing(tmp_path, duration_s)
    melody = reference.model_copy(update={"chords": []})
    (tmp_path / "melody_instrumental.mid").write_bytes(chart_to_midi(melody))
    write_midi(tmp_path / "melody_vocal.mid", [])
    chart = chart_from_output(tmp_path, title=reference.title, source=golden.name)
    assert chart.time_signature == reference.time_signature
    assert chart.tempo_bpm == pytest.approx(reference.tempo_bpm)
    assert chart.duration_beats == pytest.approx(reference.duration_beats)
    assert [n.pitch for n in chart.melody] == [n.pitch for n in reference.melody]
    for actual, expected in zip(chart.melody, reference.melody, strict=True):
        assert actual.start_beat == pytest.approx(expected.start_beat, abs=0.002)
        assert actual.end_beat == pytest.approx(expected.end_beat, abs=0.002)


@pytest.mark.parametrize(
    "mutation", ["no-beats", "duplicate-beat", "bad-meter", "no-key", "nonfinite"]
)
def test_sheetsage2_rejects_unusable_events(model_output: Path, mutation: str) -> None:
    path = model_output / "events.json"
    data = json.loads(path.read_text())
    if mutation == "no-beats":
        data["events"] = [e for e in data["events"] if "rhythm" not in e["values"]]
    elif mutation == "duplicate-beat":
        data["events"].insert(0, data["events"][0])
    elif mutation == "bad-meter":
        data["events"][0]["values"]["rhythm"]["meter"] = [4, 3]
    elif mutation == "nonfinite":
        data["events"][0]["time"] = float("nan")
    else:
        for event in data["events"]:
            event["values"].pop("key", None)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=r"required|increasing|meter|key|finite"):
        chart_from_output(model_output, title="Example", source="example.wav")


def write_pickup_output(
    path: Path,
    rhythm: list[tuple[float, list[int] | None, int]],
    duration_s: float,
    downbeat_chord_s: float | None = None,
) -> None:
    events: list[dict[str, object]] = [
        {
            "time": time,
            "values": {
                "rhythm": {"eighth_position": position}
                | ({} if meter is None else {"meter": meter})
            },
        }
        for time, meter, position in rhythm
    ]
    events.append({"time": 0, "values": {"key": "C:maj", "chord": "G:7", "structure": "intro"}})
    if downbeat_chord_s is not None:
        events.append({"time": downbeat_chord_s, "values": {"chord": "C:maj"}})
    events.sort(key=lambda event: float(str(event["time"])))
    (path / "events.json").write_text(json.dumps({"events": events}))
    write_timing(path, duration_s)
    write_midi(path / "melody_vocal.mid", [])


def test_sheetsage2_counts_a_three_eighth_pickup_before_common_time(tmp_path: Path) -> None:
    # Eighths last 0.25 s. Three pickup eighths end at the downbeat, 0.75 s, which is beat
    # 3 / 2 = 1.5. Quarters follow every 0.5 s on beats 2.5, 3.5 and 4.5.
    write_pickup_output(
        tmp_path,
        [
            (0.0, [3, 8], 0),
            (0.25, None, 1),
            (0.5, None, 2),
            (0.75, [4, 4], 0),
            (1.25, None, 2),
            (1.75, None, 4),
            (2.25, None, 6),
        ],
        duration_s=2.5,
        downbeat_chord_s=0.75,
    )
    # Pickup notes on the first and third eighths, then a quarter on the downbeat.
    write_midi(
        tmp_path / "melody_instrumental.mid", [(0, 0.25, 62), (0.5, 0.75, 65), (0.75, 1.25, 67)]
    )
    chart = chart_from_output(tmp_path, title="Pickup", source="pickup.wav")
    assert chart.time_signature == (4, 4)
    assert chart.tempo_bpm == pytest.approx(120)
    # Whole beats: 0 at 0 s; 1 two thirds through the pickup; 2 halfway between the downbeat
    # and the next quarter (0.75 + 0.25); then every 0.5 s.
    assert chart.beat_times_s == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])
    assert [(c.symbol, c.start_beat) for c in chart.chords] == [("G7", 0.0), ("C", 1.5)]
    assert [beat for n in chart.melody for beat in (n.start_beat, n.end_beat)] == pytest.approx(
        [0.0, 0.5, 1.0, 1.5, 1.5, 2.5]
    )
    # The recording ends 0.25 s after beat 4.5.
    assert chart.duration_beats == pytest.approx(5.0)


def test_sheetsage2_counts_a_single_eighth_pickup_before_two_four(tmp_path: Path) -> None:
    # One pickup eighth of 0.3 s is half a quarter beat, so the downbeat is beat 0.5 and
    # quarters every 0.6 s land on 1.5 and 2.5. Whole beats fall midway: 0.6 s and 1.2 s.
    write_pickup_output(
        tmp_path,
        [(0.0, [1, 8], 0), (0.3, [2, 4], 0), (0.9, None, 2), (1.5, None, 0)],
        duration_s=1.8,
    )
    write_midi(tmp_path / "melody_instrumental.mid", [(0, 0.3, 67)])
    chart = chart_from_output(tmp_path, title="Pickup", source="pickup.wav")
    assert chart.time_signature == (2, 4)
    assert chart.beat_times_s == pytest.approx([0.0, 0.6, 1.2])
    assert [(n.start_beat, n.end_beat) for n in chart.melody] == [pytest.approx((0.0, 0.5))]


@pytest.mark.parametrize(
    "rhythm",
    [
        # A change after two full measures.
        [
            (0.0, [2, 4], 0),
            (0.4, None, 2),
            (0.8, None, 0),
            (1.2, None, 2),
            (1.6, [3, 4], 0),
            (2.0, None, 2),
        ],
        # An opening measure as long as the meter that follows.
        [(0.0, [4, 8], 0), (0.5, None, 2), (1.0, [2, 4], 0), (1.5, None, 2), (2.0, None, 0)],
        # A second change.
        [(0.0, [1, 8], 0), (0.3, [2, 4], 0), (0.9, None, 2), (1.5, [3, 4], 0), (2.1, None, 2)],
    ],
)
def test_sheetsage2_rejects_meter_changes_that_are_not_a_pickup(
    tmp_path: Path, rhythm: list[tuple[float, list[int] | None, int]]
) -> None:
    write_pickup_output(tmp_path, rhythm, duration_s=2.5)
    write_midi(tmp_path / "melody_instrumental.mid", [])
    with pytest.raises(ValueError, match=r"The meter changes from \d+/\d+ to \d+/\d+ at"):
        chart_from_output(tmp_path, title="Change", source="change.wav")
