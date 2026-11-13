from itertools import combinations, permutations

import pytest
from ghostband.chords import Chord, ChordQuality, NoChord, parse_chord, voice_chord
from hypothesis import given, settings
from hypothesis import strategies as st

SYMBOL_CASES = (
    ("C", 0, (0, 4, 7), 0),
    ("D", 2, (2, 6, 9), 2),
    ("E", 4, (4, 8, 11), 4),
    ("F", 5, (5, 9, 0), 5),
    ("G", 7, (7, 11, 2), 7),
    ("A", 9, (9, 1, 4), 9),
    ("B", 11, (11, 3, 6), 11),
    ("C#", 1, (1, 5, 8), 1),
    ("Db", 1, (1, 5, 8), 1),
    ("F#", 6, (6, 10, 1), 6),
    ("Gb", 6, (6, 10, 1), 6),
    ("B#", 0, (0, 4, 7), 0),
    ("Cb", 11, (11, 3, 6), 11),
    ("E#", 5, (5, 9, 0), 5),
    ("Fb", 4, (4, 8, 11), 4),
    ("Am", 9, (9, 0, 4), 9),
    ("Bm", 11, (11, 2, 6), 11),
    ("Cm", 0, (0, 3, 7), 0),
    ("Dm", 2, (2, 5, 9), 2),
    ("Em", 4, (4, 7, 11), 4),
    ("Fm", 5, (5, 8, 0), 5),
    ("Gm", 7, (7, 10, 2), 7),
    ("C7", 0, (0, 4, 7, 10), 0),
    ("D7", 2, (2, 6, 9, 0), 2),
    ("E7", 4, (4, 8, 11, 2), 4),
    ("F7", 5, (5, 9, 0, 3), 5),
    ("G7", 7, (7, 11, 2, 5), 7),
    ("A7", 9, (9, 1, 4, 7), 9),
    ("B7", 11, (11, 3, 6, 9), 11),
    ("Cmaj7", 0, (0, 4, 7, 11), 0),
    ("Cm7", 0, (0, 3, 7, 10), 0),
    ("Cdim", 0, (0, 3, 6), 0),
    ("Cdim7", 0, (0, 3, 6, 9), 0),
    ("Cm7b5", 0, (0, 3, 6, 10), 0),
    ("Caug", 0, (0, 4, 8), 0),
    ("Csus2", 0, (0, 2, 7), 0),
    ("Csus4", 0, (0, 5, 7), 0),
    ("C7sus4", 0, (0, 5, 7, 10), 0),
    ("N.C.", None, (), None),
    ("C/E", 0, (0, 4, 7), 4),
    ("D/F#", 2, (2, 6, 9), 6),
    ("Bb/D", 10, (10, 2, 5), 2),
    ("F/A", 5, (5, 9, 0), 9),
    ("Am/C", 9, (9, 0, 4), 0),
    ("G/B", 7, (7, 11, 2), 11),
    ("C7/Bb", 0, (0, 4, 7, 10), 10),
    ("F#m7/C#", 6, (6, 9, 1, 4), 1),
    ("Abmaj7/Eb", 8, (8, 0, 3, 7), 3),
)

NOTE_NAMES = tuple(letter + accidental for letter in "ABCDEFG" for accidental in ("", "#", "b"))
CHORDS = st.builds(
    Chord,
    root=st.sampled_from(NOTE_NAMES),
    quality=st.sampled_from(ChordQuality),
    bass=st.none() | st.sampled_from(NOTE_NAMES),
)


@pytest.mark.parametrize(("symbol", "root", "pitches", "bass"), SYMBOL_CASES)
def test_symbol_table(
    symbol: str, root: int | None, pitches: tuple[int, ...], bass: int | None
) -> None:
    chord = parse_chord(symbol)
    assert str(chord) == symbol
    if root is None:
        assert isinstance(chord, NoChord)
    else:
        assert isinstance(chord, Chord)
        assert chord.root_pitch_class == root
        assert chord.pitch_classes == pitches
        assert chord.bass_pitch_class == bass


@settings(derandomize=True)
@given(CHORDS | st.just(NoChord()))
def test_symbol_round_trip(chord: Chord | NoChord) -> None:
    assert parse_chord(str(chord)) == chord


@pytest.mark.parametrize(
    ("symbol", "canonical"),
    [("Cmaj", "C"), ("Cmin", "Cm"), ("Cmin7", "Cm7"), ("Csus", "Csus4")],
)
def test_quality_aliases(symbol: str, canonical: str) -> None:
    assert str(parse_chord(symbol)) == canonical


@pytest.mark.parametrize(
    "symbol",
    [
        "",
        "H",
        "c",
        "C9",
        "C13",
        "C6",
        "C##",
        "Cbb",
        "Cunknown",
        "C/",
        "C/H",
        "C/E/G",
        "N.C./C",
        "NC",
        " C",
        "C ",
        "C\n",
        "C maj7",
        "C7b9",
        "Cadd9",
    ],
)
def test_unsupported_symbols_fail_loudly(symbol: str) -> None:
    with pytest.raises(ValueError, match="Unsupported chord symbol"):
        parse_chord(symbol)


@pytest.mark.parametrize("root", ["H", "", "C##", "c"])
def test_invalid_root_is_rejected(root: str) -> None:
    with pytest.raises(ValueError, match="Invalid note name"):
        Chord(root)


def test_invalid_bass_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid note name"):
        Chord("C", bass="H")


@pytest.mark.parametrize(("symbol", "root", "pitches", "bass"), SYMBOL_CASES)
def test_voicing_preserves_harmony(
    symbol: str, root: int | None, pitches: tuple[int, ...], bass: int | None
) -> None:
    voicing = voice_chord(parse_chord(symbol))
    if root is None:
        assert voicing == ()
        return
    assert 36 <= voicing[0] <= 47
    assert voicing[0] % 12 == bass
    assert all(55 <= note <= 70 for note in voicing[1:])
    assert tuple(sorted(voicing[1:])) == voicing[1:]
    assert len(voicing[1:]) == len(pitches)
    assert {note % 12 for note in voicing[1:]} == set(pitches)


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("C", (36, 55, 60, 64)),
        ("C/E", (40, 55, 60, 64)),
        ("C/F#", (42, 55, 60, 64)),
        ("Cm7", (36, 55, 58, 60, 63)),
    ],
)
def test_initial_voicing_uses_lowest_legal_notes(symbol: str, expected: tuple[int, ...]) -> None:
    assert voice_chord(parse_chord(symbol)) == expected


def test_voice_leading_keeps_common_tones() -> None:
    previous = (36, 60, 64, 67)
    assert voice_chord(parse_chord("F"), previous) == (41, 60, 65, 69)


def test_no_chord_releases_all_harmony_and_resets_voicing() -> None:
    previous = (36, 60, 64, 67)
    silence = voice_chord(parse_chord("N.C."), previous)
    assert silence == ()
    assert voice_chord(parse_chord("C"), silence) == (36, 55, 60, 64)


def legal_upper_voicings(chord: Chord) -> tuple[tuple[int, ...], ...]:
    return tuple(
        upper
        for upper in combinations(range(55, 71), len(chord.pitch_classes))
        if {pitch % 12 for pitch in upper} == set(chord.pitch_classes)
    )


def movement_cost(before: tuple[int, ...], after: tuple[int, ...]) -> int:
    smaller, larger = sorted((before, after), key=len)
    costs: list[int] = []
    for assignment in permutations(larger, len(smaller)):
        matched_cost = sum(
            abs(left - right) for left, right in zip(smaller, assignment, strict=True)
        )
        unmatched_cost = sum(
            min(abs(note - other) for other in smaller) for note in set(larger) - set(assignment)
        )
        costs.append(matched_cost + unmatched_cost)
    return min(costs)


@pytest.mark.parametrize("symbol", [case[0] for case in SYMBOL_CASES])
def test_initial_voicing_minimizes_bottom_then_top(symbol: str) -> None:
    chord = parse_chord(symbol)
    if isinstance(chord, NoChord):
        return
    candidates = legal_upper_voicings(chord)
    assert candidates, f"{symbol} has no voicing in MIDI 55..70"
    actual = voice_chord(chord)[1:]
    assert (actual[0], actual[-1]) == min((upper[0], upper[-1]) for upper in candidates)


@pytest.mark.parametrize(
    ("symbol", "previous", "expected"),
    [
        ("D", (36, 60, 64, 67), (38, 57, 62, 66)),
        ("C7", (36, 55, 60, 64), (36, 55, 58, 60, 64)),
        ("C", (36, 55, 58, 60, 64), (36, 55, 60, 64)),
    ],
)
def test_voice_leading_ties_and_voice_count_changes(
    symbol: str, previous: tuple[int, ...], expected: tuple[int, ...]
) -> None:
    assert voice_chord(parse_chord(symbol), previous) == expected


@settings(derandomize=True)
@given(CHORDS, CHORDS, st.integers(min_value=0, max_value=15))
def test_voice_leading_minimizes_movement_among_all_inversions(
    chord: Chord, previous_chord: Chord, placement: int
) -> None:
    previous_candidates = legal_upper_voicings(previous_chord)
    previous = (
        36 + previous_chord.bass_pitch_class,
        *previous_candidates[placement % len(previous_candidates)],
    )
    actual = voice_chord(chord, previous)
    assert actual[0] == 36 + chord.bass_pitch_class
    assert actual[1:] in legal_upper_voicings(chord)
    actual_rank = (movement_cost(previous[1:], actual[1:]), actual[-1], actual[1])
    assert actual_rank == min(
        (movement_cost(previous[1:], upper), upper[-1], upper[0])
        for upper in legal_upper_voicings(chord)
    )


@settings(derandomize=True)
@given(CHORDS)
def test_repeated_chord_keeps_the_same_voicing(chord: Chord) -> None:
    previous = voice_chord(chord)
    assert voice_chord(chord, previous) == previous
