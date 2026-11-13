"""Bar-level song structure from the self-similarity of beat-synchronous chroma."""

from itertools import pairwise

import numpy as np
from chords import FloatArray

MIN_SECTION_BARS = 4  # Also the novelty kernel's half-width, so a boundary has a full bar window.
# A clean split between unrelated sections scores 0.5, so half of that is the weakest boundary kept.
NOVELTY_THRESHOLD = 0.25
LABEL_SIMILARITY = 0.8  # Cosine of centred bar chroma above which two sections share a letter.
LETTERS = 26


def bar_edges(beats: FloatArray, downbeats: FloatArray, beat_count: int) -> list[int]:
    """Return the beat index where each bar starts, folding a pickup into the first bar."""
    starts = sorted({int(np.abs(beats - time).argmin()) for time in downbeats})
    edges = [0, *starts[1:]]
    if len(edges) > 2:
        median = float(np.median(np.diff([*edges, beat_count])))
        if beat_count - edges[-1] < median / 2:
            edges.pop()
    return edges


def _novelty(similarity: FloatArray) -> FloatArray:
    """Correlate a checkerboard kernel along the diagonal; index i is the bar-i boundary."""
    bars = len(similarity)
    half = MIN_SECTION_BARS
    sign = np.where(np.arange(2 * half) < half, -1.0, 1.0)
    kernel = np.outer(sign, sign)
    novelty = np.zeros(bars, dtype=np.float64)
    for index in range(half, bars - half + 1):
        window = similarity[index - half : index + half, index - half : index + half]
        novelty[index] = float((kernel * window).sum()) / kernel.size
    return novelty


def _boundaries(novelty: FloatArray) -> list[int]:
    """Pick local novelty maxima, strongest first, at least a minimum section apart."""
    padded = np.pad(novelty, 1, constant_values=-np.inf)
    peaks = [
        index
        for index in range(len(novelty))
        if novelty[index] >= NOVELTY_THRESHOLD
        and novelty[index] >= padded[index]
        and novelty[index] > padded[index + 2]
    ]
    chosen: list[int] = []
    for index in sorted(peaks, key=lambda peak: -novelty[peak]):
        if all(abs(index - other) >= MIN_SECTION_BARS for other in chosen):
            chosen.append(index)
    return sorted(chosen)


def _letter(index: int) -> str:
    return chr(ord("A") + index) if index < LETTERS else f"Section {index + 1}"


def find_sections(
    features: FloatArray, beats: FloatArray, downbeats: FloatArray, duration_beats: float
) -> list[dict[str, object]]:
    """Split the song at bars where the harmony changes character and letter repeats.

    Sections start and end on bar lines, run contiguously from beat 0 to the duration, and
    reuse a letter when their average chroma resembles an earlier section.
    """
    edges = bar_edges(beats, downbeats, len(features))
    spans = list(zip(edges, [*edges[1:], len(features)], strict=True))
    bars = np.array([features[start:end].mean(axis=0) for start, end in spans])
    bars = bars - bars.mean(axis=0)
    lengths = np.linalg.norm(bars, axis=1, keepdims=True)
    unit = np.divide(bars, lengths, out=np.zeros_like(bars), where=lengths > 0)
    cuts = _boundaries(_novelty(unit @ unit.T)) if len(spans) >= 2 * MIN_SECTION_BARS else []
    cuts = [0, *cuts, len(spans)]
    representatives: list[FloatArray] = []
    sections: list[dict[str, object]] = []
    for first, last in pairwise(cuts):
        mean = unit[first:last].mean(axis=0)
        norm = float(np.linalg.norm(mean))
        mean = mean / norm if norm > 0 else mean
        label = next(
            (
                i
                for i, other in enumerate(representatives)
                if float(mean @ other) >= LABEL_SIMILARITY
            ),
            len(representatives),
        )
        if label == len(representatives):
            representatives.append(mean)
        end_beat = duration_beats if last == len(spans) else float(spans[last][0])
        sections.append(
            {"name": _letter(label), "start_beat": float(spans[first][0]), "end_beat": end_beat}
        )
    return sections
