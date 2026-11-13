import asyncio
import csv
import json
import shutil
import struct
import sys
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest
from ghostband.chart import Chart
from ghostband.evaluation import extraction
from ghostband.evaluation.extraction import (
    BeatRow,
    DetectorScore,
    Reference,
    TrackResult,
    aam_chord_label,
    aam_key,
    aam_reference,
    aam_symbol,
    bootstrap_mean,
    detector_scores,
    dominant_key,
    guitarset_reference,
    guitarset_references,
    jams_symbol,
    mirex_key,
    parse_beatinfo,
    parse_segments,
    prepare_audio,
    read_audio_22k,
    read_midi_notes,
    score_group,
    seconds_chart,
    summarise,
    write_outputs,
)

sf = import_module("soundfile")
ROOT = Path(__file__).resolve().parents[2]
AAM_FIXTURES = ROOT / "fixtures" / "aam"
RATE = 22_050
FREQUENCY = {
    "C": (261.63, 329.63, 392.0),
    "G": (196.0, 246.94, 293.66),
    "Am": (220.0, 261.63, 329.63),
}


def variable_length(value: int) -> bytes:
    groups = [value & 0x7F]
    while value >> 7:
        value >>= 7
        groups.append(value & 0x7F | 0x80)
    return bytes(reversed(groups))


def midi_file(division: int, events: list[tuple[int, bytes]]) -> bytes:
    """One format-0 track from (delta ticks, event bytes) pairs."""
    body = b"".join(variable_length(delta) + event for delta, event in events)
    body += variable_length(0) + b"\xff\x2f\x00"
    return (
        b"MThd"
        + struct.pack(">IHHH", 6, 0, 1, division)
        + b"MTrk"
        + struct.pack(">I", len(body))
        + body
    )


def tempo(microseconds: int) -> bytes:
    return b"\xff\x51\x03" + microseconds.to_bytes(3)


def estimate(reference: Reference, **changes: object) -> Chart:
    """The reference chart as an extractor would return it, on the reference's own beats."""
    update: dict[str, object] = {"beat_times_s": list(reference.beats_s), **changes}
    return reference.chart.model_copy(update=update)


def triad_audio(chords: list[tuple[float, float, str]], seconds: float) -> np.ndarray:
    audio = np.zeros(int(seconds * RATE), dtype=np.float32)
    for start, end, name in chords:
        first, last = int(start * RATE), int(end * RATE)
        times = np.arange(last - first) / RATE
        audio[first:last] = sum(np.sin(2 * np.pi * hz * times) for hz in FREQUENCY[name]) / 3
    return audio


def small_reference(
    audio: Path,
    duration_s: float,
    chords: list[tuple[float, float, str]],
    melody: list[tuple[float, float, int]] | None = None,
    beats: tuple[float, ...] = (),
) -> Reference:
    name = audio.stem.removesuffix("_mix")
    return Reference(
        name=name,
        audio=audio,
        duration_s=duration_s,
        chart=seconds_chart(name, duration_s, chords=chords, melody=melody or []),
        beats_s=beats,
        downbeats_s=beats[::4],
    )


def test_midi_notes_follow_tempo_changes_and_ignore_other_events() -> None:
    quarter = 480
    data = midi_file(
        quarter,
        [
            (0, tempo(500_000)),
            # An unreadable key signature must not stop the notes from being read.
            (0, b"\xff\x59\x02\x0a\x01"),
            (0, b"\xf0\x03\x01\x02\xf7"),
            (0, b"\xc0\x05"),
            (0, b"\x90\x3c\x40"),
            (0, b"\x90\x40\x40"),
            (480, b"\x80\x3c\x40"),
            (480, tempo(250_000)),
            (0, b"\x90\x3e\x40"),
            # Running status: a second note-on with no status byte, then a zero-velocity release.
            (240, b"\x40\x00"),
            (240, b"\x3e\x00"),
        ],
    )
    # 60: 0 to one quarter at 0.5 s. 64 starts at 0 and releases at tick 1200, which is
    # 960 ticks (1.0 s) plus 240 ticks at 0.25 s per quarter. 62 starts at tick 960.
    assert read_midi_notes(data) == [
        (0.0, 0.5, 60),
        (0.0, 1.125, 64),
        (1.0, 1.25, 62),
    ]


def test_midi_reader_rejects_other_files() -> None:
    with pytest.raises(ValueError, match="Not a MIDI file"):
        read_midi_notes(b"RIFF0000WAVE0000")
    smpte = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 0xE728)
    with pytest.raises(ValueError, match="SMPTE"):
        read_midi_notes(smpte)
    whole = midi_file(480, [(0, b"\x90\x3c\x40"), (480, b"\x80\x3c\x40")])
    with pytest.raises(ValueError, match="Truncated"):
        read_midi_notes(whole[:-6])


def test_committed_aam_annotations_parse() -> None:
    rows = parse_beatinfo((AAM_FIXTURES / "0001_beatinfo.arff").read_text())
    assert len(rows) == 184
    assert rows[0] == BeatRow(0.0, 1, 1, "F:maj")
    assert rows[4] == BeatRow(2.6086957, 2, 1, "A#:maj")
    assert rows[8] == BeatRow(5.2173915, 3, 1, "A:min")
    assert sum(row.quarter == 1 for row in rows) == 46
    segments, end_s = parse_segments((AAM_FIXTURES / "0001_segments.arff").read_text())
    assert end_s == 120.0
    assert [segment.mark for segment in segments] == ["A", "A", "A", "A", "A", "C"]
    first, last = segments[0], segments[-1]
    assert (first.start_s, first.end_s, first.key) == (0.0, 15.652174, "Fmaj")
    assert first.instruments == ("TenorSax", "Ukulele", "OrganBass")
    assert first.generators == ("MelodyBow", "ChordArpeggios", "BassLine")
    assert (last.start_s, last.end_s, last.key) == (78.26087, 120.0, "Gmin")
    assert last.instruments == ("Flugelhorn", "OrganBass", "Drums")
    assert dominant_key(segments) == "Fmaj"


def test_repeated_bars_keep_the_later_copy() -> None:
    text = """@RELATION 'Beat information of 9999'

@ATTRIBUTE 'Start time in seconds' NUMERIC

0.0,1,1,'Amin'
0.5,1,2,'Amin'
1.0,2,1,'Cmaj'
1.5,2,2,'Cmaj'
1.0000004,1,1,'Cmaj'
1.5000004,1,2,'Cmaj'
2.0,1,3,'N.C.'
"""
    rows = parse_beatinfo(text)
    assert [(row.time_s, row.bar, row.chord) for row in rows] == [
        (0.0, 1, "A:min"),
        (0.5, 1, "A:min"),
        (1.0000004, 1, "C:maj"),
        (1.5000004, 1, "C:maj"),
        (2.0, 1, "N"),
    ]
    with pytest.raises(ValueError, match="end row"):
        parse_segments("0.0,'A',92,'Fmaj','[Flute]','[MelodyBow]'\n")


def test_chord_and_key_names_convert() -> None:
    assert aam_chord_label("Amin") == "A:min"
    assert aam_chord_label("A#maj") == "A#:maj"
    assert aam_chord_label("N.C.") == "N"
    assert [aam_symbol(label) for label in ("A:min", "A#:maj", "N")] == ["Am", "A#", "N.C."]
    assert (aam_key("Gmin"), aam_key("C#maj")) == ("Gm", "C#")
    assert (mirex_key("Gm"), mirex_key("F#")) == ("G minor", "F# major")
    for bad in ("Adim", "H", ""):
        with pytest.raises(ValueError, match="Unsupported"):
            aam_chord_label(bad)
    with pytest.raises(ValueError, match="Unsupported"):
        mirex_key("H")


@pytest.mark.parametrize(
    ("label", "symbol"),
    [
        ("D#:maj/1", "D#"),
        ("G#:maj6(*5)/1", "G#"),
        ("A:min7/b7", "Am"),
        ("B:hdim7(11)/4", "Bm"),
        ("F:dim", "Fm"),
        ("C:(1,5)/1", "C"),
        ("C:(1,b3,5)", "Cm"),
        ("C:sus2(b7)/1", "C"),
        ("E:7(#9,*5)/1", "E"),
        ("N", "N.C."),
    ],
)
def test_performed_chord_labels_reduce_to_triads(label: str, symbol: str) -> None:
    assert jams_symbol(label) == symbol


def test_unreadable_chord_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported chord label"):
        jams_symbol("H:maj")


def note_events(notes: list[tuple[int, int, int]]) -> list[tuple[int, bytes]]:
    marks = sorted(
        [(start, 1, pitch) for start, _, pitch in notes]
        + [(end, 0, pitch) for _, end, pitch in notes]
    )
    events: list[tuple[int, bytes]] = []
    previous = 0
    for tick, on, pitch in marks:
        status = 0x90 if on else 0x80
        events.append((tick - previous, bytes([status, pitch, 64])))
        previous = tick
    return events


def test_aam_reference_reads_truth_from_the_annotations_and_melody_midi(tmp_path: Path) -> None:
    root = tmp_path / "aam"
    (root / "annotations").mkdir(parents=True)
    (root / "midi").mkdir()
    for kind in ("beatinfo", "segments"):
        shutil.copy(AAM_FIXTURES / f"0001_{kind}.arff", root / "annotations")
    # 960 ticks are one second at 120 bpm. The sax is the melody only in segments A (to
    # 78.26 s), the flugelhorn only in segment C, so each one's note outside its segment
    # must be left out.
    files = {
        "TenorSax": [(960, 1440, 60), (76_800, 77_280, 61)],
        "Flugelhorn": [(9_600, 10_080, 62), (76_800, 77_280, 69)],
    }
    for instrument, notes in files.items():
        data = midi_file(480, [(0, tempo(500_000)), *note_events(notes)])
        (root / "midi" / f"0001_{instrument}.mid").write_bytes(data)
    reference = aam_reference("0001", root)
    assert reference.name == "0001"
    assert reference.audio == root / "mixes" / "0001_mix.flac"
    assert reference.duration_s == 120.0
    assert len(reference.beats_s) == 184
    assert len(reference.downbeats_s) == 46
    assert reference.downbeats_s[:3] == (0.0, 2.6086957, 5.2173915)
    assert reference.key == "F"
    chart = reference.chart
    assert [section.name for section in chart.sections] == ["A", "A", "A", "A", "A", "C"]
    assert chart.sections[-1].start_beat == 78.26087
    assert len(chart.chords) == 43
    assert (chart.chords[1].symbol, chart.chords[1].start_beat) == ("A#", 2.6086957)
    assert (chart.chords[2].symbol, chart.chords[2].end_beat) == ("Am", 6.5217395)
    assert [(n.start_beat, n.end_beat, n.pitch) for n in chart.melody] == [
        (1.0, 1.5, 60),
        (80.0, 80.5, 69),
    ]


def jams_json(
    chords: list[tuple[float, float, str]], *, performed: int = 1, duration: float = 10.0
) -> str:
    def chord_annotation(source: str, spans: list[tuple[float, float, str]]) -> dict[str, object]:
        return {
            "namespace": "chord",
            "annotation_metadata": {"data_source": source},
            "data": [
                {"time": start, "duration": length, "value": label, "confidence": None}
                for start, length, label in spans
            ],
        }

    def notes(string: str, spans: list[tuple[float, float, float]]) -> dict[str, object]:
        return {
            "namespace": "note_midi",
            "annotation_metadata": {"data_source": string},
            "data": [
                {"time": start, "duration": length, "value": pitch, "confidence": None}
                for start, length, pitch in spans
            ],
        }

    beats = [
        {
            "time": index * 0.5,
            "duration": 0.0,
            "value": {"position": index % 4 + 1, "measure": index // 4 + 1, "num_beats": 4},
            "confidence": None,
        }
        for index in range(6)
    ]
    return json.dumps(
        {
            "file_metadata": {"duration": duration},
            "annotations": [
                {"namespace": "pitch_contour", "annotation_metadata": {}, "data": {"0": []}},
                chord_annotation("", [(0.0, 10.0, "E:maj")]),
                *[chord_annotation("Semi-automatic", chords) for _ in range(performed)],
                notes("0", [(1.0, 0.5, 60.04)]),
                notes("1", [(0.5, 0.25, 52.9)]),
                {"namespace": "beat_position", "annotation_metadata": {}, "data": beats},
            ],
        }
    )


def test_guitarset_reference_uses_the_performed_chords(tmp_path: Path) -> None:
    jams = tmp_path / "00_BN1-129-Eb_comp.jams"
    jams.write_text(jams_json([(0.0, 4.0, "C:maj/1"), (4.0, 4.0, "A:min7/1"), (8.0, 3.0, "N")]))
    reference = guitarset_reference(jams, tmp_path / "x.wav")
    assert reference.name == "00_BN1-129-Eb_comp"
    assert reference.duration_s == 10.0
    assert [(c.start_beat, c.end_beat, c.symbol) for c in reference.chart.chords] == [
        (0.0, 4.0, "C"),
        (4.0, 8.0, "Am"),
        (8.0, 10.0, "N.C."),
    ]
    # The full performed labels keep the seventh that the chart's triad reduction drops.
    assert reference.chord_labels == (
        (0.0, 4.0, "C:maj/1"),
        (4.0, 8.0, "A:min7/1"),
        (8.0, 10.0, "N"),
    )
    assert [(n.start_beat, n.end_beat, n.pitch) for n in reference.chart.melody] == [
        (0.5, 0.75, 53),
        (1.0, 1.5, 60),
    ]
    assert reference.beats_s == (0.0, 0.5, 1.0, 1.5, 2.0, 2.5)
    assert reference.downbeats_s == (0.0, 2.0)
    assert reference.chart.tempo_bpm == 60


def test_guitarset_reference_needs_exactly_one_performed_annotation(tmp_path: Path) -> None:
    jams = tmp_path / "bad_comp.jams"
    for performed in (0, 2):
        jams.write_text(jams_json([(0.0, 4.0, "C:maj")], performed=performed))
        with pytest.raises(ValueError, match="one performed chord annotation"):
            guitarset_reference(jams, tmp_path / "x.wav")


def test_guitarset_references_split_comping_and_solo(tmp_path: Path) -> None:
    (tmp_path / "annotation").mkdir()
    for name in ("00_A-90-C_comp", "00_A-90-C_solo", "01_B-120-D_comp"):
        (tmp_path / "annotation" / f"{name}.jams").write_text(jams_json([(0.0, 10.0, "C:maj")]))
    groups = guitarset_references(tmp_path)
    assert [r.name for r in groups["comp"]] == ["00_A-90-C_comp", "01_B-120-D_comp"]
    assert [r.name for r in groups["solo"]] == ["00_A-90-C_solo"]
    assert groups["solo"][0].audio == tmp_path / "audio" / "00_A-90-C_solo_mic.wav"
    with pytest.raises(FileNotFoundError, match="fetch_guitarset"):
        guitarset_references(tmp_path / "empty")


def test_bootstrap_is_seeded_and_brackets_the_mean() -> None:
    assert bootstrap_mean([]) is None
    assert bootstrap_mean([0.25] * 7) == (0.25, 0.25, 0.25)
    values = [0.0, 1.0] * 50
    first = bootstrap_mean(values)
    assert first is not None
    mean, low, high = first
    assert mean == 0.5
    assert 0.35 < low < 0.5 < high < 0.65
    assert bootstrap_mean(values) == first


def test_beat_key_and_melody_scorers() -> None:
    reference = Reference(
        name="r",
        audio=Path("r.wav"),
        duration_s=10.0,
        chart=seconds_chart("r", 10.0, melody=[(1.0, 2.0, 60)]),
        beats_s=(0.5, 1.0, 1.5),
        downbeats_s=(0.5,),
        key="F",
    )
    chart = estimate(reference, beat_times_s=[0.52, 1.0, 2.0], key="C")
    # Two of three beats land within 70 ms, so precision and recall are both 2/3.
    assert extraction.SCORERS["beat_f"](reference, chart) == pytest.approx(2 / 3)
    assert (
        extraction.SCORERS["beat_f"](reference, chart.model_copy(update={"beat_times_s": []}))
        is None
    )
    # MIREX weights: the same key 1, a fifth apart 0.5, the relative minor 0.3, the parallel 0.2.
    for key, expected in (("F", 1.0), ("C", 0.5), ("Dm", 0.3), ("Fm", 0.2)):
        assert extraction.SCORERS["key"](reference, chart.model_copy(update={"key": key})) == (
            pytest.approx(expected)
        )
    assert extraction.SCORERS["key"](reference, chart.model_copy(update={"key": "H"})) is None
    assert extraction.SCORERS["melody_f1"](reference, chart) == 1.0
    assert (
        extraction.SCORERS["melody_f1"](reference, chart.model_copy(update={"melody": []})) == 0.0
    )
    silent = small_reference(Path("s.wav"), 10.0, [(0.0, 10.0, "C")])
    assert extraction.SCORERS["melody_f1"](silent, chart) is None
    assert extraction.SCORERS["key"](silent, chart) is None


def write_flac(path: Path, audio: np.ndarray, rate: int = RATE) -> Path:
    sf.write(str(path), audio, rate)
    return path


def test_scoring_a_group_counts_failures_and_decodes_flac_for_extractors(tmp_path: Path) -> None:
    audio = write_flac(tmp_path / "a_mix.flac", np.zeros(RATE, dtype=np.float32))
    first = small_reference(
        audio,
        20.0,
        [(0.0, 10.0, "C"), (10.0, 20.0, "G")],
        melody=[(1.0, 2.0, 60), (3.0, 4.0, 62)],
        beats=(0.0, 0.5, 1.0, 1.5),
    )
    first = replace(first, key="F")
    second = small_reference(tmp_path / "b.wav", 40.0, [(0.0, 40.0, "C")])
    seen: list[Path] = []

    async def good(wav: Path) -> Chart:
        seen.append(wav)
        # 120 bpm, so a beat is half a second and the recorded beats sit on the reference's.
        return Chart.model_validate(
            {
                "schema_version": 1,
                "title": "a",
                "source": "test",
                "rights": "unknown",
                "extractor": "manual",
                "tempo_bpm": 120,
                "beat_times_s": [0.0, 0.5, 1.0, 1.5],
                "time_signature": [4, 4],
                "key": "F",
                "sections": [{"name": "A", "start_beat": 0, "end_beat": 40}],
                "chords": [
                    {"symbol": "C", "start_beat": 0, "end_beat": 20},
                    {"symbol": "Am", "start_beat": 20, "end_beat": 40},
                ],
                "melody": [{"pitch": 72, "start_beat": 2.04, "end_beat": 4}],
                "duration_beats": 40,
            }
        )

    async def fails(wav: Path) -> Chart:
        raise RuntimeError(f"no model for {wav.name}")

    results = asyncio.run(
        score_group("aam", [first, second], {"good": good, "fails": fails}, tmp_path / "work")
    )
    assert [(r.extractor, r.name, r.failed) for r in results] == [
        ("good", "a", False),
        ("good", "b", False),
        ("fails", "a", True),
        ("fails", "b", True),
    ]
    assert seen[0] == tmp_path / "work" / "wav" / "a_mix.wav"
    assert seen[0].is_file()
    assert seen[1] == tmp_path / "b.wav"
    # Chords agree for half the time; the one extracted note matches one of two by pitch class.
    assert results[0].values["majmin"] == pytest.approx(0.5)
    assert results[0].values["melody_f1"] == pytest.approx(2 / 3)
    assert results[0].values["beat_f"] == 1.0
    assert results[0].values["segment_f"] == 1.0
    assert results[0].values["key"] == 1.0
    assert results[2].values == {}


def test_prepare_audio_leaves_wav_alone_and_decodes_once(tmp_path: Path) -> None:
    wav = tmp_path / "x.wav"
    assert prepare_audio(wav, tmp_path / "work") == wav
    flac = write_flac(tmp_path / "y.flac", np.full(RATE, 0.25, dtype=np.float32))
    decoded = prepare_audio(flac, tmp_path / "work")
    stamp = decoded.stat().st_mtime_ns
    assert prepare_audio(flac, tmp_path / "work") == decoded
    assert decoded.stat().st_mtime_ns == stamp
    samples, rate = sf.read(str(decoded))
    assert rate == RATE
    assert len(samples) == RATE
    assert samples[0] == pytest.approx(0.25, abs=1e-3)


def test_audio_is_read_as_mono_at_the_analysis_rate(tmp_path: Path) -> None:
    stereo = np.zeros((44_100, 2), dtype=np.float32)
    stereo[:, 0] = 0.5
    path = write_flac(tmp_path / "s.flac", stereo, 44_100)
    audio = read_audio_22k(path)
    assert audio.dtype == np.float32
    assert len(audio) == RATE
    assert float(np.abs(audio[2000:-2000]).mean()) == pytest.approx(0.25, abs=0.02)


def detector_reference(tmp_path: Path, chords: list[tuple[float, float, str]]) -> Reference:
    seconds = chords[-1][1]
    audio = write_flac(
        tmp_path / "t.flac", triad_audio([c for c in chords if c[2] in FREQUENCY], seconds)
    )
    beats = tuple(index * 0.5 for index in range(int(seconds * 2)))
    return small_reference(audio, seconds, chords, beats=beats)


def test_detector_agrees_with_clean_triads_and_finds_their_changes(tmp_path: Path) -> None:
    chords = [(0.0, 2.0, "C"), (2.0, 4.0, "G"), (4.0, 6.0, "Am")]
    (score,) = detector_scores([detector_reference(tmp_path, chords)])
    assert (score.windows, score.correct) == (12, 12)
    assert score.accuracy == 1.0
    assert score.changes == 2
    assert score.hit_rate == 1.0
    assert score.mean_abs_error_s is not None
    assert score.mean_abs_error_s < 0.15


def test_detector_changes_skip_no_chord_and_count_every_beat_window(tmp_path: Path) -> None:
    chords = [(0.0, 2.0, "C"), (2.0, 4.0, "G"), (4.0, 6.0, "N.C.")]
    (score,) = detector_scores([detector_reference(tmp_path, chords)], None)
    # Every half-second is a window, but only C to G is a change: G to N.C. is skipped.
    assert (score.windows, score.changes) == (12, 1)


def test_detector_scores_report_the_track_in_progress(tmp_path: Path) -> None:
    reference = detector_reference(tmp_path, [(0.0, 3.0, "C"), (3.0, 6.0, "G")])
    messages: list[str] = []
    detector_scores([reference], messages.append)
    assert messages == ["aam-detector 1/1 t"]


def test_detector_score_properties_handle_empty_counts() -> None:
    empty = DetectorScore("e", 10.0, 0, 0, 0, ())
    assert (empty.accuracy, empty.hit_rate, empty.mean_abs_error_s) == (None, None, None)
    some = DetectorScore("s", 10.0, 8, 6, 4, (0.1, 0.3))
    assert some.accuracy == 0.75
    assert some.hit_rate == 0.5
    assert some.mean_abs_error_s == pytest.approx(0.2)


def test_detector_needs_chord_truth(tmp_path: Path) -> None:
    reference = Reference("n", tmp_path / "n.wav", 5.0, seconds_chart("n", 5.0), (0.0,), (0.0,))
    with pytest.raises(ValueError, match="no chord truth"):
        extraction.score_detector(reference, np.zeros(1), np.zeros((12, 1)))


def result(duration_s: float, extractor: str = "x", **values: float | None) -> TrackResult:
    return TrackResult("aam", f"t{duration_s:g}", duration_s, extractor, dict(values))


def row(
    rows: list[extraction.Summary], subset: str, extractor: str, metric: str
) -> extraction.Summary:
    (match,) = [
        r
        for r in rows
        if (r.subset, r.extractor, r.metric) == (subset, extractor, metric) and r.group == "aam"
    ]
    return match


def test_summaries_cover_every_row_for_all_tracks_and_for_30_seconds_and_longer() -> None:
    results = [
        result(20.0, majmin=0.5, melody_f1=None),
        result(40.0, majmin=1.0, melody_f1=0.8),
        TrackResult("aam", "broken", 45.0, "x", {}, True),
        result(40.0, "y", majmin=0.25),
    ]
    rows = summarise(results, [])
    everything = row(rows, "all", "x", "majmin")
    assert (everything.tracks, everything.failed) == (2, 1)
    assert everything.interval is not None
    assert everything.interval[0] == 0.75
    long = row(rows, "30 s and longer", "x", "majmin")
    assert (long.tracks, long.failed, long.interval) == (1, 1, (1.0, 1.0, 1.0))
    assert row(rows, "all", "x", "melody_f1").tracks == 1
    assert row(rows, "all", "x", "beat_f").interval is None
    assert row(rows, "all", "y", "majmin").interval == (0.25, 0.25, 0.25)
    assert row(rows, "30 s and longer", "x", "melody_f1").tracks == 1
    # Both subsets, three groups of metrics, and the detector's three rows each.
    assert len([r for r in rows if r.group == "guitarset-comp"]) == 0
    assert len([r for r in rows if r.group == "aam-detector"]) == 6


def test_detector_rows_average_tracks() -> None:
    detector = [
        DetectorScore("a", 20.0, 10, 5, 2, (0.2,)),
        DetectorScore("b", 40.0, 10, 10, 0, ()),
    ]
    rows = summarise([], detector)
    found = {(r.subset, r.metric): r for r in rows if r.group == "aam-detector"}
    accuracy = found[("all", "majmin_accuracy")]
    assert (accuracy.tracks, accuracy.interval and accuracy.interval[0]) == (2, 0.75)
    assert found[("all", "change_hit_rate")].tracks == 1
    assert found[("all", "change_abs_error_s")].interval == (0.2, 0.2, 0.2)
    assert found[("30 s and longer", "majmin_accuracy")].interval == (1.0, 1.0, 1.0)
    assert found[("30 s and longer", "change_hit_rate")].interval is None


def test_outputs_are_written_and_reproducible(tmp_path: Path) -> None:
    results = [
        TrackResult("aam", "0001", 120.0, "permissive", {"majmin": 0.5, "melody_f1": None}),
        TrackResult("guitarset-comp", "c", 31.0, "permissive", {"majmin": 0.25, "beat_f": 1.0}),
        TrackResult("guitarset-solo", "s", 12.0, "permissive", {}, True),
    ]
    detector = [DetectorScore("0001", 120.0, 4, 3, 1, (0.05,))]
    summaries = summarise(results, detector)
    first = write_outputs(tmp_path / "one", results, detector, summaries)
    second = write_outputs(tmp_path / "two", results, detector, summaries)
    assert [p.name for p in first] == [
        "extraction.md",
        "extraction.csv",
        "extraction_tracks.csv",
        "detector_aam.csv",
    ]
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]
    report = first[0].read_text()
    assert "GuitarSet has no section annotations, so no segment metric is reported" in report
    assert "## GuitarSet comping excerpts" in report
    assert "## Chord detector on AAM" in report
    assert "30 s and longer" in report
    assert "| Subset | Extractor | Metric | Tracks | Failed | Mean | Low | High |" in report
    with first[1].open(newline="") as handle:
        table = list(csv.DictReader(handle))
    pick = next(
        r
        for r in table
        if (r["group"], r["subset"], r["metric"]) == ("guitarset-comp", "30 s and longer", "majmin")
    )
    assert (pick["tracks"], pick["mean"], pick["low"], pick["high"]) == (
        "1",
        "0.250",
        "0.250",
        "0.250",
    )
    empty = next(r for r in table if (r["group"], r["metric"]) == ("aam", "melody_f1"))
    assert (empty["tracks"], empty["mean"]) == ("0", "")
    with first[2].open(newline="") as handle:
        tracks = list(csv.DictReader(handle))
    failed = [r for r in tracks if r["failed"] == "true"]
    assert [(r["track"], r["value"]) for r in failed] == [("s", "")]
    with first[3].open(newline="") as handle:
        (detector_row,) = list(csv.DictReader(handle))
    assert detector_row["accuracy"] == "0.75"
    assert detector_row["found"] == "1"


def make_datasets(tmp_path: Path) -> tuple[Path, Path]:
    guitarset, aam = tmp_path / "guitarset", tmp_path / "aam"
    (guitarset / "annotation").mkdir(parents=True)
    (guitarset / "audio").mkdir()
    for name in ("00_A-90-C_comp", "00_A-90-C_solo"):
        (guitarset / "annotation" / f"{name}.jams").write_text(
            jams_json([(0.0, 10.0, "C:maj/1")], duration=6.0)
        )
        sf.write(
            str(guitarset / "audio" / f"{name}_mic.wav"), np.zeros(RATE, dtype=np.float32), RATE
        )
    (aam / "annotations").mkdir(parents=True)
    (aam / "midi").mkdir()
    (aam / "mixes").mkdir()
    (aam / "annotations" / "0007_segments.arff").write_text(
        "0.0,'A',120,'Cmaj','[Flute]','[MelodyBow]'\n6.0,'end',0,'','[]','[]'\n"
    )
    names = ["Cmaj"] * 4 + ["Gmaj"] * 4 + ["Amin"] * 4
    (aam / "annotations" / "0007_beatinfo.arff").write_text(
        "".join(f"{i * 0.5},{i // 4 + 1},{i % 4 + 1},'{name}'\n" for i, name in enumerate(names))
    )
    notes = midi_file(480, [(0, tempo(500_000)), *note_events([(960, 1440, 60)])])
    (aam / "midi" / "0007_Flute.mid").write_bytes(notes)
    write_flac(
        aam / "mixes" / "0007_mix.flac",
        triad_audio([(0.0, 2.0, "C"), (2.0, 4.0, "G"), (4.0, 6.0, "Am")], 6.0),
    )
    return guitarset, aam


def test_datasets_are_evaluated_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    guitarset, aam = make_datasets(tmp_path)
    reference = aam_reference("0007", aam)
    paths: list[Path] = []

    async def perfect(wav: Path) -> Chart:
        paths.append(wav)
        return reference.chart

    monkeypatch.setattr(extraction, "permissive_extractor", lambda: perfect)
    output = tmp_path / "out"
    argv = [
        "extraction",
        "--output-dir",
        str(output),
        "--guitarset",
        str(guitarset),
        "--aam",
        str(aam),
        "--work-dir",
        str(tmp_path / "work"),
        "--extractor",
        "permissive",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    extraction.main()
    printed = capsys.readouterr().out.split()
    assert printed == [
        str(output / name)
        for name in ("extraction.md", "extraction.csv", "extraction_tracks.csv", "detector_aam.csv")
    ]
    assert paths[-1] == tmp_path / "work" / "wav" / "0007_mix.wav"
    with (output / "extraction.csv").open(newline="") as handle:
        table = list(csv.DictReader(handle))
    scored = {(r["group"], r["extractor"], r["metric"]): r for r in table if r["subset"] == "all"}
    assert scored[("aam", "permissive", "majmin")]["mean"] == "1.000"
    assert scored[("aam", "permissive", "segment_f")]["mean"] == "1.000"
    assert scored[("aam", "permissive", "key")]["mean"] == "1.000"
    assert scored[("aam", "permissive", "beat_f")]["tracks"] == "0"
    assert scored[("guitarset-comp", "permissive", "majmin")]["tracks"] == "1"
    assert scored[("guitarset-solo", "permissive", "melody_f1")]["tracks"] == "1"
    assert scored[("aam-detector", "chord_detector", "majmin_accuracy")]["mean"] == "1.000"
    assert (output / "detector_aam.csv").read_text().count("\n") == 2


def test_missing_sheetsage2_models_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHOSTBAND_SHEETSAGE2_CACHE", str(tmp_path / "absent"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["extraction", "--output-dir", str(tmp_path / "out"), "--extractor", "sheetsage2"],
    )
    with pytest.raises(SystemExit) as exit_info:
        extraction.main()
    assert exit_info.value.code == 2
