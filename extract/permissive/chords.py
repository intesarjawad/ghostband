"""Beat-synchronous chord templates, temporal decoding and key profiles."""

from itertools import pairwise

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
ROOTS = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
QUALITIES = (("", (0, 4, 7)), ("m", (0, 3, 7)), ("7", (0, 4, 7, 10)), ("m7", (0, 3, 7, 10)))
SELF_TRANSITION = 0.9
ROOT_WEIGHT = 2.0  # Distinguish the harmonic root from upper-voice colour tones.
SEVENTH_MARGIN = 0.05  # Require stronger evidence than every competing triad.
EMISSION_SCALE = 24.0  # Fixture calibration preserves short changes with 0.9 self-transition.
NOTE_ACTIVATION_THRESHOLD = 0.3  # Basic Pitch 0.4's default frame threshold.
SILENCE_RMS = 1e-4  # -80 dBFS rejects numerical noise without labelling quiet notes silent.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def normalize(features: FloatArray) -> FloatArray:
    """L1-normalize each feature row, preserving silence as zero."""
    totals = features.sum(axis=1, keepdims=True)
    return np.divide(features, totals, out=np.zeros_like(features), where=totals > 0)


def beat_average(features: FloatArray, times_s: FloatArray, edges_s: FloatArray) -> FloatArray:
    """Average frames in half-open beat windows; empty windows carry no evidence."""
    if features.ndim != 2 or len(features) != len(times_s):
        raise ValueError("Feature frames and timestamps must align")
    if len(edges_s) < 2 or ((edges_s[1:] - edges_s[:-1]) <= 0).any():
        raise ValueError("Beat edges must be strictly increasing")
    result = np.zeros((len(edges_s) - 1, features.shape[1]), dtype=np.float64)
    for index, (start, end) in enumerate(pairwise(edges_s)):
        frames = features[(times_s >= start) & (times_s < end)]
        if len(frames):
            result[index] = frames.mean(axis=0)
    return result


def fold_salience(notes: FloatArray) -> FloatArray:
    """Fold active Basic Pitch piano-note probabilities, starting at MIDI 21."""
    if notes.ndim != 2 or notes.shape[1] != 88:
        raise ValueError("Basic Pitch note salience must have 88 columns")
    if not np.isfinite(notes).all() or (notes < 0).any():
        raise ValueError("Note salience must be finite and nonnegative")
    # Inactive probabilities otherwise accumulate into a large floor across 88 keys.
    active = np.where(notes >= NOTE_ACTIVATION_THRESHOLD, notes, 0)
    chroma = np.zeros((len(notes), 12), dtype=np.float64)
    for column in range(notes.shape[1]):
        chroma[:, (column + 21) % 12] += active[:, column]
    return chroma


def templates() -> tuple[list[str], FloatArray]:
    """Build 48 unit-length major, minor and seventh templates plus N.C."""
    symbols: list[str] = []
    vectors = np.zeros((49, 12), dtype=np.float64)
    for root, name in enumerate(ROOTS):
        for quality, (suffix, intervals) in enumerate(QUALITIES):
            symbols.append(name + suffix)
            vectors[root * 4 + quality, [(root + step) % 12 for step in intervals]] = 1
            vectors[root * 4 + quality, root] = ROOT_WEIGHT
    norms = np.sqrt((vectors**2).sum(axis=1, keepdims=True))
    vectors = np.divide(vectors, norms, out=vectors, where=norms > 0)
    return [*symbols, "N.C."], vectors


def decode_chords(features: FloatArray, rms: FloatArray) -> list[str]:
    """Viterbi-decode chord evidence with a 0.9 probability of holding a chord."""
    if features.ndim != 2 or features.shape[1] != 12 or len(features) != len(rms):
        raise ValueError("Chord features must be beat rows of 12 pitch classes with matching RMS")
    if not np.isfinite(features).all() or not np.isfinite(rms).all():
        raise ValueError("Chord features and RMS must be finite")
    if (features < 0).any() or (rms < 0).any():
        raise ValueError("Chord features and RMS must be nonnegative")
    if not len(features):
        return []
    symbols, vectors = templates()
    # A uniform activation floor contains no evidence for a particular chord.
    features = features - features.min(axis=1, keepdims=True)
    lengths = np.sqrt((features**2).sum(axis=1, keepdims=True))
    unit = np.divide(features, lengths, out=np.zeros_like(features), where=lengths > 0)
    scores = unit @ vectors.T
    triads = [root * 4 + quality for root in range(12) for quality in (0, 1)]
    best_triad = scores[:, triads].max(axis=1)
    for root in range(12):
        for quality in (2, 3):
            seventh = root * 4 + quality
            weak = scores[:, seventh] < best_triad + SEVENTH_MARGIN
            scores[weak, seventh] = -np.inf
    emissions = scores * EMISSION_SCALE
    silent = (rms < SILENCE_RMS) | (lengths[:, 0] == 0)
    emissions[silent, :] = -np.inf
    emissions[silent, -1] = 0
    emissions[~silent, -1] = -np.inf
    transitions = np.full((len(symbols), len(symbols)), np.log((1 - SELF_TRANSITION) / 48))
    np.fill_diagonal(transitions, np.log(SELF_TRANSITION))
    best = emissions[0].copy()
    back = np.zeros((len(features), len(symbols)), dtype=np.int64)
    for index in range(1, len(features)):
        paths = best[:, None] + transitions
        back[index] = paths.argmax(axis=0)
        best = paths[back[index], np.arange(len(symbols))] + emissions[index]
    state = int(best.argmax())
    states = [state]
    for index in range(len(features) - 1, 0, -1):
        state = int(back[index, state])
        states.append(state)
    return [symbols[state] for state in reversed(states)]


def estimate_key(chroma: FloatArray) -> str:
    """Correlate whole-song chroma with the Krumhansl-Kessler key profiles."""
    if chroma.ndim != 2 or chroma.shape[1] != 12 or not np.isfinite(chroma).all():
        raise ValueError("Key chroma must be finite rows of 12 pitch classes")
    total = chroma.sum(axis=0)
    centred = total - total.mean()
    if np.sqrt((centred**2).sum()) == 0:
        return "unknown"
    scores: list[float] = []
    keys: list[str] = []
    for suffix, profile in (("", MAJOR_PROFILE), ("m", MINOR_PROFILE)):
        vector = np.asarray(profile, dtype=np.float64)
        vector -= vector.mean()
        vector /= np.sqrt((vector**2).sum())
        for root, name in enumerate(ROOTS):
            keys.append(name + suffix)
            scores.append(float(centred @ vector[(np.arange(12) - root) % 12]))
    return keys[int(np.asarray(scores).argmax())]
