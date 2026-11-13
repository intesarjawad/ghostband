"""Generate deterministic audio import edge cases without model downloads."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import struct
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from ghostband.chart import Chart

if TYPE_CHECKING:
    import numpy.typing as npt

sf = import_module("soundfile")
ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE_HZ = 44_100
DURATION_S = 30
LONG_DURATION_S = 9 * 60
HUGE_DATA_BYTES = 201 * 1024 * 1024
RANDOM_SEED = 20261104


def _write_audio(path: Path, audio: npt.NDArray[np.float64]) -> None:
    sf.write(str(path), audio, SAMPLE_RATE_HZ, subtype="PCM_16", format="WAV")


def _seven_eight() -> npt.NDArray[np.float64]:
    # Seven eighth-note pulses per bar, grouped 2+2+3 at quarter-note = 120.
    pulse_s = 0.25
    pulse_frames = round(pulse_s * SAMPLE_RATE_HZ)
    time_s = np.arange(pulse_frames, dtype=np.float64) / SAMPLE_RATE_HZ
    pitches = (60, 62, 64, 65, 67, 69, 71)
    audio = np.zeros(DURATION_S * SAMPLE_RATE_HZ, dtype=np.float64)
    for pulse, start in enumerate(range(0, len(audio), pulse_frames)):
        pitch = pitches[pulse % len(pitches)]
        frequency_hz = 440 * 2 ** ((pitch - 69) / 12)
        accent = 0.35 if pulse % 7 in {0, 2, 4} else 0.18
        tone = accent * np.sin(2 * np.pi * frequency_hz * time_s) * np.exp(-time_s * 16)
        count = min(len(tone), len(audio) - start)
        audio[start : start + count] = tone[:count]
    return audio


def _drums() -> npt.NDArray[np.float64]:
    audio = np.zeros(DURATION_S * SAMPLE_RATE_HZ, dtype=np.float64)
    hit_frames = SAMPLE_RATE_HZ // 5
    time_s = np.arange(hit_frames, dtype=np.float64) / SAMPLE_RATE_HZ
    rng = np.random.default_rng(RANDOM_SEED)
    for pulse, start in enumerate(range(0, len(audio), SAMPLE_RATE_HZ // 4)):
        noise = rng.uniform(-1, 1, hit_frames)
        # A high-passed closed hat and alternating kick/snare on quarter notes.
        hit = 0.06 * np.diff(noise, prepend=noise[0]) * np.exp(-time_s * 70)
        if pulse % 4 == 0:
            hit += 0.5 * np.sin(2 * np.pi * 55 * time_s) * np.exp(-time_s * 25)
        elif pulse % 4 == 2:
            hit += 0.25 * noise * np.exp(-time_s * 30)
        count = min(hit_frames, len(audio) - start)
        audio[start : start + count] += hit[:count]
    return audio


def _rubato(audio: npt.NDArray[np.float64], tempo_bpm: float) -> npt.NDArray[np.float64]:
    # Integrate a smooth speed curve: instantaneous tempo stays within +/-10 percent.
    time_s = np.arange(len(audio), dtype=np.float64) / SAMPLE_RATE_HZ
    beat_s = 60 / tempo_bpm
    period_s = 8 * beat_s
    phase = 2 * np.pi * time_s / period_s
    source_s = time_s + 0.1 * period_s / (2 * np.pi) * (1 - np.cos(phase))
    return np.interp(source_s, time_s, audio, right=0)


def _sparse_wav(path: Path) -> None:
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + HUGE_DATA_BYTES,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        SAMPLE_RATE_HZ,
        SAMPLE_RATE_HZ * 2,
        2,
        16,
        b"data",
        HUGE_DATA_BYTES,
    )
    path.unlink(missing_ok=True)
    with path.open("wb") as output:
        output.write(header)
        output.seek(len(header) + HUGE_DATA_BYTES - 1)
        output.write(b"\0")


async def generate(output: Path) -> None:
    """Write the ten audio boundary inputs from the committed Greensleeves render."""
    source = ROOT / "fixtures/greensleeves.wav"
    golden = ROOT / "fixtures/greensleeves.golden.json"
    if not source.is_file() or not golden.is_file():
        raise FileNotFoundError("Generate the source audio and golden charts with make fixtures")
    chart = Chart.model_validate_json(golden.read_text(encoding="utf-8"))
    audio: npt.NDArray[np.float64]
    rate: int
    audio, rate = sf.read(str(source), dtype="float64")
    if rate != SAMPLE_RATE_HZ or audio.ndim != 1 or len(audio) != DURATION_S * SAMPLE_RATE_HZ:
        raise ValueError("Regenerate fixtures with make fixtures before generating edge cases")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FileNotFoundError("Install ffmpeg to generate MP3 inputs")
    output.mkdir(parents=True, exist_ok=True)
    _write_audio(output / "silent.wav", np.zeros(60 * SAMPLE_RATE_HZ, dtype=np.float64))
    _write_audio(output / "short.wav", audio[: 5 * SAMPLE_RATE_HZ])
    _write_audio(output / "long.wav", np.tile(audio, LONG_DURATION_S // DURATION_S))
    _write_audio(output / "seven-eight.wav", _seven_eight())
    _write_audio(output / "rubato.wav", _rubato(audio, chart.tempo_bpm))
    _write_audio(output / "drums-only.wav", _drums())
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map_metadata",
        "-1",
        "-ar",
        "8000",
        "-ac",
        "1",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "32k",
        "-flags:a",
        "+bitexact",
        "-write_xing",
        "0",
        "-id3v2_version",
        "0",
        str(output / "phone.mp3"),
    ]
    process = await asyncio.create_subprocess_exec(*command)
    if await process.wait() != 0:
        raise RuntimeError("MP3 encoding failed; check the ffmpeg output")
    mp3 = (output / "phone.mp3").read_bytes()
    (output / "truncated.mp3").write_bytes(mp3[4:32])
    (output / "mp3-named.wav").write_bytes(mp3)
    _sparse_wav(output / "huge.wav")
    print(f"Generated 10 inputs in {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "fixtures/worst")
    args = parser.parse_args()
    asyncio.run(generate(args.output))


if __name__ == "__main__":
    main()
