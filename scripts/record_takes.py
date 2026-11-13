"""Record the real-engine takes the sync, identity and tempo measurements score.

Run it as `uv run python scripts/record_takes.py sync|identity|tempo|all TAKES`, from the
repository root, with the models installed. Each take is one `ghostband play --record` run
with the take options its measurement needs, written to TAKES/<protocol>/. A take whose
sidecar already exists is kept, so an interrupted run resumes where it stopped; a take
that fails is reported and the rest still run. A take in which the engine dropped frames
is a failure and is deleted: its gaps would score the machine's load, not the band.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ghostband.presets import PRESETS

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_identity import CONDITIONS, SLIDERS
from scripts.measure_sync import ROOT
from scripts.measure_tempo import FACTORS, ONSET_MODES, RUNS

CHARTS = tuple(sorted((ROOT / "charts").glob("*.chart.json")))
PROTOCOLS = ("sync", "identity", "tempo")
# A chart plays for under two minutes even at half tempo; a hung engine must not stall the night.
TAKE_TIMEOUT_S = 600


@dataclass(frozen=True)
class Take:
    """One recording and the play options that produce it."""

    recording: Path
    options: tuple[str, ...]

    @property
    def sidecar(self) -> Path:
        return self.recording.with_suffix(".take.json")


def _stem(chart: Path) -> str:
    return chart.name.removesuffix(".chart.json")


def plan(protocol: str, folder: Path, charts: tuple[Path, ...] = CHARTS) -> Iterator[Take]:
    """Every take a protocol needs, in a fixed order so a resumed run skips the same ones."""
    for chart in charts:
        for run in range(1, RUNS + 1):
            common = (str(chart), "--take-run", str(run))
            if protocol == "sync":
                for preset in range(len(PRESETS)):
                    name = f"{_stem(chart)}-p{preset}-r{run}.wav"
                    yield Take(folder / name, (*common, "--preset", str(preset), "--slider-sweep"))
            elif protocol == "identity":
                for preset in range(len(PRESETS)):
                    for condition in CONDITIONS:
                        for slider in SLIDERS:
                            name = f"{_stem(chart)}-p{preset}-{condition}-s{slider}-r{run}.wav"
                            options = ("--take-condition", condition, "--take-slider", str(slider))
                            yield Take(folder / name, (*common, "--preset", str(preset), *options))
            else:
                for mode in ONSET_MODES:
                    for factor in FACTORS:
                        name = f"{_stem(chart)}-t{factor:g}-o{mode}-r{run}.wav"
                        options = (
                            "--take-tempo-factor",
                            f"{factor:g}",
                            "--take-onset-mode",
                            str(mode),
                        )
                        yield Take(folder / name, (*common, *options))


def record(take: Take) -> str | None:
    """Play one take; return None on success, else the reason it failed."""
    take.recording.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(Path(sys.executable).with_name("ghostband")),
        "play",
        *take.options,
        "--record",
        str(take.recording),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable and generated options
            command, capture_output=True, text=True, timeout=TAKE_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired:
        return f"no result after {TAKE_TIMEOUT_S} s"
    if result.returncode != 0 or not take.sidecar.is_file():
        return (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"
    dropped = json.loads(take.sidecar.read_text(encoding="utf-8")).get("dropped_frames", 0)
    if dropped:
        # Removing the take makes a rerun record it again, on a quieter machine.
        take.sidecar.unlink()
        take.recording.unlink(missing_ok=True)
        return (
            f"the engine dropped {dropped} frames, so the take has gaps; stop other heavy "
            "work on this Mac and rerun the same command"
        )
    return None


def main() -> int:
    """Record every missing take and return nonzero if any of them failed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", choices=(*PROTOCOLS, "all"))
    parser.add_argument("takes", type=Path, help="folder that gets one subfolder per protocol")
    args = parser.parse_args()
    protocols = PROTOCOLS if args.protocol == "all" else (args.protocol,)
    failed: list[str] = []
    for protocol in protocols:
        takes = list(plan(protocol, args.takes / protocol, CHARTS))
        for number, take in enumerate(takes, start=1):
            if take.sidecar.is_file():
                continue
            sys.stdout.write(f"{protocol} {number}/{len(takes)} {take.recording.name}\n")
            sys.stdout.flush()
            reason = record(take)
            if reason is not None:
                failed.append(take.recording.name)
                sys.stderr.write(f"{take.recording.name}: {reason}\n")
    if failed:
        sys.stderr.write(f"{len(failed)} takes failed; rerun the same command to retry them\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
