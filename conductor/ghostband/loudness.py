"""ITU-R BS.1770 integrated loudness for matching a reference to the band.

The filter design follows the coefficients derived from the analogue prototypes
in BS.1770-4, so 44.1 kHz and 48 kHz material measures the same as libebur128.
"""

import math
from importlib import import_module
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray

from ghostband.engine_client import SAMPLE_RATE

signal = import_module("scipy.signal")

LOUDNESS_OFFSET = -0.691
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = 10.0
BLOCK_SECONDS = 0.4
STEP_SECONDS = 0.1
TAP_WINDOW_SECONDS = 10.0

_PRE_FILTER_HZ = 1681.974450955533
_PRE_FILTER_GAIN_DB = 3.999843853973347
_PRE_FILTER_Q = 0.7071752369554196
_HIGH_PASS_HZ = 38.13547087602444
_HIGH_PASS_Q = 0.5003270373238773
_SHELF_ROLLOFF = 0.4996667741545416


def _coefficients(sample_rate: int) -> tuple[NDArray[np.float64], ...]:
    """K-weighting biquads for one sample rate, as in libebur128."""
    k = math.tan(math.pi * _PRE_FILTER_HZ / sample_rate)
    vh = 10.0 ** (_PRE_FILTER_GAIN_DB / 20.0)
    vb = vh**_SHELF_ROLLOFF
    a0 = 1.0 + k / _PRE_FILTER_Q + k * k
    shelf_b = np.array(
        [
            (vh + vb * k / _PRE_FILTER_Q + k * k) / a0,
            2.0 * (k * k - vh) / a0,
            (vh - vb * k / _PRE_FILTER_Q + k * k) / a0,
        ],
        dtype=np.float64,
    )
    shelf_a = np.array(
        [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / _PRE_FILTER_Q + k * k) / a0],
        dtype=np.float64,
    )
    k = math.tan(math.pi * _HIGH_PASS_HZ / sample_rate)
    denominator = 1.0 + k / _HIGH_PASS_Q + k * k
    high_pass_b = np.array([1.0, -2.0, 1.0], dtype=np.float64)
    high_pass_a = np.array(
        [
            1.0,
            2.0 * (k * k - 1.0) / denominator,
            (1.0 - k / _HIGH_PASS_Q + k * k) / denominator,
        ],
        dtype=np.float64,
    )
    return shelf_b, shelf_a, high_pass_b, high_pass_a


def _block_loudness(weighted: NDArray[np.float64], block: int, step: int) -> NDArray[np.float64]:
    """Loudness of each overlapping block after summing channel power."""
    values: list[float] = []
    for start in range(0, weighted.shape[0] - block + 1, step):
        segment = weighted[start : start + block]
        power = float(np.sum(segment * segment)) / block
        values.append(LOUDNESS_OFFSET + 10.0 * math.log10(power) if power > 0 else -math.inf)
    return np.asarray(values, dtype=np.float64)


def integrated_loudness(audio: ArrayLike, sample_rate: int) -> float:
    """Integrated loudness in LUFS of mono or frames-first multichannel audio."""
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive")
    samples = np.asarray(audio, dtype=np.float64)
    if samples.ndim == 1:
        samples = samples[:, np.newaxis]
    if samples.ndim != 2:
        raise ValueError("Audio must be one-dimensional or frames-first")
    if samples.shape[0] == 0:
        return float("-inf")
    if not bool(np.isfinite(samples).all()):
        raise ValueError("Audio must contain finite samples")

    block = round(BLOCK_SECONDS * sample_rate)
    step = round(STEP_SECONDS * sample_rate)
    if samples.shape[0] < block:
        return float("-inf")

    shelf_b, shelf_a, high_pass_b, high_pass_a = _coefficients(sample_rate)
    weighted = np.asarray(signal.lfilter(shelf_b, shelf_a, samples, axis=0), dtype=np.float64)
    weighted = np.asarray(
        signal.lfilter(high_pass_b, high_pass_a, weighted, axis=0), dtype=np.float64
    )
    loudness = _block_loudness(weighted, block, step)
    audible = loudness > ABSOLUTE_GATE_LUFS
    if not bool(audible.any()):
        return float("-inf")
    relative_gate = float(np.mean(10.0 ** ((loudness[audible] - LOUDNESS_OFFSET) / 10.0)))
    relative_gate = LOUDNESS_OFFSET + 10.0 * math.log10(relative_gate) - RELATIVE_GATE_LU
    kept = loudness > relative_gate
    if not bool(kept.any()):
        return float("-inf")
    mean_power = float(np.mean(10.0 ** ((loudness[kept] - LOUDNESS_OFFSET) / 10.0)))
    return LOUDNESS_OFFSET + 10.0 * math.log10(mean_power)


def measure_loudness(path: Path) -> float:
    """Measure a WAV at its own sample rate, as the engine will play it."""
    soundfile = import_module("soundfile")
    audio, sample_rate = soundfile.read(str(path), dtype="float64", always_2d=True)
    return integrated_loudness(audio, int(sample_rate))


def tap_loudness(audio: ArrayLike, sample_rate: int = SAMPLE_RATE) -> float:
    """Measure the mono tap as dual mono so it is comparable with the take."""
    samples = np.asarray(audio, dtype=np.float64)
    if samples.ndim != 1:
        raise ValueError("Tap audio must be mono")
    return integrated_loudness(np.column_stack((samples, samples)), sample_rate)


def reference_gain(band_lufs: float, reference_lufs: float) -> float:
    """Linear gain that makes the reference as loud as the band."""
    if not math.isfinite(band_lufs) or not math.isfinite(reference_lufs):
        return 1.0
    return 10.0 ** ((band_lufs - reference_lufs) / 20.0)


class TapLoudness:
    """Retain the newest tap audio and estimate the band's current loudness."""

    def __init__(self, seconds: float = TAP_WINDOW_SECONDS, sample_rate: int = SAMPLE_RATE) -> None:
        if not math.isfinite(seconds) or seconds <= 0 or sample_rate <= 0:
            raise ValueError("Tap window and sample rate must be positive")
        self._window = round(seconds * sample_rate)
        self._block = round(BLOCK_SECONDS * sample_rate)
        self._sample_rate = sample_rate
        self._audio = np.zeros(0, dtype=np.float32)
        self._end_sample: int | None = None

    def push(self, sample: int, pcm: NDArray[np.float32]) -> None:
        """Append one block; a gap starts a fresh window instead of joining takes."""
        block = np.asarray(pcm, dtype=np.float32)
        if self._end_sample is not None and sample != self._end_sample:
            self._audio = np.zeros(0, dtype=np.float32)
        self._audio = np.concatenate((self._audio, block))[-self._window :]
        self._end_sample = sample + block.size

    def reset(self) -> None:
        self._audio = np.zeros(0, dtype=np.float32)
        self._end_sample = None

    def loudness(self) -> float | None:
        """Recent band loudness, or None before a full measurement block arrived."""
        if self._audio.size < self._block:
            return None
        return tap_loudness(self._audio, self._sample_rate)
