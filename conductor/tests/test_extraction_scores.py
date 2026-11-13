import asyncio
import sys
from pathlib import Path

import pytest
from ghostband.chart import Chart
from ghostband.evaluation import extraction
from ghostband.evaluation.extraction import Reference

ROOT = Path(__file__).resolve().parents[2]


def chart(
    *,
    melody: list[tuple[int, float, float]] | None = None,
    chords: list[tuple[str, float, float]] | None = None,
    sections: list[tuple[str, float, float]] | None = None,
    beat_times_s: list[float] | None = None,
) -> Chart:
    """A 120 bpm chart, 48 beats long, from (pitch, start, end) style tuples."""
    return Chart.model_validate(
        {
            "schema_version": 1,
            "title": "Scored",
            "source": "test",
            "rights": "own-work",
            "extractor": "manual",
            "tempo_bpm": 120,
            "beat_times_s": beat_times_s or [],
            "time_signature": [4, 4],
            "key": "C",
            "sections": [
                {"name": name, "start_beat": start, "end_beat": end}
                for name, start, end in sections or [("A", 0, 48)]
            ],
            "chords": [
                {"symbol": symbol, "start_beat": start, "end_beat": end}
                for symbol, start, end in chords or []
            ],
            "melody": [
                {"pitch": pitch, "start_beat": start, "end_beat": end}
                for pitch, start, end in melody or []
            ],
            "duration_beats": 48,
        }
    )


def test_chart_beats_extend_the_recorded_grid_inbeat_seconds() -> None:
    scored = chart(beat_times_s=[1.0, 1.5, 2.0])
    assert extraction.beat_seconds(scored, [-1, 0, 0.5, 2, 3]).tolist() == [
        0.5,
        1.0,
        1.25,
        2.0,
        2.5,
    ]
    assert extraction.beat_seconds(chart(), [0, 3]).tolist() == [0.0, 1.5]


def test_melody_onsets_match_by_pitch_class_inside_fifty_milliseconds() -> None:
    reference = chart(melody=[(60, 0, 1), (62, 1, 2), (64, 2, 3), (65, 3, 4)])
    grid = [0.02 + 0.5 * beat for beat in range(8)]
    estimate = chart(melody=[(72, 0, 1), (61, 1, 2), (63, 2, 3), (65, 3, 4)], beat_times_s=grid)
    assert extraction.melody_onset_f1(reference, estimate) == pytest.approx(0.5)
    late = chart(
        melody=[(60, 0, 1), (62, 1, 2), (64, 2, 3)], beat_times_s=[0.06 + 0.5 * b for b in range(8)]
    )
    assert extraction.melody_onset_f1(reference, late) == 0
    assert extraction.melody_onset_f1(reference, chart()) == 0


def test_majmin_counts_reference_time_with_the_same_triad() -> None:
    reference = chart(chords=[("Cmaj7", 0, 4), ("G7", 4, 8), ("Am", 8, 12)])
    estimate = chart(chords=[("C", 0, 4), ("G", 4, 6), ("Dm", 6, 12)])
    assert extraction.majmin_agreement(reference, estimate) == pytest.approx(0.5)
    reduced = chart(chords=[("C/E", 0, 4), ("G", 4, 8), ("Am7", 8, 12)])
    assert extraction.majmin_agreement(reference, reduced) == 1
    assert extraction.majmin_agreement(reference, chart()) == 0


def test_segment_boundaries_match_within_threebeat_seconds() -> None:
    reference = chart(sections=[("A", 0, 16), ("B", 16, 32), ("C", 32, 48)])
    estimate = chart(sections=[("A", 0, 17), ("B", 17, 39), ("C", 39, 48)])
    assert extraction.segment_f(reference, estimate) == pytest.approx(0.5)
    near = chart(sections=[("A", 0, 17), ("B", 17, 37.8), ("C", 37.8, 48)])
    assert extraction.segment_f(reference, near) == 1
    assert extraction.segment_f(chart(), chart()) == 1
    assert extraction.segment_f(reference, chart()) == 0


def test_boundaries_were_also_scored_within_half_a_second() -> None:
    reference = chart(sections=[("A", 0, 16), ("B", 16, 32), ("C", 32, 48)])
    # At 120 bpm the estimate's boundaries sit 0.2 s late, inside half a second.
    close = chart(sections=[("A", 0, 16.4), ("B", 16.4, 32), ("C", 32, 48)])
    assert extraction.segment_f_0p5(reference, close) == 1
    late = chart(sections=[("A", 0, 18), ("B", 18, 40), ("C", 40, 48)])
    assert extraction.segment_f_0p5(reference, late) == 0


def test_chord_root_and_sevenths_follow_the_performed_chords() -> None:
    reference = chart(chords=[("Cmaj7", 0, 4), ("G7", 4, 8), ("Am", 8, 12)])
    estimate = chart(chords=[("C", 0, 4), ("G7", 4, 8), ("Am7", 8, 12)])
    scored = Reference("t", Path("t.wav"), 12.0, reference, (), ())
    assert extraction.SCORERS["root"](scored, estimate) == 1
    # The roots all match; only the G7 keeps its seventh.
    assert extraction.SCORERS["sevenths"](scored, estimate) == pytest.approx(1 / 3)


def test_downbeats_are_scored_from_each_bar() -> None:
    reference = chart()
    scored = Reference("t", Path("t.wav"), 8.0, reference, (), (0.0, 2.0))
    estimate = chart(beat_times_s=[0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5])
    assert extraction.SCORERS["downbeat_f"](scored, estimate) == 1


def test_evaluation_reports_each_fixture_and_the_acceptance_floors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extracted_melody = True

    async def extract(command: str, wav_path: Path) -> dict[str, object]:
        assert command == "chart"
        data = Chart.model_validate_json(wav_path.with_suffix(".golden.json").read_text())
        if not extracted_melody:
            data = data.model_copy(update={"melody": []})
        return data.model_dump(mode="json")

    monkeypatch.setattr(extraction, "run_permissive", extract)
    report, passed = asyncio.run(extraction.evaluate(ROOT / "fixtures"))
    assert passed
    assert report.count("| 1.000 | 1.000 | 1.000 |") == 6
    assert "| greensleeves.wav | 1.000 |" in report
    assert "Melody onset F1 >= 0.50: True" in report
    extracted_melody = False
    report, passed = asyncio.run(extraction.evaluate(ROOT / "fixtures"))
    assert not passed
    assert "| mean | 0.000 | 1.000 | 1.000 |" in report
    assert "Melody onset F1 >= 0.50: False" in report
    monkeypatch.setattr(sys, "argv", ["extraction", "--fixtures", str(ROOT / "fixtures")])
    with pytest.raises(SystemExit) as exit_info:
        extraction.main()
    assert exit_info.value.code == 1


def test_evaluation_needs_fixture_audio(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No fixture WAVs"):
        asyncio.run(extraction.evaluate(tmp_path))
