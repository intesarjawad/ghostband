"""The take recorder plans every measured cell once and skips takes already recorded."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ghostband.presets import PRESETS

from scripts import record_takes
from scripts.record_takes import CHARTS, Take, plan


@pytest.mark.parametrize(
    ("protocol", "per_chart"),
    [
        ("sync", len(PRESETS) * 3),
        ("identity", len(PRESETS) * 3 * 2 * 3),
        ("tempo", 2 * 5 * 3),
    ],
)
def test_plan_covers_each_cell_once(protocol: str, per_chart: int, tmp_path: Path) -> None:
    takes = list(plan(protocol, tmp_path))

    assert len(takes) == per_chart * len(CHARTS)
    assert len({take.recording for take in takes}) == len(takes)
    assert all(take.recording.parent == tmp_path for take in takes)


def test_tempo_takes_carry_factor_and_onset_mode(tmp_path: Path) -> None:
    take = next(plan("tempo", tmp_path, (Path("charts/ode-to-joy.chart.json"),)))

    assert take.recording.name == "ode-to-joy-t0.5-o0-r1.wav"
    assert take.options == (
        "charts/ode-to-joy.chart.json",
        "--take-run",
        "1",
        "--take-tempo-factor",
        "0.5",
        "--take-onset-mode",
        "0",
    )


def test_recorded_takes_are_skipped_and_failures_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chart = Path("charts/ode-to-joy.chart.json")
    takes = list(plan("sync", tmp_path / "sync", (chart,)))
    takes[0].recording.parent.mkdir(parents=True)
    takes[0].sidecar.write_text("{}", encoding="utf-8")
    attempted: list[Take] = []

    def fail(take: Take) -> str:
        attempted.append(take)
        return "engine offline"

    monkeypatch.setattr(record_takes, "CHARTS", (chart,))
    monkeypatch.setattr(record_takes, "record", fail)
    monkeypatch.setattr(sys, "argv", ["record_takes.py", "sync", str(tmp_path)])

    assert record_takes.main() == 1
    assert attempted == takes[1:]
    assert f"{takes[1].recording.name}: engine offline" in capsys.readouterr().err


@pytest.mark.parametrize("dropped", [0, 3])
def test_a_take_with_dropped_frames_is_deleted_for_a_rerun(
    dropped: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    take = Take(tmp_path / "take.wav", ("charts/ode-to-joy.chart.json",))

    def play(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        take.recording.write_bytes(b"RIFF")
        take.sidecar.write_text(json.dumps({"dropped_frames": dropped}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(record_takes.subprocess, "run", play)
    reason = record_takes.record(take)

    if dropped:
        assert reason is not None
        assert "dropped 3 frames" in reason
        assert not take.sidecar.exists()
        assert not take.recording.exists()
    else:
        assert reason is None
        assert take.sidecar.is_file()
