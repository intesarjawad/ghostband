from importlib import import_module
from pathlib import Path

import pytest
from ghostband.chart import Chart
from ghostband.extractors.midi import extract_midi, main

ROOT = Path(__file__).resolve().parents[2]
mido = import_module("mido")
MIDI_FILES = sorted((ROOT / "fixtures").glob("*.mid"))


@pytest.mark.parametrize("path", MIDI_FILES, ids=lambda path: path.stem)
def test_every_fixture_matches_chart_at_midi_resolution(path: Path, tmp_path: Path) -> None:
    expected = Chart.model_validate_json((ROOT / "charts" / f"{path.stem}.chart.json").read_text())
    # Copy away from the source data to enforce extraction from MIDI alone.
    isolated = tmp_path / "input.mid"
    isolated.write_bytes(path.read_bytes())
    actual = extract_midi(isolated)
    assert actual.title == expected.title
    assert actual.tempo_bpm == expected.tempo_bpm
    assert actual.time_signature == expected.time_signature
    assert actual.duration_beats == expected.duration_beats
    numerator, denominator = expected.time_signature
    ticks = 960 * 4 / denominator * (3 if denominator == 8 and numerator % 3 == 0 else 1)
    assert [
        (round(chord.start_beat * ticks), round(chord.end_beat * ticks), chord.symbol)
        for chord in actual.chords
    ] == [
        (round(chord.start_beat * ticks), round(chord.end_beat * ticks), chord.symbol)
        for chord in expected.chords
    ]
    assert [
        (round(note.start_beat * ticks), round(note.end_beat * ticks), note.pitch)
        for note in actual.melody
    ] == [
        (round(note.start_beat * ticks), round(note.end_beat * ticks), note.pitch)
        for note in expected.melody
    ]
    assert actual.rights == "unknown"
    assert actual.extractor == "midi"
    assert actual.key == "unknown"
    assert actual.source == "input.mid"
    assert actual.sections[0].end_beat == expected.duration_beats
    assert actual.beat_times_s[-1] == pytest.approx(
        expected.duration_beats * 60 / expected.tempo_bpm, abs=0.0001
    )


def test_fixtures_exist() -> None:
    assert MIDI_FILES
    assert {path.stem for path in MIDI_FILES} == {
        path.name.removesuffix(".chart.json") for path in (ROOT / "charts").glob("*.chart.json")
    }


def write_midi(tmp_path: Path, tracks: list[list[object]], *, file_type: int = 1) -> Path:
    midi = mido.MidiFile(type=file_type, ticks_per_beat=480)
    for messages in tracks:
        track = mido.MidiTrack()
        track.extend(messages)
        midi.tracks.append(track)
    path = tmp_path / "song.mid"
    midi.save(path)
    return path


def note(pitch: int, start: int = 0, duration: int = 480, channel: int = 0) -> list[object]:
    return [
        mido.Message("note_on", note=pitch, velocity=80, time=start, channel=channel),
        mido.Message("note_on", note=pitch, velocity=0, time=duration, channel=channel),
    ]


def test_tempo_map_from_all_tracks_and_partial_last_beat(tmp_path: Path) -> None:
    path = write_midi(
        tmp_path,
        [
            [mido.MetaMessage("set_tempo", tempo=600000), *note(60, duration=1080)],
            [
                mido.MetaMessage("set_tempo", tempo=300000, time=240),
                mido.MetaMessage("set_tempo", tempo=900000, time=480),
            ],
        ],
    )
    chart = extract_midi(path)
    assert chart.tempo_bpm == 100
    assert chart.duration_beats == 2.25
    assert chart.beat_times_s == pytest.approx([0, 0.45, 1.05, 1.275])
    assert chart.melody[0].end_beat == 2.25


def test_short_parts_channels_bass_inversion_and_percussion(tmp_path: Path) -> None:
    messages = [
        mido.Message("note_on", channel=0, note=pitch, velocity=80) for pitch in (48, 53, 57)
    ]
    messages.extend(
        [
            mido.Message("note_on", channel=1, note=76, velocity=80),
            mido.Message("note_on", channel=9, note=100, velocity=80),
            mido.Message("note_off", channel=1, note=76, time=480),
            mido.Message("note_off", channel=9, note=100),
            *(mido.Message("note_off", channel=0, note=pitch) for pitch in (48, 53, 57)),
        ]
    )
    chart = extract_midi(write_midi(tmp_path, [messages], file_type=0))
    assert chart.time_signature == (4, 4)
    assert chart.tempo_bpm == 120
    assert [event.symbol for event in chart.chords] == ["F/C"]
    assert [event.pitch for event in chart.melody] == [76]


def test_melody_requires_sixteen_notes_when_available(tmp_path: Path) -> None:
    chart = extract_midi(
        write_midi(
            tmp_path,
            [
                [message for _ in range(16) for message in note(72)],
                note(96, duration=7680),
            ],
        )
    )
    assert [event.pitch for event in chart.melody] == [72] * 16


@pytest.mark.parametrize(
    ("messages", "error"),
    [
        ([mido.Message("note_off", note=60)], "Unmatched"),
        ([mido.Message("note_on", note=60, velocity=80)], "Unterminated"),
        (note(60, duration=0), "duration"),
        ([mido.MetaMessage("end_of_track", time=480)], "pitched notes"),
        ([mido.MetaMessage("set_tempo", tempo=0), *note(60)], "tempo"),
        (
            [mido.MetaMessage("time_signature", numerator=3, denominator=4, time=240), *note(60)],
            "Changing meters",
        ),
    ],
)
def test_invalid_midi_fails_with_actionable_error(
    tmp_path: Path, messages: list[object], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        extract_midi(write_midi(tmp_path, [messages]))


def test_asynchronous_midi_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="synchronous MIDI"):
        extract_midi(write_midi(tmp_path, [note(60)], file_type=2))


def test_explicit_melody_below_harmony_and_metadata(tmp_path: Path) -> None:
    path = write_midi(
        tmp_path,
        [
            [
                mido.MetaMessage("track_name", name="song"),
                mido.MetaMessage("key_signature", key="Dm"),
                *note(76),
            ],
            [mido.MetaMessage("track_name", name="Melody"), *note(48)],
        ],
    )
    chart = extract_midi(path)
    assert chart.title == "song"
    assert chart.key == "Dm"
    assert [event.pitch for event in chart.melody] == [48]


def test_cli_outputs_valid_chart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_midi(tmp_path, [note(72)])
    monkeypatch.setattr("sys.argv", ["midi", str(path)])
    main()
    assert Chart.model_validate_json(capsys.readouterr().out) == extract_midi(path)
