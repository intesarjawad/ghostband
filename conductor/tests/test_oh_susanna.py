from pathlib import Path

from ghostband.chart import Chart


def test_holt_1848_vocal_transcription() -> None:
    # C. Holt Jr. (1848), pp. 2-4: solo verse, then chorus 1st Voice.
    # Durations count quarter notes; the chorus repeat is printed on p. 4.
    verse = [
        ((71, 74, 74, 76), (0.5, 0.5, 0.5, 0.5)),
        ((74, 71, 67, 69), (0.5, 0.5, 0.75, 0.25)),
        ((71, 71, 69, 67), (0.5, 0.5, 0.5, 0.5)),
        ((69, 67, 69), (1.5, 0.25, 0.25)),
        ((71, 74, 74, 76), (0.5, 0.5, 0.75, 0.25)),
        ((74, 71, 67, 69), (0.5, 0.5, 0.75, 0.25)),
        ((71, 71, 69, 69), (0.5, 0.5, 0.5, 0.5)),
        ((67, 67, 69), (1.5, 0.25, 0.25)),
        ((71, 74, 74, 76), (0.5, 0.5, 0.5, 0.5)),
        ((74, 71, 67, 69), (0.5, 0.5, 0.75, 0.25)),
        ((71, 71, 69, 67), (0.5, 0.5, 0.5, 0.5)),
        ((69, 67, 69), (1.5, 0.25, 0.25)),
        ((71, 74, 74, 76), (0.5, 0.5, 0.5, 0.5)),
        ((74, 71, 67, 69), (0.5, 0.5, 0.75, 0.25)),
        ((71, 71, 69, 69), (0.5, 0.5, 0.5, 0.5)),
        ((67,), (1.0,)),
    ]
    chorus = [
        ((72, 72), (1.0, 1.0)),
        ((76, 76, 76), (0.5, 1.0, 0.5)),
        ((74, 74, 71, 67), (0.5, 0.5, 0.5, 0.5)),
        ((69, 67, 69), (1.5, 0.25, 0.25)),
        ((71, 74, 74, 76), (0.5, 0.5, 0.5, 0.5)),
        ((74, 71, 67, 69), (0.5, 0.5, 0.75, 0.25)),
        ((71, 71, 69, 69), (0.5, 0.5, 0.5, 0.5)),
        ((67,), (1.0,)),
    ]
    path = Path(__file__).parents[2] / "charts" / "oh-susanna.chart.json"
    chart = Chart.model_validate_json(path.read_text())
    assert chart.time_signature == (2, 4)
    assert chart.key == "G"
    assert chart.duration_beats == 64.5
    assert [(n.pitch, n.start_beat, n.end_beat) for n in chart.melody[:2]] == [
        (67, 0, 0.25),
        (69, 0.25, 0.5),
    ]
    for index, (pitches, durations) in enumerate(verse + chorus + chorus):
        start = 0.5 + index * 2
        notes = [n for n in chart.melody if start <= n.start_beat < start + 2]
        assert tuple(n.pitch for n in notes) == pitches, f"performed bar {index + 1}"
        assert tuple(n.end_beat - n.start_beat for n in notes) == durations
        position = start
        for note in notes:
            assert note.start_beat == position
            position = note.end_beat
        assert position == start + sum(durations)
    # Each written quarter rest survives, including both passes of the chorus.
    assert [n.end_beat for n in chart.melody if n.pitch == 67 and n.end_beat % 2 == 1.5] == [
        31.5,
        47.5,
        63.5,
    ]
    assert len(chart.melody) == 110
