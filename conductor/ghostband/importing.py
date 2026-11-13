"""Validate local inputs before decoding and running offline extractors."""

import asyncio
import os
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import numpy as np
from pydantic import ValidationError

from ghostband.chart import Chart
from ghostband.errors import ErrorCode, GhostbandError
from ghostband.extractors.midi import extract_midi
from ghostband.extractors.permissive import run_permissive
from ghostband.extractors.sheetsage2 import run_sheetsage2

MAX_FILE_BYTES = 200 * 1024 * 1024
MIN_DURATION_S = 10
SHEETSAGE_LIMIT_S = 360
ROOT = Path(__file__).resolve().parents[2]
sf = import_module("soundfile")
Progress = Callable[[str, int], None]
Notice = Callable[[str], None]


def validate_file(path: Path) -> None:
    """Check size and signature before passing bytes to a decoder."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise GhostbandError(ErrorCode.TOO_LARGE, path.name)
    with path.open("rb") as source:
        header = source.read(32)
    extension = path.suffix.lower()
    signatures = {
        ".wav": header[:4] in {b"RIFF", b"RF64"} and header[8:12] == b"WAVE",
        ".mp3": header[:3] == b"ID3"
        or (len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224),
        ".flac": header[:4] == b"fLaC",
        ".aif": header[:4] == b"FORM" and header[8:12] in {b"AIFF", b"AIFC"},
        ".aiff": header[:4] == b"FORM" and header[8:12] in {b"AIFF", b"AIFC"},
        ".m4a": header[4:8] == b"ftyp",
        ".mid": header[:4] == b"MThd",
        ".midi": header[:4] == b"MThd",
        ".json": header.lstrip().startswith(b"{"),
    }
    if not signatures.get(extension, False):
        raise GhostbandError(ErrorCode.FORMAT_MISMATCH, path.name)


def read_chart(path: Path) -> Chart:
    try:
        return Chart.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise GhostbandError(ErrorCode.IO_FAILED, str(error)) from error
    except ValidationError as error:
        issues = error.errors(include_url=False, include_input=False)
        fields = [
            (
                f"{'.'.join(str(part) for part in issue['loc']) or 'chart'}: "
                f"{issue['msg'].removeprefix('Value error, ')[:120]}"
            )
            for issue in issues[:3]
        ]
        if len(issues) > 3:
            fields.append(f"{len(issues) - 3} more fields")
        raise GhostbandError(ErrorCode.INVALID_CHART, "; ".join(fields)) from error


async def _decode(source: Path, destination: Path, rate: int, *, channels: int = 2) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/afconvert",
            "-f",
            "WAVE",
            "-d",
            f"LEF32@{rate}",
            "-c",
            str(channels),
            str(source),
            str(destination),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
    except OSError as error:
        raise GhostbandError(ErrorCode.DECODE_FAILED, str(error)) from error
    if process.returncode:
        raise GhostbandError(ErrorCode.DECODE_FAILED, stderr.decode(errors="replace").strip())


def _audio_duration(path: Path, display: str) -> float:
    try:
        info = sf.info(str(path))
        duration = info.frames / info.samplerate
        if duration < MIN_DURATION_S:
            raise GhostbandError(ErrorCode.TOO_SHORT, display)
        audible = False
        for block in sf.blocks(str(path), blocksize=65_536, dtype="float32"):
            if not np.isfinite(block).all():
                raise GhostbandError(ErrorCode.DECODE_FAILED, "non-finite audio samples")
            audible = audible or bool(np.any(block != 0))
        if not audible:
            raise GhostbandError(ErrorCode.SILENT, display)
        return duration
    except (OSError, RuntimeError) as error:
        raise GhostbandError(ErrorCode.DECODE_FAILED, str(error)) from error


async def import_tune(
    path: Path,
    *,
    extractor: str = "sheetsage2",
    workspace: Path | None = None,
    progress: Progress | None = None,
    notice: Notice | None = None,
) -> Chart:
    """Return a chart with unknown rights; retain decoded audio in its private workspace."""
    try:
        validate_file(path)
        if extractor not in {"sheetsage2", "permissive"}:
            raise GhostbandError(ErrorCode.INVALID_INPUT, f"extractor {extractor}")
        if progress:
            progress("validate", 0)
        if path.suffix.lower() == ".json":
            chart = Chart.from_import(read_chart(path).model_dump())
        elif path.suffix.lower() in {".mid", ".midi"}:
            chart = Chart.from_import((await asyncio.to_thread(extract_midi, path)).model_dump())
        else:
            chart = await _import_audio(path, extractor, workspace, progress, notice)
        if progress:
            progress("complete", 100)
        return chart
    except OSError as error:
        raise GhostbandError(ErrorCode.IO_FAILED, str(error)) from error
    except (ValueError, EOFError) as error:
        raise GhostbandError(ErrorCode.INVALID_INPUT, str(error)) from error


async def _import_audio(
    path: Path,
    extractor: str,
    workspace: Path | None,
    progress: Progress | None,
    notice: Notice | None,
) -> Chart:
    directory = workspace or (
        Path.home() / "Library/Application Support/ghostband/imports" / uuid4().hex
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    wav = directory / "audio.wav"
    await _decode(path, wav, 44100)
    duration = await asyncio.to_thread(_audio_duration, wav, path.name)
    await _decode(path, directory / "reference_48k.wav", 48000)
    cache = Path(os.environ.get("GHOSTBAND_SHEETSAGE2_CACHE", str(ROOT / "models/sheetsage2")))
    if extractor == "sheetsage2" and duration > SHEETSAGE_LIMIT_S:
        extractor = "permissive"
        if notice:
            notice("long")
    elif extractor == "sheetsage2" and not cache.is_dir():
        extractor = "permissive"
        if notice:
            notice("fallback")
    if progress:
        progress("extract", 25)
    try:
        if extractor == "sheetsage2":
            chart = await run_sheetsage2(wav, output_dir=directory / "extracted", cache_dir=cache)
        else:
            chart = Chart.model_validate(await run_permissive("chart", wav))
    except (OSError, RuntimeError, ValueError) as error:
        raise GhostbandError(ErrorCode.EXTRACT_FAILED, str(error)) from error
    chart = Chart.from_import(
        chart.model_copy(update={"source": path.name, "title": path.stem}).model_dump()
    )
    (directory / "chart.json").write_text(chart.model_dump_json(indent=2) + "\n")
    return chart


async def import_prompt(source: Path, destination: Path) -> None:
    """Decode a local style clip to the engine's mono 16 kHz input format."""
    try:
        validate_file(source)
        await _decode(source, destination, 16000, channels=1)
        info = sf.info(str(destination))
        if not 5 <= info.frames / info.samplerate <= 30:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "audio prompts must be 5 to 30 s long")
    except (OSError, RuntimeError) as error:
        raise GhostbandError(ErrorCode.DECODE_FAILED, str(error)) from error
