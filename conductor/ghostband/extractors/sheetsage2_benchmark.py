"""Measure fixture extraction and optionally require identical CPU and MPS charts."""

import argparse
import asyncio
from pathlib import Path

from ghostband.chart import Chart
from ghostband.extractors.sheetsage2 import run_sheetsage2
from ghostband.extractors.sheetsage2_output import Timing


async def benchmark(
    fixtures: Path, output_dir: Path, cache_dir: Path, *, compare_mps: bool
) -> tuple[str, bool]:
    rows = [
        "# SheetSage2 extraction",
        "",
        "SheetSage2 and MERT-v2 weights are CC-BY-NC-4.0, for non-commercial use only.",
        "",
        "Each row includes a fresh model load, checksum verification, "
        "audio preparation and inference.",
        "CPU and MPS use float32. MPS is experimental until every fixture matches CPU exactly.",
        "",
        "| Fixture | Device | Audio s | Load s | Prepare s | Inference s "
        "| Total s | Ratio | >3x | Matches CPU |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    wavs = sorted(fixtures.glob("*.wav"))
    if not wavs:
        raise ValueError("No fixture WAVs found")
    mismatch = False
    for wav in wavs:
        reference = Chart.model_validate_json(wav.with_suffix(".golden.json").read_text())
        cpu: Chart | None = None
        for device in ("cpu", "mps") if compare_mps else ("cpu",):
            destination = output_dir / wav.stem / device
            chart = await run_sheetsage2(
                wav, output_dir=destination, cache_dir=cache_dir, device=device
            )
            chart = chart.model_copy(update={"title": reference.title})
            (destination / "chart.json").write_text(chart.model_dump_json(indent=2) + "\n")
            timing = Timing.model_validate_json((destination / "timing.json").read_text())
            if cpu is None:
                cpu = chart
            matches = cpu == chart
            mismatch |= not matches
            rows.append(
                f"| {wav.name} | {device} | {timing.audio_duration_s:.3f} | {timing.load_s:.3f} "
                f"| {timing.prepare_s:.3f} | {timing.inference_s:.3f} | {timing.total_s:.3f} "
                f"| {timing.realtime_factor:.3f} | {timing.exceeds_3x} | {matches} |"
            )
    rows.extend(
        [
            "",
            "MPS comparison: " + ("failed" if mismatch else "passed" if compare_mps else "not run"),
            "",
        ]
    )
    report = "\n".join(rows)
    (output_dir / "extraction.md").write_text(report)
    return report, not mismatch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("docs/benchmarks/extraction.md"))
    parser.add_argument("--compare-mps", action="store_true")
    args = parser.parse_args()
    report, matches = asyncio.run(
        benchmark(args.fixtures, args.output_dir, args.cache_dir, compare_mps=args.compare_mps)
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report)
    print(report)
    if not matches:
        parser.exit(1, "MPS charts differ from CPU; see the report and saved charts\n")


if __name__ == "__main__":
    main()
