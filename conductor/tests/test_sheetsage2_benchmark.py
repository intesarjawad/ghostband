import asyncio
import json
import sys
from pathlib import Path

import pytest
from ghostband.chart import Chart
from ghostband.extractors import sheetsage2, sheetsage2_benchmark

ROOT = Path(__file__).resolve().parents[2]


def test_sheetsage2_benchmark_records_measured_times_and_mps_differences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def extract(wav_path: Path, *, output_dir: Path, cache_dir: Path, device: str) -> Chart:
        assert cache_dir == tmp_path / "cache"
        output_dir.mkdir(parents=True)
        chart = Chart.model_validate_json(wav_path.with_suffix(".golden.json").read_text())
        if device == "mps":
            chart = chart.model_copy(update={"key": "C#"})
        (output_dir / "timing.json").write_text(
            json.dumps(
                {
                    "device": device,
                    "sheetsage2_revision": "test",
                    "mert_revision": "test",
                    "audio_duration_s": 1,
                    "load_s": 1,
                    "prepare_s": 2,
                    "inference_s": 3,
                    "total_s": 6,
                    "realtime_factor": 6,
                    "exceeds_3x": True,
                }
            )
        )
        return chart

    monkeypatch.setattr(sheetsage2_benchmark, "run_sheetsage2", extract)
    report, matches = asyncio.run(
        sheetsage2_benchmark.benchmark(
            ROOT / "fixtures", tmp_path / "cpu", tmp_path / "cache", compare_mps=False
        )
    )
    assert report.count("| cpu |") == 5
    assert "1.000 | 1.000 | 2.000 | 3.000 | 6.000 | 6.000 | True | True" in report
    assert "MPS comparison: not run" in report
    assert matches
    report, matches = asyncio.run(
        sheetsage2_benchmark.benchmark(
            ROOT / "fixtures", tmp_path / "both", tmp_path / "cache", compare_mps=True
        )
    )
    assert not matches
    assert (tmp_path / "both/extraction.md").read_text() == report
    assert report.count("| mps |") == 5
    assert report.count("True | False |") == 5
    assert "MPS comparison: failed" in report
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark",
            "--fixtures",
            str(ROOT / "fixtures"),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output-dir",
            str(tmp_path / "cli"),
            "--report",
            str(tmp_path / "report.md"),
            "--compare-mps",
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        sheetsage2_benchmark.main()
    assert exit_info.value.code == 1
    assert (tmp_path / "report.md").read_text() == report


def test_sheetsage2_bridge_fails_with_subprocess_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"Missing cached file; run fetch"

    async def run(*command: str, **options: object) -> Process:
        assert "--offline" in command
        assert "--frozen" in command
        assert command[command.index("--project") + 1] == str(ROOT / "extract/sheetsage2")
        env = options["env"]
        assert isinstance(env, dict)
        assert env["HF_HUB_OFFLINE"] == "1"
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", run)
    with pytest.raises(RuntimeError, match="Missing cached file; run fetch"):
        asyncio.run(
            sheetsage2.run_sheetsage2(
                tmp_path / "input.wav", output_dir=tmp_path / "out", cache_dir=tmp_path / "cache"
            )
        )
