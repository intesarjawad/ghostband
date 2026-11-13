"""Float32 array transcription with all model resources restricted to the cache."""

import json
import math
import os
from importlib import import_module
from pathlib import Path
from time import perf_counter
from typing import Literal, Protocol

import numpy as np
from numpy.typing import NDArray
from sheetsage_cache import verify_cache
from sheetsage_pins import MODELS

MAX_DURATION_S = 360
MIN_SAMPLES = 1025  # Minimum accepted by the pinned encoder at its input rate.
Device = Literal["cpu", "mps"]


class _Processor(Protocol):
    sampling_rate: int


class _Model(Protocol):
    def to(self, device: str) -> "_Model": ...
    def eval(self) -> "_Model": ...
    def transcribe(
        self,
        waveform: NDArray[np.float32],
        *,
        sampling_rate: int,
        output_dir: str,
        dtype: str,
    ) -> object: ...


def read_waveform(wav_path: Path, sampling_rate: int) -> tuple[NDArray[np.float32], float]:
    soundfile = import_module("soundfile")
    samples: NDArray[np.float32]
    source_rate: int
    samples, source_rate = soundfile.read(str(wav_path), dtype="float32", always_2d=True)
    duration_s = len(samples) / source_rate
    if not 0 < duration_s <= MAX_DURATION_S:
        raise ValueError("SheetSage2 accepts up to 360 seconds; use the permissive extractor")
    if not np.isfinite(samples).all():
        raise ValueError("WAV samples must be finite")
    mono = samples.mean(axis=1, dtype=np.float32)
    if source_rate != sampling_rate:
        divisor = math.gcd(source_rate, sampling_rate)
        signal = import_module("scipy.signal")
        mono = signal.resample_poly(mono, sampling_rate // divisor, source_rate // divisor)
    waveform = np.ascontiguousarray(mono, dtype=np.float32)
    if len(waveform) < MIN_SAMPLES:
        raise ValueError("Audio is too short; provide at least 1025 samples at the model rate")
    return waveform, duration_s


def transcribe_file(
    wav_path: Path, output_dir: Path, cache_dir: Path, device: Device = "cpu"
) -> dict[str, object]:
    """Record load, audio preparation, inference and total wall times in seconds."""
    # These must be set before importing either library: both read flags at import.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_MODULES_CACHE"] = str(cache_dir / "modules")
    started = perf_counter()
    verify_cache(cache_dir)
    transformers = import_module("transformers")
    torch = import_module("torch")
    if device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS is unavailable; use --device cpu")
    options = {
        "revision": MODELS[0].revision,
        "code_revision": MODELS[0].revision,
        "cache_dir": str(cache_dir),
        "local_files_only": True,
        "trust_remote_code": True,
        "token": False,
    }
    processor: _Processor = transformers.AutoProcessor.from_pretrained(
        MODELS[0].repository, **options
    )
    model: _Model = transformers.AutoModel.from_pretrained(
        MODELS[0].repository, **options, torch_dtype=torch.float32
    )
    model = model.eval().to(device)
    if device == "mps":
        torch.mps.synchronize()
    loaded = perf_counter()
    waveform, duration_s = read_waveform(wav_path, processor.sampling_rate)
    prepared = perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    model.transcribe(
        waveform,
        sampling_rate=processor.sampling_rate,
        output_dir=str(output_dir),
        dtype="fp32",
    )
    if device == "mps":
        torch.mps.synchronize()
    finished = perf_counter()
    timing: dict[str, object] = {
        "device": device,
        "sheetsage2_revision": MODELS[0].revision,
        "mert_revision": MODELS[1].revision,
        "audio_duration_s": duration_s,
        "load_s": loaded - started,
        "prepare_s": prepared - loaded,
        "inference_s": finished - prepared,
        "total_s": finished - started,
        "realtime_factor": (finished - started) / duration_s,
        "exceeds_3x": finished - started > 3 * duration_s,
    }
    (output_dir / "timing.json").write_text(json.dumps(timing, indent=2) + "\n")
    return timing
