import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from ghostband import bench
from ghostband.cli import main
from ghostband.evaluation import live

CLAIMS = {
    "heard_agreement": {
        "warm": {"rate": 0.8125, "low": 0.75, "high": 0.875, "takes": 6},
        "bright": {"rate": 0.5, "low": 0.25, "high": 0.625, "takes": 4},
    },
    "tempo_factors_pass": [0.7, 1.0, 1.3],
    "tempo_range": [0.7, 1.3],
    "melody_survives": True,
    "melody_difference": {"mean": 0.125, "low": 0.0625, "high": 0.1875, "pairs": 9},
    "unrelated": "kept out of the report",
}
SYNC = """| Chart | Preset | Takes |
| --- | --- | ---: |
| tune | warm | 3 |

| Preset | Takes | Median error (ms) | Within 120 ms | Heard agreement (95% interval) | Verdict |
| --- | ---: | ---: | ---: | --- | --- |
| bright | 4 | 80 | 70.0% | 50.0% (25.0% to 62.5%) | fail |
| warm | 6 | 40 | 95.0% | 81.2% (75.0% to 87.5%) | pass |
"""
EXTRACTION = """group,subset,extractor,metric,tracks,failed,mean,low,high
aam,all,permissive,majmin,10,0,0.500,0.400,0.600
aam-detector,all,chord_detector,change_abs_error_s,8,0,0.125,0.100,0.150
aam-detector,all,chord_detector,majmin_accuracy,10,0,0.750,0.700,0.800
aam-detector,all,chord_detector,change_hit_rate,10,0,0.875,0.800,0.900
aam-detector,30 s and longer,chord_detector,majmin_accuracy,4,0,0.250,0.200,0.300
aam-detector,30 s and longer,chord_detector,change_hit_rate,0,0,,,
"""
BENCH_ROW = "| mrt2_base | 120 | 21.500 | 31.500 | 0 | 3000 | yes |"
FULL_REPORT = f"""# Live evaluation

Written by `ghostband eval live` from the measurements already taken on this
Mac. A section that reads "Not measured yet" has no data behind it.

## Engine frame time

Live use needs a run of at least 120 s with a
95th-percentile frame time under 32 ms and zero underruns.

| Model | Seconds | Frame p50 ms | Frame p95 ms | Underruns | Observations | Live |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
{BENCH_ROW}

mrt2_base: live: p95 31.5 ms, 0 underruns over 120 s

## Sync

Each take is scored against the chart's chord changes; the null moves the
chart by half a bar, and a real rate that does not beat it proves nothing
about sync.

| Preset | Takes | Median error (ms) | Within 120 ms | Verdict |
| --- | ---: | ---: | ---: | --- |
| bright | 4 | 80 | 70.0% | fail |
| warm | 6 | 40 | 95.0% | pass |

### Chord detector on AAM

The same triad detector produces the sync and heard agreement figures. AAM
mixes are rendered from instrument samples, so these rows show the detector
against known chords and do not predict accuracy on recorded music.

| Subset | Metric | Tracks | Mean | Low | High |
| --- | --- | ---: | ---: | ---: | ---: |
| all | majmin_accuracy | 10 | 0.750 | 0.700 | 0.800 |
| all | change_hit_rate | 10 | 0.875 | 0.800 | 0.900 |
| all | change_abs_error_s | 8 | 0.125 | 0.100 | 0.150 |
| 30 s and longer | majmin_accuracy | 4 | 0.250 | 0.200 | 0.300 |
| 30 s and longer | change_hit_rate | 0 | n/a | n/a | n/a |

## Heard agreement

The share of counted beats whose chord, named from the audio the band
played, matches the chart. Intervals are 95% percentile bootstrap intervals
over takes.

| Preset | Takes | Agreement (95% interval) |
| --- | ---: | --- |
| bright | 4 | 50.0% (25.0% to 62.5%) |
| warm | 6 | 81.2% (75.0% to 87.5%) |

## Tempo following

Passing tempo factors: 0.7, 1, 1.3.
Tempo range: 0.7 to 1.3.

## Melody survival

Melody F1 difference, chords and melody minus chords only, over
9 pairs of takes: 0.125 (0.062 to 0.188).
Melody survives: yes.
"""


@pytest.fixture
def sources(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "claims": tmp_path / "claims.json",
        "sync": tmp_path / "sync.md",
        "extraction": tmp_path / "extraction.csv",
    }
    paths["claims"].write_text(json.dumps(CLAIMS), encoding="utf-8")
    paths["sync"].write_text(SYNC, encoding="utf-8")
    paths["extraction"].write_text(EXTRACTION, encoding="utf-8")
    bench.save(bench.BenchResult("mrt2_base", 120, 21.5, 31.5, 0, 3000), tmp_path / "bench")
    return paths


def build(sources: dict[str, Path], tmp_path: Path) -> str:
    return live.build_report(
        sources["claims"], sources["sync"], sources["extraction"], tmp_path / "bench"
    )


def test_report_joins_every_source(sources: dict[str, Path], tmp_path: Path) -> None:
    assert build(sources, tmp_path) == FULL_REPORT


def test_detector_rows_sit_between_the_sync_and_heard_figures(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    report = build(sources, tmp_path)
    assert (
        report.index("## Sync")
        < report.index("| bright | 4 | 80 |")
        < report.index("### Chord detector on AAM")
        < report.index("| all | majmin_accuracy |")
        < report.index("## Heard agreement")
    )


def test_absent_sources_are_named_and_show_no_figures(tmp_path: Path) -> None:
    report = live.build_report(
        tmp_path / "claims.json",
        tmp_path / "sync.md",
        tmp_path / "extraction.csv",
        tmp_path / "bench",
    )
    assert report.count("Not measured yet: run `") == 6
    assert "ghostband bench --model mrt2_base --seconds 120" in report
    assert "scripts.measure_sync TAKES" in report
    assert "| " not in report


def test_a_claims_file_without_tempo_keeps_that_section_unmeasured(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    sources["claims"].write_text(json.dumps({"melody_survives": False}), encoding="utf-8")
    report = build(sources, tmp_path)
    assert "## Tempo following\n\nNot measured yet" in report
    assert "## Heard agreement\n\nThe share" in report
    assert "## Melody survival\n\nNot measured yet" in report


def test_a_failed_tempo_run_says_there_is_no_range(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    claims = {"tempo_factors_pass": [0.5], "tempo_range": None}
    sources["claims"].write_text(json.dumps(claims), encoding="utf-8")
    report = build(sources, tmp_path)
    assert "Passing tempo factors: 0.5.\nTempo range: none, 1.0 does not pass.\n" in report


def test_a_failed_melody_verdict_is_reported(sources: dict[str, Path], tmp_path: Path) -> None:
    claims = dict(CLAIMS, melody_survives=False)
    sources["claims"].write_text(json.dumps(claims), encoding="utf-8")
    assert "Melody survives: no.\n" in build(sources, tmp_path)


def test_extraction_without_detector_rows_is_unmeasured(
    sources: dict[str, Path], tmp_path: Path
) -> None:
    sources["extraction"].write_text(
        "\n".join(EXTRACTION.splitlines()[:2]) + "\n", encoding="utf-8"
    )
    assert "### Chord detector on AAM" in build(sources, tmp_path)
    assert live.read_detector_table(sources["extraction"]) is None


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("claims.json", "{", "claims file"),
        ("claims.json", '{"heard_agreement": {"warm": {"rate": 0.5}}}', "claims file"),
        ("claims.json", '{"melody_survives": "maybe"}', "claims file"),
        ("sync.md", "| Chart | Preset |\n| --- | --- |\n", "no preset table"),
        ("sync.md", "| Preset | Takes |\n| --- | ---: |\n| warm |\n", "ragged rows"),
        ("extraction.csv", "a,b\n1,2\n", "not an extraction summary"),
    ],
)
def test_malformed_sources_are_rejected(
    sources: dict[str, Path], tmp_path: Path, name: str, text: str, message: str
) -> None:
    (tmp_path / name).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        build(sources, tmp_path)


def test_command_writes_the_report(sources: dict[str, Path], tmp_path: Path) -> None:
    output = tmp_path / "out" / "live.md"
    result = CliRunner().invoke(
        main,
        [
            "eval",
            "live",
            "--output",
            str(output),
            "--claims",
            str(sources["claims"]),
            "--sync",
            str(sources["sync"]),
            "--extraction",
            str(sources["extraction"]),
            "--bench-dir",
            str(tmp_path / "bench"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("ghostband: Wrote")
    assert output.read_text(encoding="utf-8") == FULL_REPORT


def test_command_reads_and_writes_the_checkout_folder(
    sources: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "benchmarks"
    folder.mkdir()
    for flag, name in (
        ("claims", live.CLAIMS_NAME),
        ("sync", live.SYNC_NAME),
        ("extraction", live.EXTRACTION_NAME),
    ):
        (folder / name).write_text(sources[flag].read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(live, "default_directory", lambda: folder)
    result = CliRunner().invoke(main, ["eval", "live", "--bench-dir", str(tmp_path / "bench")])
    assert result.exit_code == 0, result.output
    assert (folder / live.REPORT_NAME).read_text(encoding="utf-8") == FULL_REPORT


def test_command_outside_a_checkout_needs_every_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(live, "default_directory", lambda: None)
    result = CliRunner().invoke(main, ["eval", "live", "--claims", str(tmp_path / "c.json")])
    assert result.exit_code == 2
    assert "choose --output, --sync, --extraction outside a checkout" in result.stderr


def test_command_reports_a_malformed_claims_file(tmp_path: Path) -> None:
    claims = tmp_path / "claims.json"
    claims.write_text("[]", encoding="utf-8")
    result = CliRunner().invoke(
        main,
        [
            "eval",
            "live",
            "--output",
            str(tmp_path / "live.md"),
            "--claims",
            str(claims),
            "--sync",
            str(tmp_path / "sync.md"),
            "--extraction",
            str(tmp_path / "extraction.csv"),
            "--bench-dir",
            str(tmp_path / "bench"),
        ],
    )
    assert result.exit_code == 2
    assert "ghostband: Invalid input:" in result.stderr
    assert not (tmp_path / "live.md").exists()
