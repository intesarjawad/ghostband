"""Single-line melody from polyphonic note events, expressed in musician-counted beats."""

from itertools import pairwise

import numpy as np
from chords import NOTE_ACTIVATION_THRESHOLD, FloatArray

Note = tuple[float, float, int, float]
MELODY_PITCH_MIN = 36  # The range a chart keeps on import, so extracted lines are never clamped.
MELODY_PITCH_MAX = 96
MIN_NOTE_BEATS = 0.25  # Shorter fragments are transcription flicker, not a played sixteenth.


def beat_positions(times_s: FloatArray, beats: FloatArray) -> FloatArray:
    """Map seconds to fractional beats, extending the grid at the median beat period."""
    if len(beats) < 2:
        raise ValueError("Two beats are required to place notes on the beat grid")
    period_s = float(np.median(beats[1:] - beats[:-1]))
    last = len(beats) - 1
    inside = np.interp(times_s, beats, np.arange(len(beats), dtype=np.float64))
    before = (times_s - beats[0]) / period_s
    after = last + (times_s - beats[-1]) / period_s
    return np.where(times_s < beats[0], before, np.where(times_s > beats[-1], after, inside))


def extract_melody(
    notes: list[Note], beats: FloatArray, duration_s: float
) -> list[dict[str, float | int]]:
    """Keep the highest sounding note at every instant, the skyline of the transcription.

    Notes outside the chart's pitch range or below Basic Pitch's activation threshold are
    ignored. A held note that a higher one interrupts is cut there, so the result never
    overlaps and fits inside the chart's duration.
    """
    duration_beats = float(beat_positions(np.array([duration_s]), beats)[0])
    candidates: list[tuple[float, float, int, float]] = []
    for start_s, end_s, pitch, amplitude in notes:
        if (
            amplitude < NOTE_ACTIVATION_THRESHOLD
            or not MELODY_PITCH_MIN <= pitch <= MELODY_PITCH_MAX
        ):
            continue
        start, end = beat_positions(np.array([start_s, end_s]), beats)
        start, end = max(float(start), 0.0), min(float(end), duration_beats)
        if end > start:
            candidates.append((start, end, pitch, amplitude))
    order = sorted(range(len(candidates)), key=lambda index: candidates[index][0])
    boundaries = sorted({edge for start, end, _, _ in candidates for edge in (start, end)})
    pieces: list[tuple[float, float, int, int]] = []  # start, end, pitch, candidate index
    active: list[int] = []
    cursor = 0
    for low, high in pairwise(boundaries):
        while cursor < len(order) and candidates[order[cursor]][0] <= low:
            active.append(order[cursor])
            cursor += 1
        active = [index for index in active if candidates[index][1] > low]
        if not active:
            continue
        top = max(active, key=lambda index: (candidates[index][2], candidates[index][3]))
        if pieces and pieces[-1][3] == top and pieces[-1][1] == low:
            pieces[-1] = (pieces[-1][0], high, pieces[-1][2], top)
        else:
            pieces.append((low, high, candidates[top][2], top))
    return [
        {"start_beat": start, "end_beat": end, "pitch": pitch}
        for start, end, pitch, _ in pieces
        if end - start >= MIN_NOTE_BEATS
    ]
