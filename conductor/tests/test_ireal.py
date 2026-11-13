import json
from pathlib import Path
from urllib.parse import quote

import pytest
from click.testing import CliRunner
from ghostband.chart import Chart
from ghostband.cli import main
from ghostband.errors import GhostbandError
from ghostband.ireal import chart_to_ireal, read_ireal, style_text

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures/ireal"


def link(chart: str) -> str:
    """Build an unscrambled link; charts shorter than 50 characters need no swap."""
    return "irealb://Tune=Composer=Medium%20Swing=C==1r34LbKcu7" + quote(chart, safe="") + "==120=0"


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("Medium Swing", "medium swing jazz"),
        ("Up Tempo Swing", "up tempo swing jazz"),
        ("Bossa Nova", "Bossa Nova"),
        ("Funk", "Funk"),
    ],
)
def test_style_text(style: str, expected: str) -> None:
    assert style_text(style) == expected


@pytest.mark.parametrize(
    ("slug", "title", "signature", "key", "symbols"),
    [
        (
            "greensleeves",
            "Greensleeves",
            (6, 8),
            "Em",
            [
                "Em",
                "D",
                "Em",
                "B7",
                "Em",
                "D",
                "B7",
                "Em",
                "G",
                "D",
                "Em",
                "B7",
                "G",
                "D",
                "B7",
                "Em",
            ],
        ),
        (
            "ode-to-joy",
            "Ode to Joy",
            (4, 4),
            "D",
            ["D", "A7", "D", "A", "D", "A", "D", "A7", "G", "D"],
        ),
    ],
)
def test_single_playlist_and_html_forms_match(
    slug: str, title: str, signature: tuple[int, int], key: str, symbols: list[str]
) -> None:
    single = read_ireal(FIXTURES / f"{slug}.irealb.txt")
    playlist = read_ireal(FIXTURES / f"{slug}.irealbook.txt")
    document = read_ireal(FIXTURES / f"{slug}.html")
    assert single == playlist == document
    assert single.title == title
    assert single.time_signature == signature
    assert single.key == key
    assert [chord.symbol for chord in single.chords] == symbols
    assert single.rights == "unknown"
    assert single.extractor == "manual"


def test_import_cli_accepts_a_link_a_text_file_and_html(tmp_path: Path) -> None:
    runner = CliRunner()
    from_file = tmp_path / "from-file.chart.json"
    from_link = tmp_path / "from-link.chart.json"
    from_html = tmp_path / "from-html.chart.json"
    assert (
        runner.invoke(
            main, ["import", str(FIXTURES / "greensleeves.irealb.txt"), "-o", str(from_file)]
        ).exit_code
        == 0
    )
    url = (FIXTURES / "greensleeves.irealb.txt").read_text().strip()
    assert runner.invoke(main, ["import", url, "-o", str(from_link)]).exit_code == 0
    assert (
        runner.invoke(
            main, ["import", str(FIXTURES / "greensleeves.html"), "-o", str(from_html)]
        ).exit_code
        == 0
    )
    charts = [json.loads(path.read_text()) for path in (from_file, from_link, from_html)]
    assert charts[0] == charts[1] == charts[2]
    assert charts[0]["title"] == "Greensleeves"


@pytest.mark.parametrize(
    ("chart", "symbols"),
    [
        ("T44C |F |G |C Z", ["C", "F", "G", "C"]),
        ("T44{A |B |N1C |D }N2E |F Z", ["A", "B", "C", "D", "A", "B", "E", "F"]),
        ("T44C |x |G |x Z", ["C", "G"]),
        ("T44C |F |r Z", ["C", "F", "C", "F"]),
        ("T44C7 p |F Z", ["C7", "F"]),
        ("T44n |C Z", ["N.C.", "C"]),
        (
            "T44C-7 |F^7 |Bh7 |Ebo |G+ |A7sus4 Z",
            ["Cm7", "Fmaj7", "Bm7b5", "Ebdim", "Gaug", "A7sus4"],
        ),
        (
            "T44C9 |Cm9 |Cadd9 |Csus2 Z",
            ["C7", "Cm7", "C", "Csus2"],
        ),
        (
            "T44A7 |A7S |B7 |B7Q |C7 |C7Q |D7 |D7 Z",
            ["A7", "B7", "C7", "B7", "D7"],
        ),
    ],
)
def test_cell_grammar(chart: str, symbols: list[str]) -> None:
    assert [chord.symbol for chord in read_ireal(link(chart)).chords] == symbols


def test_chart_round_trips_through_a_link() -> None:
    data: dict[str, object] = {
        "schema_version": 1,
        "title": "Link test",
        "source": "test",
        "rights": "own-work",
        "extractor": "manual",
        "tempo_bpm": 100,
        "beat_times_s": [],
        "time_signature": [4, 4],
        "key": "C",
        "sections": [{"name": "A", "start_beat": 0, "end_beat": 16}],
        "chords": [
            {"start_beat": 0, "end_beat": 4, "symbol": "Cmaj7"},
            {"start_beat": 4, "end_beat": 8, "symbol": "F"},
            {"start_beat": 8, "end_beat": 12, "symbol": "G7"},
            {"start_beat": 12, "end_beat": 16, "symbol": "C"},
        ],
        "melody": [],
        "duration_beats": 16,
    }
    chart = Chart.model_validate(data)
    url = chart_to_ireal(chart)
    assert url.startswith("irealb://Link%20test=")
    assert read_ireal(url) == chart.model_copy(update={"rights": "unknown", "source": "iReal Pro"})


def test_export_cli_writes_a_link(tmp_path: Path) -> None:
    runner = CliRunner()
    chart_path = tmp_path / "greensleeves.chart.json"
    link_path = tmp_path / "greensleeves.irealb.txt"
    imported = runner.invoke(
        main, ["import", str(FIXTURES / "greensleeves.irealb.txt"), "-o", str(chart_path)]
    )
    assert imported.exit_code == 0, imported.output
    exported = runner.invoke(main, ["export", str(chart_path), "--ireal", "-o", str(link_path)])
    assert exported.exit_code == 0, exported.output
    assert link_path.read_text().startswith("irealb://")
    assert read_ireal(link_path) == Chart.model_validate_json(chart_path.read_text())


def test_documents_without_a_link_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.html"
    path.write_text("<html><body>no songs</body></html>")
    with pytest.raises(GhostbandError) as caught:
        read_ireal(path)
    assert int(caught.value.code) == 2
