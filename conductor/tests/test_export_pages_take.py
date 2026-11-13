"""The Pages roll's register loudness: each register reads its own band on a 30 dB scale."""

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from ghostband.cli import write_take_sidecar
from ghostband.setup_models import sha256_file

from scripts.export_pages_take import ENVELOPE_RATE, envelopes, export, pooled
from scripts.measure_sync import TOLERANCE_S

RATE = 22_050


def test_each_register_hears_only_its_band() -> None:
    seconds = np.arange(RATE * 2) / RATE
    bass = np.sin(2 * np.pi * 80 * seconds) * (seconds < 1)
    treble = np.sin(2 * np.pi * 5000 * seconds) * (seconds >= 1)
    out = envelopes((bass + treble).astype(np.float32), RATE)

    first, second = slice(10, ENVELOPE_RATE - 10), slice(ENVELOPE_RATE + 10, -10)
    assert np.min(out["low"][first]) > 0.9
    assert np.max(out["low"][second]) < 0.1
    assert np.min(out["high"][second]) > 0.9
    assert np.max(out["high"][first]) < 0.1
    assert len(out["mid"]) == 2 * ENVELOPE_RATE


@pytest.mark.parametrize(
    "problem",
    [
        "drops",
        "missing_drops",
        "offline",
        "fake",
        "missing_provenance",
        "missing_mode",
        "fake_provenance",
        "malformed_provenance",
        "changed_chart",
        "changed_recording",
    ],
)
def test_export_rejects_invalid_takes_before_scoring(tmp_path: Path, problem: str) -> None:
    chart, recording, sidecar = make_take(tmp_path)
    data = json.loads(sidecar.read_text())
    if problem == "drops":
        data["dropped_frames"] = 999
    elif problem == "missing_drops":
        del data["dropped_frames"]
    elif problem in {"offline", "fake"}:
        data["mode"] = problem
    elif problem == "missing_mode":
        del data["mode"]
    elif problem == "fake_provenance":
        data["provenance"]["engine"] = "fake-engine"
    elif problem == "malformed_provenance":
        data["provenance"]["recording_sha256"] = "invalid"
    elif problem == "missing_provenance":
        del data["provenance"]
    elif problem == "changed_chart":
        chart.write_text(chart.read_text() + " ")
    else:
        recording.write_bytes(b"changed")
    sidecar.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=r"requires live|provenance|fake-engine"):
        export(sidecar)
    with pytest.raises(ValueError, match=r"requires live|provenance|fake-engine"):
        pooled([sidecar])


def make_take(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = Path(__file__).resolve().parents[2]
    chart = tmp_path / "tune.chart.json"
    chart.write_bytes((root / "charts/greensleeves.chart.json").read_bytes())
    recording = tmp_path / "take.wav"
    with wave.open(str(recording), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(bytes(RATE * 5 * 4))
    sidecar = write_take_sidecar(
        recording,
        chart_path=chart,
        preset="Warm jazz / chiptune",
        run=1,
        play_time_s=0,
        beat_log=[(0, 0.2), (4, 2.2)],
    )
    return chart, recording, sidecar


def test_export_retains_take_provenance(tmp_path: Path) -> None:
    _, _, sidecar = make_take(tmp_path)
    raw = json.loads(sidecar.read_text())
    data = export(sidecar)
    assert data["take"]["provenance"] == raw["provenance"]
    assert data["take"]["mode"] == "live"
    assert data["take"]["dropped_frames"] == 0


def test_diagnostic_export_preserves_unknown_capture_metadata(tmp_path: Path) -> None:
    chart, recording, sidecar = make_take(tmp_path)
    raw = json.loads(sidecar.read_text())
    for field in ("mode", "dropped_frames", "provenance"):
        del raw[field]
    sidecar.write_text(json.dumps(raw))
    data = export(sidecar, diagnostic=True)
    assert data["take"]["validation"] == "diagnostic"
    assert data["take"]["mode"] == "unknown"
    assert data["take"]["dropped_frames"] is None
    assert data["take"]["provenance"] == {
        "engine": None,
        "ghostband_version": None,
        "model_revision": None,
        "chart_sha256": sha256_file(chart),
        "recording_sha256": sha256_file(recording),
    }
    pool = pooled([sidecar], diagnostic=True)
    assert pool["validation"] == "diagnostic"
    assert pool["sources"][0]["sidecar"] == raw
    assert pool["sources"][0]["provenance"] == data["take"]["provenance"]
    assert pool["sources"][0]["sidecar_sha256"] == sha256_file(sidecar)
    with pytest.raises(ValueError, match="requires live"):
        export(sidecar)


@pytest.mark.parametrize("problem", ["drops", "fake", "offline", "changed_recording"])
def test_diagnostic_export_still_rejects_invalid_capture_evidence(
    tmp_path: Path, problem: str
) -> None:
    _, recording, sidecar = make_take(tmp_path)
    raw = json.loads(sidecar.read_text())
    if problem == "drops":
        raw["dropped_frames"] = 1
    elif problem == "changed_recording":
        recording.write_bytes(b"changed")
    else:
        raw["mode"] = problem
    sidecar.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=r"requires live|provenance"):
        export(sidecar, diagnostic=True)
    with pytest.raises(ValueError, match=r"requires live|provenance"):
        pooled([sidecar], diagnostic=True)


def test_pool_rejects_duplicate_sources(tmp_path: Path) -> None:
    _, _, sidecar = make_take(tmp_path)
    with pytest.raises(ValueError, match="duplicate"):
        pooled([sidecar, sidecar])


def test_export_command_runs_from_the_repository_root() -> None:
    root = Path(__file__).resolve().parents[2]
    finished = subprocess.run(
        [sys.executable, "scripts/export_pages_take.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert "--diagnostic" in finished.stdout


def test_shipped_take_has_current_scores_and_replayable_pool_sources() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "ui/public/takes/oh-susanna-t1-o1-r2.json"
    data = json.loads(path.read_text())
    assert data["audio"]["sha256"] == sha256_file(path.parent / data["audio"]["file"])
    take = data["take"]
    assert {"mode", "dropped_frames", "provenance", "validation"} <= take.keys()
    errors = [
        abs(change["found_s"] - change["chart_s"])
        for change in data["changes"]
        if change["found_s"] is not None and change["chart_s"] is not None
    ]
    assert data["score"]["within_120"] == sum(error <= TOLERANCE_S for error in errors)
    pool = data["pool"]
    assert len(pool["sources"]) == pool["takes"]
    assert sum(source["eligible"] for source in pool["sources"]) == pool["eligible"]
    assert sum(source["within_120"] for source in pool["sources"]) == pool["within_120"]
    names: set[str] = set()
    for source in pool["sources"]:
        assert source["name"] not in names
        names.add(source["name"])
        assert source["provenance"]["chart_sha256"] == sha256_file(
            root / source["sidecar"]["chart"]
        )
        assert not Path(source["sidecar"]["chart"]).is_absolute()
        assert source["sidecar"]["beat_audible"]
    selected = next(
        source for source in pool["sources"] if source["name"] == take["id"] + ".take.json"
    )
    assert selected["provenance"] == take["provenance"]
