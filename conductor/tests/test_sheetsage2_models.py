import asyncio
import os
from pathlib import Path

import pytest
from ghostband.extractors.sheetsage2 import EXTRACT_ENV
from ghostband.extractors.sheetsage2_output import Timing, chart_from_output

ROOT = Path(__file__).resolve().parents[2]

# Run the real model in its Python 3.11 environment, with audit hooks isolated
# from pytest and the conductor process.
OFFLINE_ARRAY_CHECK = """
import ctypes
import sys
from pathlib import Path
import torchaudio
from sheetsage_runtime import transcribe_file

def reject_decoder(*args, **kwargs):
    raise RuntimeError('File decoder called during array transcription')

torchaudio.load = reject_decoder
torchaudio.info = reject_decoder
libraries = ('libavcodec', 'libavformat', 'libavutil', 'libavfilter', 'libswresample', 'ffmpeg')
dyld = ctypes.CDLL(None)
dyld._dyld_get_image_name.restype = ctypes.c_char_p
for index in range(dyld._dyld_image_count()):
    name = dyld._dyld_get_image_name(index).decode().lower()
    if any(part in name for part in libraries):
        raise RuntimeError('FFmpeg library already loaded: ' + name)

def audit(event, args):
    external = {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen',
                'os.system', 'os.posix_spawn'}
    if event in external:
        raise RuntimeError('External I/O during offline inference: ' + event)
    if event == 'ctypes.dlopen' and any(part in str(args).lower() for part in libraries):
        raise RuntimeError('FFmpeg library loading during array transcription')

sys.addaudithook(audit)
transcribe_file(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
"""


@pytest.mark.models
@pytest.mark.parametrize("wav", sorted((ROOT / "fixtures").glob("*.wav")))
def test_sheetsage2_real_offline_array_without_ffmpeg(wav: Path, tmp_path: Path) -> None:
    cache = Path(os.environ.get("GHOSTBAND_SHEETSAGE2_CACHE", str(ROOT / "models/sheetsage2")))

    async def invoke() -> tuple[int | None, bytes]:
        process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "--offline",
            "--frozen",
            "--project",
            str(EXTRACT_ENV),
            "python",
            "-c",
            OFFLINE_ARRAY_CHECK,
            str(wav),
            str(tmp_path),
            str(cache.resolve()),
            cwd=EXTRACT_ENV,
            env={
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_MODULES_CACHE": str(cache.resolve() / "modules"),
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        return process.returncode, stderr

    code, stderr = asyncio.run(invoke())
    assert code == 0, stderr.decode()
    chart = chart_from_output(tmp_path, title=wav.stem, source=wav.name)
    assert chart.beat_times_s
    assert chart.chords
    assert chart.melody
    timing = Timing.model_validate_json((tmp_path / "timing.json").read_text())
    assert timing.total_s == pytest.approx(timing.load_s + timing.prepare_s + timing.inference_s)
