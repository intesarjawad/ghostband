import json
from dataclasses import replace
from pathlib import Path

import pytest
from ghostband.chart import Chart
from ghostband.presets import PRESETS
from ghostband.render import OfflineEngine, Render, plan

from scripts.measure_sync import ROOT, PresetResult, Score
from scripts.sweep_cfg import Row, choose, confirmed_choice, main, record_cell


def row(cfg: int, rate: float, median: float) -> Row:
    return Row(
        cfg, 1, PresetResult("Folk / metal", 15, 0.5, rate, 0.1, median, (0.5, 0.4, 0.6), None)
    )


def test_selection_prioritises_hits_then_median_then_lower_guidance() -> None:
    assert choose([row(5, 0.5, 20), row(7, 0.8, 70), row(10, 0.7, 10)]) == 7
    assert choose([row(5, 0.8, 60), row(10, 0.8, 40), row(14, 0.8, 40)]) == 10
    empty = row(5, 0.5, 20)
    with pytest.raises(ValueError, match="without scored changes"):
        choose([replace(empty, result=replace(empty.result, real_within_tolerance=None))])


@pytest.mark.parametrize(
    ("condition", "has_melody", "has_chords"),
    [
        (None, True, True),
        ("chords_melody", True, True),
        ("chords", False, True),
        ("none", False, False),
    ],
)
def test_render_cells_keep_paired_seeds_and_distinct_identity_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    condition: str | None,
    has_melody: bool,
    has_chords: bool,
) -> None:
    calls: list[tuple[Chart, OfflineEngine, float, bool]] = []

    def render(
        chart: Chart, output: Path, engine: OfflineEngine, *, slider: float, sweep: bool
    ) -> Render:
        calls.append((chart, engine, slider, sweep))
        output.touch()
        return plan(chart)

    monkeypatch.setattr("scripts.sweep_cfg.render_chart", render)
    chart_path = ROOT / "charts/ode-to-joy.chart.json"
    for _ in range(2):
        record_cell(
            tmp_path,
            tmp_path / "engine",
            tmp_path / "models",
            10,
            1,
            chart_path,
            2,
            condition=condition,
            slider=None if condition is None else 1,
        )
    assert len(calls) == 1  # A completed sidecar is the resume marker.
    chart, engine, slider, sweep = calls[0]
    assert bool(chart.melody) == has_melody
    assert bool(chart.chords) == has_chords
    assert engine.seed_rotation == 2
    assert engine.guidance.cfg_notes == 10
    assert engine.prompt_b == PRESETS[1].b
    assert slider == (0 if condition is None else 1)
    assert sweep == (condition is None)
    data = json.loads(next(tmp_path.glob("*.take.json")).read_text())
    assert data["mode"] == "offline"
    assert data["dropped_frames"] == 0
    if condition is not None:
        assert data["condition"] == condition
        assert data["slider"] == 1


@pytest.mark.parametrize("identity_exit", [0, 1])
def test_shared_default_requires_identity_on_every_preset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_exit: int,
) -> None:
    cells: list[tuple[int, int, str | None, int | None]] = []

    def record(
        _folder: Path,
        _binary: Path,
        _models: Path,
        cfg: int,
        preset: int,
        _chart: Path,
        _repeat: int,
        *,
        condition: str | None = None,
        slider: int | None = None,
    ) -> None:
        cells.append((cfg, preset, condition, slider))

    def score(_folder: Path, cfg: int, preset: int) -> Row:
        result = row(cfg, cfg / 20, 40)
        return replace(
            result, preset=preset, result=replace(result.result, preset=PRESETS[preset].name)
        )

    def measure(_script: str, _folder: Path, _report: Path) -> int:
        return identity_exit

    def ceiling() -> list[Score]:
        return []

    monkeypatch.setattr("scripts.sweep_cfg.record_cell", record)
    monkeypatch.setattr("scripts.sweep_cfg.sync_row", score)
    monkeypatch.setattr("scripts.sweep_cfg.measure", measure)
    monkeypatch.setattr("scripts.sweep_cfg.sync.detector_ceiling", ceiling)
    monkeypatch.setattr(
        "sys.argv",
        [
            "sweep_cfg",
            str(tmp_path),
            "--engine",
            "engine",
            "--model-dir",
            "models",
            "--report",
            str(tmp_path / "report.md"),
        ],
    )
    assert main() == 0
    for preset in range(4):
        identity_cells = [cell for cell in cells if cell[1] == preset and cell[2] is not None]
        assert len(identity_cells) == 5 * 3 * 3 * 2
        assert {cell[0] for cell in identity_cells} == {14}
    selection = json.loads((tmp_path / "selection.json").read_text())
    assert selection["chosen"] == (14 if identity_exit == 0 else 5)
    assert "on all four presets" in (tmp_path / "report.md").read_text()


def test_shared_guidance_rejects_a_winner_that_does_not_generalise() -> None:
    rows = [
        replace(row(cfg, rate, 40), preset=preset)
        for preset in range(4)
        for cfg, rate in ((5, 0.85), (7, 0.83 if preset != 1 else 0.86))
    ]
    assert confirmed_choice(rows, 7, 0) == 5
    improved = [
        replace(value, result=replace(value.result, real_within_tolerance=0.90))
        if value.cfg == 7
        else value
        for value in rows
    ]
    assert confirmed_choice(improved, 7, 0) == 7
    assert confirmed_choice(improved, 7, 1) == 5
