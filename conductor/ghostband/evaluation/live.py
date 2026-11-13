"""Collect the live measurements already on disk into one Markdown report.

Nothing is measured here. Each section reads the file a measurement script or
command wrote: the claims file for heard agreement, tempo following and melody
survival, the sync report's preset table, the extraction summary for the chord
detector on AAM, and the saved engine benchmark runs. A source that is absent
leaves its section saying so with the command that produces it, so the report
never shows a figure nobody took.
"""

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ghostband import bench, first_run
from ghostband.evaluation import extraction

REPORT_NAME = "live.md"
CLAIMS_NAME = "claims.json"
SYNC_NAME = "sync.md"
EXTRACTION_NAME = "extraction.csv"
# The sync table reports heard agreement too; the claims file is its one source here.
DUPLICATE_COLUMN = "Heard agreement"
SYNC_COMMAND = (
    "uv run python -m scripts.measure_sync TAKES --report docs/benchmarks/sync.md "
    "--claims docs/benchmarks/claims.json"
)
TEMPO_COMMAND = "uv run python scripts/measure_tempo.py"
IDENTITY_COMMAND = "uv run python -m scripts.measure_identity TAKES"
EXTRACTION_COMMAND = "uv run python -m ghostband.evaluation.extraction --output-dir docs/benchmarks"
BENCH_COMMAND = f"ghostband bench --model {bench.BASE_MODEL} --seconds {bench.LIVE_MIN_SECONDS:g}"


class _Agreement(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    rate: float
    low: float
    high: float
    takes: int


class _Difference(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    mean: float
    low: float
    high: float
    pairs: int


class Claims(BaseModel):
    """The claims file; every measurement is optional until its script has run."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    heard_agreement: dict[str, _Agreement] | None = None
    tempo_factors_pass: list[float] | None = None
    tempo_range: tuple[float, float] | None = None
    melody_survives: bool | None = None
    melody_difference: _Difference | None = None


@dataclass(frozen=True)
class Table:
    """A Markdown table kept as text, with its alignment row."""

    header: list[str]
    align: list[str]
    rows: list[list[str]]

    def render(self) -> list[str]:
        return [
            _row(self.header),
            _row(self.align),
            *(_row(cells) for cells in self.rows),
        ]


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _split(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def default_directory() -> Path | None:
    """The checkout's benchmark folder, or None when running from an installed package."""
    folder = first_run.ROOT / "docs" / "benchmarks"
    return folder if folder.is_dir() else None


def read_claims(path: Path) -> Claims | None:
    """The claims file, or None when it does not exist."""
    if not path.is_file():
        return None
    try:
        return Claims.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise ValueError(f"{path} is not a valid claims file: {error}") from error


def read_sync_table(path: Path) -> Table | None:
    """The per-preset table of the sync report without its heard agreement column."""
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        header = _split(line) if line.startswith("|") else []
        if header[:1] != ["Preset"]:
            continue
        align = _split(lines[index + 1]) if index + 1 < len(lines) else []
        rows: list[list[str]] = []
        for text in lines[index + 2 :]:
            if not text.startswith("|"):
                break
            rows.append(_split(text))
        if len(align) != len(header) or any(len(cells) != len(header) for cells in rows):
            raise ValueError(f"{path} has a preset table with ragged rows")
        keep = [i for i, name in enumerate(header) if not name.startswith(DUPLICATE_COLUMN)]
        return Table(
            [header[i] for i in keep],
            [align[i] for i in keep],
            [[cells[i] for i in keep] for cells in rows],
        )
    raise ValueError(f"{path} has no preset table")


def read_detector_table(path: Path) -> Table | None:
    """The chord detector's rows of the extraction summary, or None when there are none."""
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    if records and not set(extraction.SUMMARY_HEADER) <= set(records[0]):
        raise ValueError(f"{path} is not an extraction summary")
    rows: list[list[str]] = []
    for subset, _ in extraction.SUBSETS:
        for metric in extraction.DETECTOR_METRICS:
            for record in records:
                if (record["group"], record["subset"], record["metric"]) != (
                    extraction.DETECTOR_GROUP,
                    subset,
                    metric,
                ):
                    continue
                numbers = [record[name] or "n/a" for name in ("mean", "low", "high")]
                rows.append([subset, metric, record["tracks"], *numbers])
    if not rows:
        return None
    return Table(
        ["Subset", "Metric", "Tracks", "Mean", "Low", "High"],
        ["---", "---", "---:", "---:", "---:", "---:"],
        rows,
    )


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _missing(command: str) -> list[str]:
    return [f"Not measured yet: run `{command}`.", ""]


def _bench_section(results: list[bench.BenchResult], status: bench.ModelStatus) -> list[str]:
    lines = [
        "## Engine frame time",
        "",
        f"Live use needs a run of at least {bench.LIVE_MIN_SECONDS:g} s with a",
        f"95th-percentile frame time under {bench.LIVE_P95_LIMIT_MS:g} ms and zero underruns.",
        "",
    ]
    if not results:
        return [*lines, *_missing(BENCH_COMMAND)]
    table = Table(
        ["Model", "Seconds", "Frame p50 ms", "Frame p95 ms", "Underruns", "Observations", "Live"],
        ["---", "---:", "---:", "---:", "---:", "---:", "---"],
        [
            [
                r.model,
                f"{r.seconds:g}",
                f"{r.p50_ms:.3f}",
                f"{r.p95_ms:.3f}",
                str(r.underruns),
                str(r.observations),
                "yes" if r.live else "no",
            ]
            for r in results
        ],
    )
    return [*lines, *table.render(), "", f"{bench.BASE_MODEL}: {status.message}", ""]


def _sync_section(sync: Table | None, detector: Table | None) -> list[str]:
    lines = [
        "## Sync",
        "",
        "Each take is scored against the chart's chord changes; the null moves the",
        "chart by half a bar, and a real rate that does not beat it proves nothing",
        "about sync.",
        "",
        *(_missing(SYNC_COMMAND) if sync is None else [*sync.render(), ""]),
        "### Chord detector on AAM",
        "",
        "The same triad detector produces the sync and heard agreement figures. AAM",
        "mixes are rendered from instrument samples, so these rows show the detector",
        "against known chords and do not predict accuracy on recorded music.",
        "",
    ]
    return [
        *lines,
        *(_missing(EXTRACTION_COMMAND) if detector is None else [*detector.render(), ""]),
    ]


def _heard_section(claims: Claims | None) -> list[str]:
    lines = [
        "## Heard agreement",
        "",
        "The share of counted beats whose chord, named from the audio the band",
        "played, matches the chart. Intervals are 95% percentile bootstrap intervals",
        "over takes.",
        "",
    ]
    if claims is None or claims.heard_agreement is None:
        return [*lines, *_missing(SYNC_COMMAND)]
    table = Table(
        ["Preset", "Takes", "Agreement (95% interval)"],
        ["---", "---:", "---"],
        [
            [
                preset,
                str(value.takes),
                f"{_percent(value.rate)} ({_percent(value.low)} to {_percent(value.high)})",
            ]
            for preset, value in sorted(claims.heard_agreement.items())
        ],
    )
    return [*lines, *table.render(), ""]


def _tempo_section(claims: Claims | None) -> list[str]:
    lines = ["## Tempo following", ""]
    if claims is None or claims.tempo_factors_pass is None:
        return [*lines, *_missing(TEMPO_COMMAND)]
    passing = ", ".join(f"{factor:g}" for factor in claims.tempo_factors_pass) or "none"
    lines.append(f"Passing tempo factors: {passing}.")
    if claims.tempo_range is None:
        lines.append("Tempo range: none, 1.0 does not pass.")
    else:
        low, high = claims.tempo_range
        lines.append(f"Tempo range: {low:g} to {high:g}.")
    return [*lines, ""]


def _melody_section(claims: Claims | None) -> list[str]:
    lines = ["## Melody survival", ""]
    if claims is None or claims.melody_difference is None or claims.melody_survives is None:
        return [*lines, *_missing(IDENTITY_COMMAND)]
    difference = claims.melody_difference
    return [
        *lines,
        "Melody F1 difference, chords and melody minus chords only, over",
        f"{difference.pairs} pairs of takes: {difference.mean:.3f} "
        f"({difference.low:.3f} to {difference.high:.3f}).",
        f"Melody survives: {'yes' if claims.melody_survives else 'no'}.",
        "",
    ]


def build_report(
    claims_path: Path,
    sync_path: Path,
    extraction_path: Path,
    bench_directory: Path | None = None,
) -> str:
    """Read every source and write the report; an absent source is named, not guessed."""
    claims = read_claims(claims_path)
    results = [
        result
        for model in bench.MODELS
        if (result := bench.load(model, bench_directory)) is not None
    ]
    lines = [
        "# Live evaluation",
        "",
        "Written by `ghostband eval live` from the measurements already taken on this",
        'Mac. A section that reads "Not measured yet" has no data behind it.',
        "",
        *_bench_section(results, bench.base_model_status(bench_directory)),
        *_sync_section(read_sync_table(sync_path), read_detector_table(extraction_path)),
        *_heard_section(claims),
        *_tempo_section(claims),
        *_melody_section(claims),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_report(
    output: Path,
    claims_path: Path,
    sync_path: Path,
    extraction_path: Path,
    bench_directory: Path | None = None,
) -> None:
    text = build_report(claims_path, sync_path, extraction_path, bench_directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
