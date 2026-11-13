"""Run the isolated SheetSage2 environment and validate its chart and timings."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from shutil import which

from ghostband.chart import Chart
from ghostband.extractors.sheetsage2_output import Timing, chart_from_output

EXTRACT_ENV = Path(__file__).resolve().parents[3] / "extract" / "sheetsage2"


async def run_sheetsage2(
    wav_path: Path, *, output_dir: Path, cache_dir: Path, device: str = "cpu"
) -> Chart:
    """Transcribe locally; model installation and fetching are explicit operations."""
    if device not in {"cpu", "mps"}:
        raise ValueError("Device must be cpu or mps")
    executable = which("uv")
    if executable is None:
        raise FileNotFoundError("Install uv before running the extractor")
    process = await asyncio.create_subprocess_exec(
        executable,
        "run",
        "--offline",
        "--frozen",
        "--project",
        str(EXTRACT_ENV),
        "python",
        str(EXTRACT_ENV / "run.py"),
        "transcribe",
        str(wav_path.resolve()),
        "--output-dir",
        str(output_dir.resolve()),
        "--cache-dir",
        str(cache_dir.resolve()),
        "--device",
        device,
        env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(
            f"SheetSage2 exited with status {process.returncode}: {stderr.decode().strip()}"
        )
    Timing.model_validate_json(stdout)
    chart = chart_from_output(output_dir, title=wav_path.stem, source=wav_path.name)
    (output_dir / "chart.json").write_text(chart.model_dump_json(indent=2) + "\n")
    return chart


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    args = parser.parse_args()
    chart = asyncio.run(
        run_sheetsage2(
            args.wav, output_dir=args.output_dir, cache_dir=args.cache_dir, device=args.device
        )
    )
    print(json.dumps(chart.model_dump(), allow_nan=False))


if __name__ == "__main__":
    main()
