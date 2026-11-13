"""Decide whether SheetSage2 is offered over the permissive extractor, from AAM chord scores.

Run it as `uv run python -m scripts.measure_sheetsage2_gain`, from the repository root, after
`ghostband.evaluation.extraction --output-dir docs/benchmarks` has written
`extraction_tracks.csv`.

Both extractors are scored on the same AAM tracks. The gain is SheetSage2's chord majmin minus
the permissive extractor's on each track both extracted, summarised with the seeded percentile
bootstrap over tracks that the extraction report uses. The offer is enabled only when the
interval's lower bound is above zero. The result is merged into the claims file as
`sheetsage2_gain`, which is null when no track has both scores.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from ghostband.evaluation.extraction import bootstrap_mean
from ghostband.importing import ROOT

CLAIMS_PATH = ROOT / "docs" / "benchmarks" / "claims.json"
TRACKS_PATH = CLAIMS_PATH.parent / "extraction_tracks.csv"
GROUP = "aam"
METRIC = "majmin"
CHALLENGER = "sheetsage2"
BASELINE = "permissive"
OFFER_PREFIX = "Offer: "

Interval = tuple[float, float, float]


def read_scores(path: Path) -> dict[str, dict[str, float]]:
    """AAM chord majmin per extractor and track; failed extractions have no score."""
    scores: dict[str, dict[str, float]] = {CHALLENGER: {}, BASELINE: {}}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (
                row["group"] == GROUP
                and row["metric"] == METRIC
                and row["extractor"] in scores
                and row["failed"] == "false"
                and row["value"] != ""
            ):
                scores[row["extractor"]][row["track"]] = float(row["value"])
    return scores


def paired_tracks(scores: dict[str, dict[str, float]]) -> list[str]:
    """Tracks both extractors scored, in name order."""
    return sorted(scores[CHALLENGER].keys() & scores[BASELINE].keys())


def paired_gain(scores: dict[str, dict[str, float]]) -> Interval | None:
    """SheetSage2 minus permissive per track: mean and 95 percent interval, or None."""
    return bootstrap_mean(
        [scores[CHALLENGER][track] - scores[BASELINE][track] for track in paired_tracks(scores)]
    )


def offer_enabled(gain: Interval | None) -> bool:
    """The interval of the per-track difference sits above zero."""
    return gain is not None and gain[1] > 0


def _interval(value: Interval | None) -> str:
    if value is None:
        return "n/a"
    mean, low, high = value
    return f"{mean:.3f} ({low:.3f} to {high:.3f})"


def offer_line(gain: Interval | None, pairs: int) -> str:
    """The one-line verdict an ADR carries; it quotes the difference when the offer is on."""
    if gain is None:
        return f"{OFFER_PREFIX}not enabled. No track has a chord score from both extractors."
    mean, low, high = gain
    if offer_enabled(gain):
        return (
            f"{OFFER_PREFIX}enabled. On AAM chord majmin SheetSage2 beats the permissive "
            f"extractor by {mean:.3f} per track (95% interval {low:.3f} to {high:.3f}, "
            f"{pairs} paired tracks)."
        )
    return (
        f"{OFFER_PREFIX}not enabled. The per-track difference in AAM chord majmin is "
        f"{mean:.3f} (95% interval {low:.3f} to {high:.3f}, {pairs} paired tracks), "
        "which does not sit above zero."
    )


def offer_present(adr_text: str) -> bool:
    """Whether an ADR's offer verdict line says the offer is enabled."""
    return any(line.startswith(f"{OFFER_PREFIX}enabled.") for line in adr_text.splitlines())


def render_table(scores: dict[str, dict[str, float]]) -> str:
    """The AAM chord majmin of each extractor on the paired tracks, and their difference."""
    tracks = paired_tracks(scores)
    rows = [
        "| Extractor | Paired tracks | AAM chord majmin (95% interval) |",
        "| --- | ---: | --- |",
    ]
    for extractor in (BASELINE, CHALLENGER):
        interval = bootstrap_mean([scores[extractor][track] for track in tracks])
        rows.append(f"| {extractor} | {len(tracks)} | {_interval(interval)} |")
    rows.append(
        f"| {CHALLENGER} minus {BASELINE} | {len(tracks)} | {_interval(paired_gain(scores))} |"
    )
    return "\n".join(rows) + "\n"


def write_claims(path: Path, gain: Interval | None, pairs: int) -> None:
    """Merge the gain into the claims file, keeping other claims; null when unmeasured."""
    claims: dict[str, object] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    if gain is None:
        claims["sheetsage2_gain"] = None
    else:
        mean, low, high = gain
        claims["sheetsage2_gain"] = {"mean": mean, "low": low, "high": high, "pairs": pairs}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    """Print the table and verdict, write the claim, and return nonzero when unmeasured."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, default=TRACKS_PATH)
    parser.add_argument("--claims", type=Path, default=CLAIMS_PATH)
    args = parser.parse_args()
    if not args.tracks.is_file():
        parser.error(f"{args.tracks} not found; run ghostband.evaluation.extraction first")
    scores = read_scores(args.tracks)
    gain = paired_gain(scores)
    pairs = len(paired_tracks(scores))
    write_claims(args.claims, gain, pairs)
    sys.stdout.write(f"{render_table(scores)}\n{offer_line(gain, pairs)}\n")
    return 0 if gain is not None else 1


if __name__ == "__main__":
    sys.exit(main())
