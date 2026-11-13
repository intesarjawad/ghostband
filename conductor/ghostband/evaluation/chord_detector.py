"""Frame-level triad detection shared by sync measurement and the heard row.

The detector never sees a chart: it names the major or minor triad that best
explains each frame of recorded audio, so comparing it with the chart is an
independent check. Only the 24 triad templates and N.C. exist here, because
majmin agreement is the unit both callers report.
"""

from dataclasses import dataclass
from math import ceil

import librosa
import numpy as np
from numpy.typing import NDArray

from ghostband.chords import NoChord, parse_chord

ANALYSIS_RATE = 22_050
HOP_SAMPLES = 512
# C2 upward still spans every triad tone in one second of audio without the
# transform padding its lowest octave.
FMIN_HZ = 65.41
# Mean chroma below this is treated as no chord rather than a quiet triad.
SILENCE_FLOOR = 0.02
NO_CHORD = "N.C."
MIN_FRAMES = 2 * ANALYSIS_RATE
FRAME_SECONDS = HOP_SAMPLES / ANALYSIS_RATE
# An early change is still an error to count, even outside the 120 ms window,
# so the search opens a quarter second ahead; late responses get longer.
CHANGE_BEFORE_S = 0.25
CHANGE_AFTER_S = 0.5
# The new triad must win this long, so one noisy frame is not a change.
CHANGE_HOLD_S = 0.1
# A weak correlation is a melody note or a release tail, not a new harmony.
CHANGE_MIN_CORRELATION = 0.1
_PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_THIRDS = {"": 4, "m": 3}


def _templates() -> tuple[tuple[str, NDArray[np.float64]], ...]:
    rows: list[tuple[str, NDArray[np.float64]]] = []
    for root, name in enumerate(_PITCH_NAMES):
        for suffix, third in _THIRDS.items():
            weights = np.zeros(12)
            weights[[root, (root + third) % 12, (root + 7) % 12]] = 1.0
            rows.append((name + suffix, weights / np.linalg.norm(weights)))
    return tuple(rows)


_TEMPLATES = _templates()
_SYMBOLS = tuple(symbol for symbol, _ in _TEMPLATES)
_MATRIX = np.stack([weights for _, weights in _TEMPLATES])
_CENTRED = {symbol: weights - weights.mean() for symbol, weights in _TEMPLATES}


@dataclass(frozen=True)
class ChordGuess:
    """One majmin symbol and the template correlation that produced it."""

    symbol: str
    confidence: float


def frames(audio: NDArray[np.float32]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return centred chroma frame times in seconds and their 12-bin vectors.

    Audio shorter than two seconds is zero-padded in front so every octave of
    the transform is computed from real filter length instead of pads.
    """
    pad = max(0, MIN_FRAMES - len(audio))
    # A fixed tuning keeps frames stable while the listening window slides.
    chroma: NDArray[np.float64] = np.asarray(
        librosa.feature.chroma_cqt(
            y=np.pad(audio, (pad, 0)),
            sr=ANALYSIS_RATE,
            hop_length=HOP_SAMPLES,
            fmin=FMIN_HZ,
            tuning=0.0,
        ),
        dtype=np.float64,
    )
    times = np.arange(chroma.shape[1], dtype=np.float64) * HOP_SAMPLES / ANALYSIS_RATE - (
        pad / ANALYSIS_RATE
    )
    return times, chroma


def classify(chroma: NDArray[np.float64]) -> ChordGuess:
    """Pick the best triad for one averaged chroma vector."""
    energy = float(np.mean(chroma))
    norm = float(np.linalg.norm(chroma))
    if energy < SILENCE_FLOOR or norm == 0.0:
        return ChordGuess(NO_CHORD, 0.0)
    scores = _MATRIX @ (chroma / norm)
    best = int(np.argmax(scores))
    return ChordGuess(_SYMBOLS[best], float(np.clip(scores[best], 0.0, 1.0)))


def beat_window(
    times: NDArray[np.float64],
    chroma: NDArray[np.float64],
    start_s: float,
    end_s: float,
) -> ChordGuess | None:
    """Average the frames inside a half-open beat and classify the result."""
    selected = (times >= start_s) & (times < end_s)
    if not bool(selected.any()):
        return None
    return classify(np.mean(chroma[:, selected], axis=1))


def majmin(symbol: str) -> str:
    """Reduce a chart symbol to the triad the detector can name."""
    chord = parse_chord(symbol)
    if isinstance(chord, NoChord):
        return NO_CHORD
    intervals = {(pitch - chord.root_pitch_class) % 12 for pitch in chord.pitch_classes}
    minor = 3 in intervals and 4 not in intervals
    return _PITCH_NAMES[chord.root_pitch_class] + ("m" if minor else "")


def template_correlation(chroma: NDArray[np.float64], symbol: str) -> NDArray[np.float64]:
    """Correlate each mean-subtracted chroma frame with one triad's template.

    A frame with no spread across pitch classes correlates with nothing, so it
    reads 0 against every triad instead of favouring either side of a change.
    """
    if symbol not in _CENTRED:
        raise ValueError(f"No triad template for {symbol!r}; use a major or minor triad name")
    template = _CENTRED[symbol]
    centred = chroma - np.mean(chroma, axis=0)
    norms = np.linalg.norm(centred, axis=0) * np.linalg.norm(template)
    dots = template @ centred
    return np.divide(dots, norms, out=np.zeros_like(dots), where=norms > 1e-12)


def change_time(
    times: NDArray[np.float64],
    chroma: NDArray[np.float64],
    before: str,
    after: str,
    at_s: float,
) -> float | None:
    """Find when frames switch from one triad to another near a chart change.

    The result is the first frame in [at_s - 0.25, at_s + 0.5] that starts a
    run of at least 100 ms in which the new triad correlates better than the
    old one and above the weak-harmony floor, or None when no frame does.
    Triad names come from `majmin`.
    """
    new = template_correlation(chroma, after)
    gain = (new > template_correlation(chroma, before)) & (new > CHANGE_MIN_CORRELATION)
    run = ceil(CHANGE_HOLD_S / FRAME_SECONDS - 1e-9)
    # held[i] is true when frames i .. i + run - 1 all favour the new triad.
    total = np.concatenate(([0], np.cumsum(gain)))
    held = total[run:] - total[:-run] == run
    # Only the first frame of a held run is a change; a run already under way at
    # the search edge belongs to an earlier transition, as the null checks.
    starts = held & ~np.concatenate(([False], held[:-1]))
    candidates = np.flatnonzero(
        starts
        & (times[: starts.size] >= at_s - CHANGE_BEFORE_S)
        & (times[: starts.size] <= at_s + CHANGE_AFTER_S)
    )
    return float(times[candidates[0]]) if candidates.size else None
