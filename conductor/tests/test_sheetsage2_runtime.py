from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np
import pytest

if TYPE_CHECKING:
    from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / "extract/sheetsage2"


@pytest.fixture
def sheet_modules(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(sys, "path", [str(ENV), *sys.path])
    return SimpleNamespace(
        cache=import_module("sheetsage_cache"),
        pins=import_module("sheetsage_pins"),
        runtime=import_module("sheetsage_runtime"),
    )


def test_sheetsage2_fetch_checks_every_file_at_exact_revisions(
    tmp_path: Path, sheet_modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, pins = sheet_modules.cache, sheet_modules.pins
    content = b"local test resource"
    digest = hashlib.sha256(content).hexdigest()
    models = tuple(
        pins.ModelFiles(model.repository, model.revision, (("config.json", len(content), digest),))
        for model in pins.MODELS
    )
    monkeypatch.setattr(cache, "MODELS", models)
    calls: list[tuple[str, str, bool]] = []

    def download(*, repo_id: str, filename: str, revision: str, cache_dir: str, token: bool) -> str:
        calls.append((repo_id, revision, token))
        model = next(model for model in models if model.repository == repo_id)
        path = cache.snapshot_path(Path(cache_dir), model) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    cache.fetch(tmp_path)
    assert calls == [(model.repository, model.revision, False) for model in models]
    assert models[1].revision == "d8ba1c745e733b3908ce6ad16ebeb17ac7600a42"
    path = cache.snapshot_path(tmp_path, models[1]) / "config.json"
    path.write_bytes(b"x" * len(content))
    with pytest.raises(ValueError, match="Checksum or size mismatch"):
        cache.verify_cache(tmp_path)


def test_sheetsage2_fetch_rejects_corrupt_download(
    tmp_path: Path, sheet_modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "corrupt"
    path.write_bytes(b"x")

    def download(**_options: object) -> str:
        return str(path)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    with pytest.raises(ValueError, match="Checksum or size mismatch"):
        sheet_modules.cache.fetch(tmp_path)


def test_sheetsage2_missing_cache_fails_before_library_import(
    tmp_path: Path, sheet_modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_import(name: str) -> None:
        pytest.fail(f"Imported model library before checking the cache: {name}")

    monkeypatch.setattr(sheet_modules.runtime, "import_module", reject_import)
    with pytest.raises(FileNotFoundError, match="run the fetch command"):
        sheet_modules.runtime.transcribe_file(tmp_path / "absent.wav", tmp_path / "out", tmp_path)
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_sheetsage2_cli_requires_license_acceptance(tmp_path: Path) -> None:
    async def invoke() -> tuple[int | None, bytes]:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(ENV / "run.py"),
            "fetch",
            "--cache-dir",
            str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        return process.returncode, stderr

    code, stderr = asyncio.run(invoke())
    assert code == 2
    assert b"--accept-noncommercial-license" in stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("wav", sorted((ROOT / "fixtures").glob("*.wav")))
def test_sheetsage2_fixture_arrays_are_mono_finite_and_resampled(
    wav: Path, sheet_modules: SimpleNamespace
) -> None:
    soundfile = import_module("soundfile")
    info = soundfile.info(str(wav))
    waveform, duration = sheet_modules.runtime.read_waveform(wav, 24000)
    assert waveform.ndim == 1
    assert waveform.dtype == np.float32
    assert waveform.flags.c_contiguous
    assert np.isfinite(waveform).all()
    assert abs(len(waveform) - duration * 24000) <= 1
    assert duration == info.frames / info.samplerate


def test_sheetsage2_stereo_mix_and_input_limits(
    tmp_path: Path, sheet_modules: SimpleNamespace
) -> None:
    sf = import_module("soundfile")
    path = tmp_path / "audio.wav"
    samples = np.column_stack((np.full(2400, 0.75), np.full(2400, -0.25)))
    sf.write(str(path), samples, 24000, subtype="FLOAT")
    waveform, duration = sheet_modules.runtime.read_waveform(path, 24000)
    np.testing.assert_allclose(waveform, 0.25)
    assert duration == 0.1
    for samples, message in [
        (np.zeros(500), "too short"),
        (np.full(2400, np.nan), "finite"),
        (np.zeros(361 * 24000), "permissive"),
    ]:
        sf.write(str(path), samples, 24000, subtype="FLOAT")
        with pytest.raises(ValueError, match=message):
            sheet_modules.runtime.read_waveform(path, 24000)


@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_sheetsage2_array_call_offline_pins_and_timing(
    device: str, tmp_path: Path, sheet_modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = sheet_modules.runtime

    def verify(_path: Path) -> None:
        return None

    monkeypatch.setattr(runtime, "verify_cache", verify)
    clock = iter([10.0, 12.0, 13.0, 20.0])
    monkeypatch.setattr(runtime, "perf_counter", lambda: next(clock))
    calls: list[str] = []
    arrays: list[NDArray[np.float32]] = []
    options_seen: list[dict[str, object]] = []

    class Model:
        def eval(self) -> Model:
            return self

        def to(self, target: str) -> Model:
            calls.append(target)
            return self

        def transcribe(
            self,
            waveform: NDArray[np.float32],
            *,
            sampling_rate: int,
            output_dir: str,
            dtype: str,
        ) -> None:
            assert sampling_rate == 24000
            assert dtype == "fp32"
            assert Path(output_dir) == tmp_path / "out"
            arrays.append(waveform)

    def load(repository: str, **options: object) -> Model:
        assert repository == "m-a-p/SheetSage2"
        options_seen.append(options)
        return Model()

    def processor(*_args: object, **_options: object) -> SimpleNamespace:
        return SimpleNamespace(sampling_rate=24000)

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoModel=SimpleNamespace(from_pretrained=load),
            AutoProcessor=SimpleNamespace(from_pretrained=processor),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            float32="float32",
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
            mps=SimpleNamespace(synchronize=lambda: calls.append("sync")),
        ),
    )
    wav = tmp_path / "input.wav"
    import_module("soundfile").write(str(wav), np.zeros(24000), 24000)
    result = runtime.transcribe_file(wav, tmp_path / "out", tmp_path / "cache", device)
    assert len(arrays) == 1
    assert arrays[0].shape == (24000,)
    assert options_seen[0]["revision"] == "b83c3d79a6d98bcffaf8f9ed112c90aa887887bc"
    assert options_seen[0]["local_files_only"] is True
    assert options_seen[0]["cache_dir"] == str(tmp_path / "cache")
    assert calls == (["mps", "sync", "sync"] if device == "mps" else ["cpu"])
    assert result["load_s"] == 2
    assert result["prepare_s"] == 1
    assert result["inference_s"] == 7
    assert result["total_s"] == 10
    assert result["realtime_factor"] == 10
    assert result["exceeds_3x"] is True
    assert json.loads((tmp_path / "out/timing.json").read_text()) == result
