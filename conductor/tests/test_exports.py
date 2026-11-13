import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import pytest
from click.testing import CliRunner
from ghostband.chart import Chart
from ghostband.cli import main
from ghostband.export import (
    TakeMetadata,
    chart_to_abc,
    chart_to_chords_midi,
    chart_to_melody_midi,
    chart_to_midi,
    export_bundle,
    share_safe,
    take_path,
    write_take_sidecar,
)

ROOT = Path(__file__).resolve().parents[2]
mido = import_module("mido")


def small_chart(**overrides: object) -> Chart:
    data: dict[str, object] = {
        "schema_version": 1,
        "title": "Test tune",
        "source": "user import",
        "rights": "own-work",
        "extractor": "manual",
        "tempo_bpm": 120,
        "beat_times_s": [],
        "time_signature": [4, 4],
        "key": "C",
        "sections": [{"name": "A", "start_beat": 0, "end_beat": 8}],
        "chords": [
            {"symbol": "C", "start_beat": 0, "end_beat": 4},
            {"symbol": "G7", "start_beat": 4, "end_beat": 8},
        ],
        "melody": [
            {"pitch": 60, "start_beat": 0, "end_beat": 2},
            {"pitch": 62, "start_beat": 2, "end_beat": 4},
            {"pitch": 64, "start_beat": 4, "end_beat": 6},
            {"pitch": 67, "start_beat": 6, "end_beat": 8},
        ],
        "duration_beats": 8,
    }
    data.update(overrides)
    return Chart.model_validate(data)


@pytest.mark.parametrize("path", sorted((ROOT / "charts").glob("*.chart.json")))
def test_bundled_exports_are_stable_and_balanced(path: Path) -> None:
    chart = Chart.model_validate_json(path.read_text())
    encoded = chart_to_midi(chart)
    assert chart_to_midi(chart) == encoded
    assert (ROOT / "fixtures" / path.name.replace(".chart.json", ".mid")).read_bytes() == encoded
    midi = mido.MidiFile(file=BytesIO(encoded))
    numerator, denominator = chart.time_signature
    quarters = 4 / denominator * (3 if denominator == 8 and numerator % 3 == 0 else 1)
    assert abs(midi.length - chart.duration_beats * 60 / chart.tempo_bpm) < 0.001
    assert midi.tracks[0][2].numerator == numerator
    assert midi.tracks[0][2].denominator == denominator
    for track in midi.tracks[1:]:
        active: Counter[int] = Counter()
        tick = 0
        starts: list[tuple[int, int]] = []
        for message in track:
            tick += message.time
            if message.type == "note_on":
                active[message.note] += 1
                starts.append((tick, message.note))
            elif message.type == "note_off":
                active[message.note] -= 1
                assert active[message.note] >= 0
        assert all(count == 0 for count in active.values())
        assert tick == round(chart.duration_beats * quarters * 960)
        if track[0].name == "Melody":
            expected = sorted(
                (round(note.start_beat * quarters * 960), note.pitch) for note in chart.melody
            )
            assert sorted(starts) == expected


@pytest.mark.parametrize(
    ("field", "value"), [("rights", "unknown"), ("source", ""), ("source", "   ")]
)
def test_chart_checker_rejects_unpublishable_chart(
    tmp_path: Path,
    field: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = json.loads((ROOT / "charts/greensleeves.chart.json").read_text())
    data[field] = value
    path = tmp_path / "invalid.chart.json"
    path.write_text(json.dumps(data))
    checker = import_module("scripts.check_charts")
    monkeypatch.setattr("sys.argv", ["check_charts.py", str(path)])
    assert asyncio.run(checker.main()) == 1
    assert field in capsys.readouterr().err


def test_export_cli(tmp_path: Path) -> None:
    output = tmp_path / "out.mid"
    chart = ROOT / "charts/greensleeves.chart.json"
    result = CliRunner().invoke(main, ["export-midi", str(chart), "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.read_bytes() == chart_to_midi(Chart.model_validate_json(chart.read_text()))


def test_same_pitch_overlap_and_retrigger() -> None:
    data = json.loads((ROOT / "charts/ode-to-joy.chart.json").read_text())
    data["chords"] = [{"start_beat": 0, "end_beat": 4, "symbol": "N.C."}]
    data["melody"] = [
        {"start_beat": 0, "end_beat": 2, "pitch": 60},
        {"start_beat": 1, "end_beat": 3, "pitch": 60},
        {"start_beat": 3, "end_beat": 4, "pitch": 60},
    ]
    midi = mido.MidiFile(file=BytesIO(chart_to_midi(Chart.model_validate(data))))
    tick = 0
    events: list[tuple[int, str]] = []
    for message in midi.tracks[2]:
        tick += message.time
        if message.type in {"note_on", "note_off"}:
            events.append((tick, message.type))
    assert events == [(0, "note_on"), (2880, "note_off"), (2880, "note_on"), (3840, "note_off")]
    assert all(message.type != "note_on" for message in midi.tracks[1])


def test_share_export_drops_melody_with_unknown_rights() -> None:
    chart = small_chart(rights="unknown")
    shared = share_safe(chart)
    assert shared.melody == []
    assert shared.chords == chart.chords
    assert [section.name for section in shared.sections] == ["A"]
    assert shared.tempo_bpm == 120
    assert shared.key == "C"


def test_share_export_keeps_public_domain_melody() -> None:
    chart = small_chart(rights="public-domain")
    assert share_safe(chart).melody == chart.melody


def test_export_share_cli_writes_no_melody(tmp_path: Path) -> None:
    source = tmp_path / "tune.chart.json"
    source.write_text(small_chart(rights="unknown").model_dump_json(indent=2), encoding="utf-8")
    output = tmp_path / "shared.chart.json"
    result = CliRunner().invoke(main, ["export", str(source), "--share", "-o", str(output)])
    assert result.exit_code == 0, result.output
    exported = json.loads(output.read_text())
    assert exported["melody"] == []
    assert [chord["symbol"] for chord in exported["chords"]] == ["C", "G7"]


def test_chart_to_abc_writes_hand_checked_score() -> None:
    assert (
        chart_to_abc(small_chart())
        == 'X:1\nT:Test tune\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n% Section: A\n"C"C4 D4 |\n"G7"E4 G4 |\n'
    )


def test_chart_to_abc_fills_gaps_with_rests() -> None:
    chart = small_chart(
        melody=[
            {"pitch": 60, "start_beat": 0, "end_beat": 3},
            {"pitch": 64, "start_beat": 3, "end_beat": 6},
        ]
    )
    assert chart_to_abc(chart).endswith('% Section: A\n"C"C6 E2- |\n"G7"E4 z4 |\n')


def test_chart_to_abc_ties_notes_across_bars() -> None:
    chart = small_chart(
        melody=[
            {"pitch": 60, "start_beat": 0, "end_beat": 6},
            {"pitch": 64, "start_beat": 6, "end_beat": 8},
        ]
    )
    assert chart_to_abc(chart).endswith('% Section: A\n"C"C8- |\n"G7"C4 E4 |\n')


def test_split_midi_parts_hold_one_part_each() -> None:
    chart = small_chart()
    chords = mido.MidiFile(file=BytesIO(chart_to_chords_midi(chart)))
    melody = mido.MidiFile(file=BytesIO(chart_to_melody_midi(chart)))
    assert [track[0].name for track in chords.tracks] == ["Test tune", "Harmony"]
    assert [track[0].name for track in melody.tracks] == ["Test tune", "Melody"]
    assert [message.note for message in melody.tracks[1] if message.type == "note_on"] == [
        60,
        62,
        64,
        67,
    ]
    assert any(message.type == "note_on" and message.time == 0 for message in chords.tracks[1])


def test_export_bundle_writes_parts_and_shares_safely(tmp_path: Path) -> None:
    directory = tmp_path / "exports"
    exported = export_bundle(small_chart(rights="unknown"), directory, share=True)
    assert [path.name for path in exported] == [
        "chords.mid",
        "melody.mid",
        "test-tune.abc",
        "test-tune.chart.json",
    ]
    assert json.loads((directory / "test-tune.chart.json").read_text())["melody"] == []
    assert (directory / "test-tune.abc").read_text().endswith('% Section: A\n"C"z8 |\n"G7"z8 |\n')
    melody = mido.MidiFile(file=BytesIO((directory / "melody.mid").read_bytes()))
    assert all(message.type != "note_on" for track in melody.tracks[1:] for message in track)


def test_export_bundle_cli_writes_four_files(tmp_path: Path) -> None:
    source = tmp_path / "tune.chart.json"
    source.write_text(small_chart().model_dump_json(indent=2), encoding="utf-8")
    result = CliRunner().invoke(main, ["export", str(source), "--bundle", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [
        "chords.mid",
        "melody.mid",
        "test-tune.abc",
        "test-tune.chart.json",
    ]


def test_take_path_and_sidecar_name_the_recording_settings(tmp_path: Path) -> None:
    started = datetime(2026, 11, 6, 1, 30, 0, tzinfo=UTC)
    take = take_path(tmp_path / "exports", "Ode to Joy", "Warm jazz / chiptune", started)
    assert take == tmp_path / "exports/ode-to-joy-warm-jazz-chiptune-20261106-013000.wav"
    sidecar = write_take_sidecar(
        take,
        TakeMetadata(
            model="mrt2_small",
            preset="Warm jazz / chiptune",
            seed_rotation=[11, 23, 37],
        ),
    )
    assert sidecar == take.with_name(take.name + ".json")
    assert json.loads(sidecar.read_text()) == {
        "model": "mrt2_small",
        "preset": "Warm jazz / chiptune",
        "seed_rotation": [11, 23, 37],
        "version": version("ghostband"),
    }
