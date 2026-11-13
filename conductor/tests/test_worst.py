import asyncio
import hashlib
import importlib.util
import shutil
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sf = import_module("soundfile")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def test_all_edge_cases_are_repeatable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "make_worst_case", ROOT / "scripts/make_worst_case.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source_dir = tmp_path / "fixtures"
    source_dir.mkdir()
    time = np.arange(30 * 44100, dtype=np.float64) / 44100
    sf.write(
        str(source_dir / "greensleeves.wav"),
        0.2 * np.sin(2 * np.pi * 440 * time),
        44100,
        subtype="PCM_16",
    )
    shutil.copyfile(
        ROOT / "charts/greensleeves.chart.json", source_dir / "greensleeves.golden.json"
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    output = tmp_path / "worst"
    asyncio.run(module.generate(output))
    names = {
        "silent.wav",
        "short.wav",
        "long.wav",
        "phone.mp3",
        "seven-eight.wav",
        "rubato.wav",
        "drums-only.wav",
        "truncated.mp3",
        "mp3-named.wav",
        "huge.wav",
    }
    assert {path.name for path in output.iterdir()} == names
    expected = {path.name: digest(path) for path in output.iterdir()}
    asyncio.run(module.generate(output))
    assert {path.name: digest(path) for path in output.iterdir()} == expected
    for name, seconds in [
        ("silent.wav", 60),
        ("short.wav", 5),
        ("long.wav", 540),
        ("seven-eight.wav", 30),
        ("rubato.wav", 30),
        ("drums-only.wav", 30),
    ]:
        info = sf.info(str(output / name))
        assert info.frames == seconds * 44100
        assert info.samplerate == 44100
        assert info.channels == 1
        assert info.subtype == "PCM_16"
    silent, _ = sf.read(str(output / "silent.wav"))
    assert np.count_nonzero(silent) == 0
    huge = output / "huge.wav"
    assert huge.stat().st_size > 200 * 1024 * 1024
    assert huge.stat().st_blocks * 512 < huge.stat().st_size / 10
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg is not None
    assert ffprobe is not None
    decoded_code, _ = asyncio.run(
        run_command(
            [ffmpeg, "-v", "error", "-i", str(output / "truncated.mp3"), "-f", "null", "-"],
        )
    )
    assert decoded_code != 0
    info_code, info_output = asyncio.run(
        run_command(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=sample_rate,channels,bit_rate",
                "-of",
                "csv=p=0",
                str(output / "phone.mp3"),
            ],
        )
    )
    assert info_code == 0
    assert info_output.decode().strip() == "8000,1,32000"
    assert (output / "mp3-named.wav").read_bytes() == (output / "phone.mp3").read_bytes()


async def run_command(command: list[str]) -> tuple[int, bytes]:
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    output, _ = await process.communicate()
    assert process.returncode is not None
    return process.returncode, output
