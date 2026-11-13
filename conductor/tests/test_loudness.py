"""BS.1770 loudness compared with FFmpeg and with the tap's dual-mono view."""

import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from ghostband.loudness import (
    TapLoudness,
    integrated_loudness,
    measure_loudness,
    reference_gain,
    tap_loudness,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = sorted((ROOT / "fixtures").glob("*.wav"))
SAMPLE_RATE = 48_000


def stereo_sine(amplitude: float, *, hz: float = 997.0, seconds: float = 2.0) -> np.ndarray:
    time = np.arange(round(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    wave = amplitude * np.sin(2 * np.pi * hz * time)
    return np.column_stack((wave, wave))


def test_a_full_scale_stereo_tone_reads_zero_lufs() -> None:
    # BS.1770 sets the offset and K-weighting so this tone reads 0 LUFS.
    assert integrated_loudness(stereo_sine(1.0), SAMPLE_RATE) == pytest.approx(0.0, abs=0.05)
    assert integrated_loudness(stereo_sine(0.5), SAMPLE_RATE) == pytest.approx(-6.02, abs=0.05)


def test_the_tap_is_measured_as_dual_mono() -> None:
    stereo = stereo_sine(0.1)
    mono = stereo[:, 0]
    assert tap_loudness(mono) == pytest.approx(integrated_loudness(stereo, SAMPLE_RATE), abs=0.02)
    assert tap_loudness(mono) == pytest.approx(
        integrated_loudness(mono, SAMPLE_RATE) + 10 * math.log10(2), abs=0.02
    )


def test_the_mono_mean_tracks_a_wider_stereo_take() -> None:
    time = np.arange(2 * SAMPLE_RATE, dtype=np.float64) / SAMPLE_RATE
    left = 0.1 * np.sin(2 * np.pi * 997 * time)
    right = 0.9 * left + 0.05 * np.sin(2 * np.pi * 1499 * time)
    stereo = np.column_stack((left, right))
    assert tap_loudness((left + right) / 2) == pytest.approx(
        integrated_loudness(stereo, SAMPLE_RATE), abs=0.5
    )


def test_reference_gain_brings_the_band_and_take_to_the_same_level() -> None:
    band_lufs, reference_lufs = -26.0, -20.0
    gain = reference_gain(band_lufs, reference_lufs)
    assert gain == pytest.approx(10 ** (-6 / 20))
    assert reference_lufs + 20 * math.log10(gain) == pytest.approx(band_lufs, abs=0.5)


def test_reference_gain_is_neutral_without_finite_measurements() -> None:
    assert reference_gain(float("-inf"), -20.0) == 1.0
    assert reference_gain(-20.0, float("nan")) == 1.0


def test_tap_window_keeps_the_last_ten_seconds() -> None:
    tap = TapLoudness()
    block = SAMPLE_RATE // 10
    time = np.arange(block, dtype=np.float64) / SAMPLE_RATE
    loud = (0.2 * np.sin(2 * np.pi * 99 * time)).astype(np.float32)
    quiet = (0.02 * np.sin(2 * np.pi * 1000 * time)).astype(np.float32)
    for index in range(2):
        tap.push(index * block, loud)
    for index in range(2, 102):
        tap.push(index * block, quiet)
    measured = tap.loudness()
    assert measured is not None
    assert measured == pytest.approx(tap_loudness(np.tile(quiet, 10)), abs=0.5)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
@pytest.mark.parametrize("path", FIXTURES, ids=[path.stem for path in FIXTURES])
def test_integrated_loudness_matches_ffmpeg_ebur128(path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    # The executable and every argument come from the fixture list, never from input.
    result = subprocess.run(  # noqa: S603
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    matches = re.findall(r"I:\s+(-?\d+\.\d+) LUFS", result.stderr)
    assert matches
    assert measure_loudness(path) == pytest.approx(float(matches[-1]), abs=0.2)
