import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from ghostband import bench, setup_models
from ghostband.cli import main
from ghostband.engine_client import Command
from ghostband.setup_models import ModelFile

ALPHA = ModelFile(
    "resources/alpha.txt", 6, "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060"
)
BASE = ModelFile(
    "models/mrt2_base/base.txt",
    5,
    "f34848ca92665c342abd5816c9e3eda0e82180671195362bcd0080544a3bc2ac",
)
PASSING_REPLY: Command = {
    "ok": True,
    "frame_time": {"p50_ms": 21.5, "p95_ms": 31.5, "observations": 3000},
    "underruns": 0,
}


def result(
    *, seconds: float = 120, p95_ms: float = 31.5, underruns: int = 0, model: str = "mrt2_base"
) -> bench.BenchResult:
    return bench.BenchResult(model, seconds, 21.5, p95_ms, underruns, 3000)


@pytest.mark.parametrize(
    ("seconds", "p95_ms", "underruns", "live"),
    [
        (120, 31.5, 0, True),
        (300, 31.999, 0, True),
        (120, 32.0, 0, False),
        (120, 40.25, 0, False),
        (120, 20.0, 1, False),
        (119, 20.0, 0, False),
    ],
)
def test_live_needs_a_long_fast_run_with_no_underruns(
    seconds: float, p95_ms: float, underruns: int, live: bool
) -> None:
    assert result(seconds=seconds, p95_ms=p95_ms, underruns=underruns).live is live


def test_a_reply_becomes_a_result() -> None:
    parsed = bench.parse_reply("mrt2_base", 120, PASSING_REPLY)
    assert parsed == bench.BenchResult("mrt2_base", 120, 21.5, 31.5, 0, 3000)


@pytest.mark.parametrize(
    "reply",
    [
        {"ok": True, "underruns": 0},
        {"ok": True, "frame_time": {"p50_ms": 1, "p95_ms": 2, "observations": 5}},
        {"ok": True, "frame_time": {"p50_ms": 1, "p95_ms": 2, "observations": 5}, "underruns": -1},
        {
            "ok": True,
            "frame_time": {"p50_ms": 1, "p95_ms": 2, "observations": 5},
            "underruns": True,
        },
        {"ok": True, "frame_time": {"p50_ms": 1, "p95_ms": 2, "observations": 0}, "underruns": 0},
        {"ok": True, "frame_time": {"p50_ms": 1, "p95_ms": "2", "observations": 5}, "underruns": 0},
        {"ok": True, "frame_time": {"p50_ms": 1, "p95_ms": -2, "observations": 5}, "underruns": 0},
        {
            "ok": True,
            "frame_time": {"p50_ms": 1, "p95_ms": 1e999, "observations": 5},
            "underruns": 0,
        },
    ],
)
def test_a_malformed_reply_is_rejected(reply: Command) -> None:
    with pytest.raises(ValueError, match="engine bench reply"):
        bench.parse_reply("mrt2_base", 120, reply)


def test_measure_loads_the_model_then_benchmarks_for_the_requested_seconds() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.sent: list[tuple[Command, float]] = []

        def request(self, command: Command, *, timeout_s: float = 2.0) -> Command:
            self.sent.append((command, timeout_s))
            return PASSING_REPLY if command["cmd"] == "bench" else {"ok": True}

    client = Recorder()
    measured = bench.measure(client, "mrt2_base", Path("/models/resources"), 120)
    assert measured == bench.BenchResult("mrt2_base", 120, 21.5, 31.5, 0, 3000)
    assert client.sent == [
        ({"cmd": "load", "model": "mrt2_base", "resources": "/models/resources"}, 180.0),
        ({"cmd": "bench", "seconds": 120, "latency_probes": 0}, 180.0),
    ]


def test_saved_runs_are_read_back_and_damaged_files_are_ignored(tmp_path: Path) -> None:
    path = bench.save(result(), tmp_path)
    assert path == tmp_path / "mrt2_base.json"
    assert json.loads(path.read_text()) == {
        "model": "mrt2_base",
        "seconds": 120,
        "p50_ms": 21.5,
        "p95_ms": 31.5,
        "underruns": 0,
        "observations": 3000,
    }
    assert bench.load("mrt2_base", tmp_path) == result()
    path.write_text("{")
    assert bench.load("mrt2_base", tmp_path) is None
    assert bench.load("mrt2_small", tmp_path) is None


def test_settings_offer_the_base_model_live_only_after_a_passing_run(tmp_path: Path) -> None:
    unmeasured = bench.base_model_status(tmp_path)
    assert unmeasured.live is False
    assert unmeasured.message == (
        "offline render only on this Mac: not measured yet; "
        "run ghostband bench --model mrt2_base --seconds 120"
    )

    bench.save(result(), tmp_path)
    assert bench.base_model_status(tmp_path).as_state() == {
        "live": True,
        "message": "live: p95 31.5 ms, 0 underruns over 120 s",
    }

    bench.save(result(p95_ms=45.26, underruns=3), tmp_path)
    assert bench.base_model_status(tmp_path).as_state() == {
        "live": False,
        "message": "offline render only on this Mac: p95 45.3 ms, 3 underruns over 120 s",
    }


def test_report_has_the_header_and_one_row_per_measured_model(tmp_path: Path) -> None:
    records = tmp_path / "records"
    bench.save(result(model="mrt2_small", p95_ms=12.5), records)
    bench.save(result(p95_ms=45.26, underruns=3), records)
    report = tmp_path / "engine.md"
    bench.write_report(report, records)
    assert report.read_text() == (
        "# Engine benchmark\n"
        "\n"
        "Frame time and underruns of the real engine, written by `ghostband bench`.\n"
        "Each row is the latest run of that model on the Mac that ran it. A model is\n"
        "offered for live playback only when its run lasted at least 120 s with\n"
        "a 95th-percentile frame time under 32 ms and zero underruns.\n"
        "\n"
        "| Model | Seconds | Frame p50 ms | Frame p95 ms | Underruns | Observations | Live |\n"
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |\n"
        "| mrt2_small | 120 | 21.500 | 12.500 | 0 | 3000 | yes |\n"
        "| mrt2_base | 120 | 21.500 | 45.260 | 3 | 3000 | no |\n"
    )


def engine_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "engine"
    script.write_text(f"#!{sys.executable}\n{body}")
    script.chmod(0o755)
    return script


SERVER = """
import json, socket, sys
server = socket.socket(socket.AF_UNIX)
server.bind(sys.argv[2])
server.listen(1)
connection, _ = server.accept()
for line in connection.makefile():
    command = json.loads(line)
    if command["cmd"] == "quit":
        break
    reply = {{"ok": True, "protocol": 1}}
    if command["cmd"] == "bench":
        reply = {reply!r}
    connection.sendall(json.dumps(reply).encode() + b"\\n")
"""


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    models = tmp_path / "models"
    for entry, contents in ((ALPHA, b"alpha\n"), (BASE, b"base\n")):
        (models / entry.name).parent.mkdir(parents=True, exist_ok=True)
        (models / entry.name).write_bytes(contents)
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))
    monkeypatch.setattr(setup_models, "BASE_MODEL_FILES", (BASE,))
    monkeypatch.setenv("GHOSTBAND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GHOSTBAND_MODEL_DIR", str(models))
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("COLUMNS", "200")
    return tmp_path


def test_bench_command_records_a_passing_run_and_rewrites_the_report(installed: Path) -> None:
    engine_script(installed, SERVER.format(reply=PASSING_REPLY))
    report = installed / "engine.md"
    outcome = CliRunner().invoke(
        main,
        ["bench", "--model", "mrt2_base", "--seconds", "120", "--report", str(report)],
    )
    assert outcome.exit_code == 0, outcome.output + outcome.stderr
    assert "Measured mrt2_base: p95 31.5 ms, 0 underruns over 120 s" in outcome.output
    assert "live: p95 31.5 ms, 0 underruns over 120 s" in outcome.output
    assert bench.load("mrt2_base", installed / "data" / "bench") == result()
    assert report.read_text().endswith("| mrt2_base | 120 | 21.500 | 31.500 | 0 | 3000 | yes |\n")


def test_bench_command_keeps_the_base_model_offline_after_a_slow_run(installed: Path) -> None:
    slow = {**PASSING_REPLY, "frame_time": {"p50_ms": 30, "p95_ms": 41.26, "observations": 3000}}
    engine_script(installed, SERVER.format(reply={**slow, "underruns": 2}))
    report = installed / "engine.md"
    outcome = CliRunner().invoke(main, ["bench", "--model", "mrt2_base", "--report", str(report)])
    assert outcome.exit_code == 0, outcome.output + outcome.stderr
    assert "offline render only on this Mac: p95 41.3 ms, 2 underruns over 120 s" in outcome.output
    assert report.read_text().endswith("| mrt2_base | 120 | 30.000 | 41.260 | 2 | 3000 | no |\n")


def test_bench_command_reports_an_engine_that_exits(installed: Path) -> None:
    engine_script(installed, "import sys\nsys.exit(3)\n")
    outcome = CliRunner().invoke(main, ["bench", "--seconds", "1"])
    assert outcome.exit_code == 8
    assert "ghostband: Engine failed: engine exited with status 3" in outcome.stderr
    assert bench.load("mrt2_small", installed / "data" / "bench") is None
