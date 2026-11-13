"""The first-run harness names each stage against its budget and times the weight download."""

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import cold_start
from scripts.cold_start import StatusSample, first_mix, problems, stage_budgets, throughput

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/cold_start.py"


def test_each_route_prints_the_command_it_installs_with() -> None:
    assert cold_start.install_command("uvx", "1.2.3") == "uvx ghostband==1.2.3 --version"
    assert cold_start.install_command("brew", "1.2.3") == "brew install ghostband"
    assert cold_start.install_command("local", "1.2.3") == (
        "uv tool install --force " + str(ROOT / "dist/ghostband-1.2.3-py3-none-any.whl")
    )


def test_the_local_route_installs_before_the_run_so_its_row_is_not_budgeted() -> None:
    assert "install" in stage_budgets("uvx")
    assert "install" in stage_budgets("brew")
    assert "install" not in stage_budgets("local")
    assert stage_budgets("local") == {
        "ready-preview": 5.0,
        "chart": 120.0,
        "ready-live": 1800.0,
        "playing": 5.0,
    }


def test_every_mark_inside_its_budget_passes() -> None:
    marks = {
        "install": 12.5,
        "ready-preview": 0.4,
        "chart": 9.0,
        "ready-live": 41.0,
        "playing": 0.2,
    }
    assert problems(marks, stage_budgets("local")) == []


def test_a_slow_stage_is_named_with_its_budget() -> None:
    marks = {"ready-preview": 7.25, "chart": 1.0, "ready-live": 1.0, "playing": 1.0}
    assert problems(marks, stage_budgets("local")) == ["ready-preview took 7.2 s, budget 5.0 s"]


def test_a_stage_that_never_arrived_is_named() -> None:
    marks = {"ready-preview": 0.5, "chart": 3.0}
    assert problems(marks, stage_budgets("local")) == [
        "no ready-live mark",
        "no playing mark",
    ]


def test_the_download_rate_comes_from_the_bytes_and_the_sampled_time() -> None:
    samples = [
        StatusSample(at_s=0.0, state="installing", stage="models", done_bytes=0),
        StatusSample(at_s=0.5, state="installing", stage="models", done_bytes=0),
        StatusSample(at_s=1.5, state="installing", stage="models", done_bytes=300_000),
        StatusSample(at_s=3.0, state="installing", stage="models", done_bytes=900_000),
    ]
    assert throughput(samples) == 300_000.0


def test_one_progress_read_cannot_give_a_rate() -> None:
    assert throughput([]) is None
    assert throughput([StatusSample(1.0, "installing", "models", 400)]) is None


def test_progress_that_never_moved_cannot_give_a_rate() -> None:
    samples = [
        StatusSample(0.0, "installing", "models", 500),
        StatusSample(2.0, "installing", "models", 500),
    ]
    assert throughput(samples) is None


def test_the_first_mix_is_the_first_locked_track() -> None:
    lock = Path(__file__).with_name("aam-lock.json")
    lock.write_text(json.dumps({"tracks": [42, 7]}), encoding="utf-8")
    try:
        assert first_mix(lock, Path("/cache")) == Path("/cache/mixes/0042_mix.flac")
    finally:
        lock.unlink()


def options_for(tmp_path: Path, aam: Path | None) -> cold_start.Options:
    return cold_start.Options(
        route="local",
        version="0.1.0",
        aam=aam,
        cache=tmp_path / "aam",
        second=tmp_path / "second.mid",
        status=tmp_path / "first-run.json",
        report=None,
        fake_engine=True,
        timeout_s=60.0,
    )


def test_a_named_track_is_dropped_as_given(tmp_path: Path) -> None:
    track = tmp_path / "song.mid"
    track.write_bytes(b"MThd")
    options = options_for(tmp_path, track)
    assert cold_start.pinned_mix(options) == track
    assert cold_start.missing_mix(options) is None


def test_without_a_named_track_the_lock_names_the_first_mix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "aam-subset.json"
    lock.write_text(json.dumps({"tracks": [42, 7]}), encoding="utf-8")
    monkeypatch.setattr(cold_start, "DEFAULT_LOCK", lock)
    options = options_for(tmp_path, None)
    mix = tmp_path / "aam" / "mixes" / "0042_mix.flac"
    assert cold_start.pinned_mix(options) == mix
    assert cold_start.missing_mix(options) == (
        f"{mix} is missing; run `uv run python -m scripts.fetch_aam --cache "
        f"{tmp_path / 'aam'}` first"
    )
    mix.parent.mkdir(parents=True)
    mix.write_bytes(b"fLaC")
    assert cold_start.missing_mix(options) is None


def test_without_a_lock_the_harness_names_the_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cold_start, "DEFAULT_LOCK", tmp_path / "absent.json")
    assert cold_start.missing_mix(options_for(tmp_path, None)) == (
        f"{tmp_path / 'absent.json'} is missing; run `uv run python -m scripts.fetch_aam` first"
    )


def test_the_download_is_timed_from_the_weight_progress_file(tmp_path: Path) -> None:
    status = tmp_path / "first-run.json"
    watch = cold_start.StatusWatch(status, started_s=0.0)
    watch.sample(1.0)
    assert watch.download() is None

    writes = [
        ("installing", "models", 0),
        ("installing", "models", 250_000),
        ("installing", "models", 1_000_000),
    ]
    for index, (state, stage, done) in enumerate(writes):
        status.write_text(
            json.dumps(
                {
                    "state": state,
                    "stage": stage,
                    "done_bytes": done,
                    "total_bytes": 0,
                    "detail": "",
                }
            ),
            encoding="utf-8",
        )
        watch.sample(2.0 + index)

    assert watch.download() == {
        "first_bytes": 0,
        "last_bytes": 1_000_000,
        "seconds": 2.0,
        "bytes_per_s": 500_000,
    }
    assert watch.finished_s is None


def test_a_finished_install_ends_the_watch(tmp_path: Path) -> None:
    status = tmp_path / "first-run.json"
    watch = cold_start.StatusWatch(status, started_s=0.0)
    status.write_text(
        json.dumps(
            {
                "state": "ready",
                "stage": "ready",
                "done_bytes": 0,
                "total_bytes": 0,
                "detail": "",
            }
        ),
        encoding="utf-8",
    )
    watch.sample(7.5)
    assert watch.finished_s == 7.5
    assert cold_start.preview_before_download(watch, 2.0) is True
    assert cold_start.preview_before_download(watch, 9.0) is False


def test_a_run_that_never_downloaded_does_not_judge_the_preview(tmp_path: Path) -> None:
    watch = cold_start.StatusWatch(tmp_path / "first-run.json", started_s=0.0)
    assert cold_start.preview_before_download(watch, 2.0) is None
    assert cold_start.preview_before_download(None, 2.0) is None


def run_harness(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    if not Path(sys.executable).with_name("ghostband").is_file():
        pytest.skip("the harness needs the installed ghostband entry point")
    if not (ROOT / "conductor/ghostband/ui_dist/index.html").is_file():
        pytest.skip("the harness needs a built interface, which the wheel ships")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", cold_start.PORT)) == 0:
            pytest.skip(f"port {cold_start.PORT} is in use")
    return subprocess.run(  # noqa: S603 - the arguments are written in this test
        [
            sys.executable,
            str(SCRIPT),
            "--route",
            "local",
            "--fake-engine",
            "--status",
            str(tmp_path / "first-run.json"),
            "--aam",
            str(ROOT / "fixtures/greensleeves.mid"),
            "--timeout",
            "120",
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_the_harness_drives_a_whole_run_and_orders_its_stages(tmp_path: Path) -> None:
    report_path = tmp_path / "cold-start.json"
    finished = run_harness(tmp_path, "--report", str(report_path))

    assert finished.returncode == 0, finished.stderr
    written = json.loads(report_path.read_text())
    stages = written["stages"]
    assert written["route"] == "local"
    assert written["result"] == "pass"
    assert "install" not in stages
    assert "ready-preview" in stages
    assert "chart" in stages
    assert "ready-live" in stages
    assert "playing" in stages
    assert stages["ready-preview"] <= stages["chart"] <= stages["playing"]
    assert stages["playing-2"] > stages["chart-2"]
    assert written["download"] is None
    assert written["preview_before_download"] is None
    assert written["problems"] == []


def test_a_missing_track_stops_the_harness_before_it_starts(tmp_path: Path) -> None:
    missing = tmp_path / "0001_mix.flac"
    finished = run_harness(tmp_path, "--aam", str(missing))

    assert finished.returncode == 1
    assert f"FAIL: {missing} is missing" in finished.stderr
