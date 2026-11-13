"""Independent chord row from the audio the engine actually played.

The engine taps the mono model output before band gain, reference mixing and
clicks, so naming a triad per beat from it checks the chart instead of echoing
it. Only frames that sit at least one block inside the buffer are used, which
keeps the row one beat plus at most one block behind the playhead.
"""

import base64
from collections import defaultdict
from dataclasses import dataclass
from math import floor, isfinite

import librosa
import numpy as np
from numpy.typing import NDArray

from ghostband.engine_client import SAMPLE_RATE, Command
from ghostband.evaluation import chord_detector

BLOCK_SAMPLES = 4_800
BUFFER_SAMPLES = SAMPLE_RATE
# A shorter window names a triad from less than a beat of played audio.
MIN_BUFFER_SAMPLES = SAMPLE_RATE // 2
FRAME_SECONDS = chord_detector.HOP_SAMPLES / chord_detector.ANALYSIS_RATE
# The newest block of audio still depends on samples that have not arrived.
EDGE_SECONDS = BLOCK_SAMPLES / SAMPLE_RATE


@dataclass(frozen=True)
class HeardEvent:
    """One completed beat of played audio, named by the triad detector."""

    beat: int
    symbol: str
    confidence: float


def decode_tap(message: Command) -> tuple[int, NDArray[np.float32]]:
    """Validate one tap event and decode its little-endian float32 block."""
    sample = message.get("sample")
    if message.get("event") != "tap" or type(sample) is not int or sample < 0:
        raise ValueError("Tap event requires a nonnegative integer sample")
    pcm = message.get("pcm")
    if not isinstance(pcm, str):
        raise ValueError("Tap event requires base64 pcm")
    try:
        data = base64.b64decode(pcm.encode(), validate=True)
    except ValueError as error:
        raise ValueError("Tap pcm is not valid base64") from error
    if len(data) != BLOCK_SAMPLES * 4:
        raise ValueError(f"Tap pcm must hold {BLOCK_SAMPLES} float32 samples")
    audio = np.frombuffer(data, dtype="<f4").astype(np.float32)
    if not bool(np.isfinite(audio).all()):
        raise ValueError("Tap pcm must be finite")
    return sample, audio


class HeardRow:
    """Turn tap blocks into at most one chord per completed beat."""

    def __init__(self, *, beat_seconds: float) -> None:
        if not isfinite(beat_seconds) or beat_seconds <= 0:
            raise ValueError("Beat seconds must be finite and positive")
        self.beat_seconds = beat_seconds
        self._audio = np.zeros(0, np.float32)
        self._end_sample = 0
        self._next_frame_s = 0.0
        self._beat = 0
        self._frames: dict[int, list[NDArray[np.float64]]] = defaultdict(list)

    def push(self, sample: int, pcm: NDArray[np.float32]) -> list[HeardEvent]:
        """Accept the next block and return the beats whose whole span arrived."""
        if sample != self._end_sample:
            self._restart(sample)
        self._audio = np.concatenate((self._audio, pcm))[-BUFFER_SAMPLES:]
        self._end_sample = sample + len(pcm)
        start_s = (self._end_sample - len(self._audio)) / SAMPLE_RATE
        end_s = self._end_sample / SAMPLE_RATE
        self._collect(start_s, end_s)
        return self._complete(end_s)

    def _restart(self, sample: int) -> None:
        """A dropped block or a new take breaks timing; start a fresh window."""
        self._audio = np.zeros(0, np.float32)
        self._frames.clear()
        self._end_sample = sample
        self._next_frame_s = sample / SAMPLE_RATE
        self._beat = floor(sample / SAMPLE_RATE / self.beat_seconds)

    def _collect(self, start_s: float, end_s: float) -> None:
        limit_s = end_s - EDGE_SECONDS
        if len(self._audio) < MIN_BUFFER_SAMPLES or limit_s <= self._next_frame_s:
            return
        resampled: NDArray[np.float32] = np.asarray(
            librosa.resample(
                self._audio,
                orig_sr=SAMPLE_RATE,
                target_sr=chord_detector.ANALYSIS_RATE,
                res_type="soxr_hq",
            ),
            dtype=np.float32,
        )
        times, chroma = chord_detector.frames(resampled)
        for index in np.flatnonzero(times <= limit_s - start_s):
            time_s = start_s + float(times[index])
            # The recentred buffer repeats frames already taken; half a hop of
            # slack absorbs the float difference between two window starts.
            if time_s < self._next_frame_s:
                continue
            self._frames[floor(time_s / self.beat_seconds)].append(chroma[:, index])
            self._next_frame_s = time_s + FRAME_SECONDS / 2

    def _complete(self, end_s: float) -> list[HeardEvent]:
        events: list[HeardEvent] = []
        while (self._beat + 1) * self.beat_seconds <= end_s:
            vectors = self._frames.pop(self._beat, [])
            if vectors:
                guess = chord_detector.classify(np.mean(vectors, axis=0))
                events.append(HeardEvent(self._beat, guess.symbol, guess.confidence))
            self._beat += 1
        return events
