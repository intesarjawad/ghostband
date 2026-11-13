"""Golden transcripts of the CLI: help, each command's output and each registry error.

After an intended change to the output, rewrite them with
GHOSTBAND_UPDATE_GOLDEN=1 uv run python -m pytest -k cli_render
and review the diff of conductor/tests/golden/cli/.
"""

import asyncio
import fcntl
import os
import shlex
import shutil
import struct
import sys
import termios
import unicodedata
import wave
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from itertools import count
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from click.testing import CliRunner
from ghostband import first_run
from ghostband.bench import BenchResult
from ghostband.chart import Chart
from ghostband.cli import main
from ghostband.engine_client import EngineBusyError
from ghostband.errors import ErrorCode
from ghostband.importing import MAX_FILE_BYTES
from ghostband.render import OfflineEngine, Render

from conductor.tests.engine_stand_in import Mode, executable

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden" / "cli"
UPDATE = os.environ.get("GHOSTBAND_UPDATE_GOLDEN") == "1"
WIDTHS = (80, 120)
HELP_MAX_WIDTH = 80
CHART = "greensleeves.chart.json"
SAMPLE_RATE = 44_100
sf = import_module("soundfile")
# Same length as secrets.token_urlsafe(32), so the served URL wraps as a real one does.
FIXED_SESSION_ID = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
INSTALL_VARIABLES = (
    "GHOSTBAND_ENGINE_SOCKET",
    "GHOSTBAND_RESOURCES",
    "GHOSTBAND_PERMISSIVE_ENV",
    "GHOSTBAND_SHEETSAGE2_CACHE",
)


def nothing(_monkeypatch: pytest.MonkeyPatch) -> None:
    pass


@dataclass(frozen=True)
class Scenario:
    name: str
    args: tuple[str, ...]
    prepare: Callable[[pytest.MonkeyPatch], None] = nothing
    exit_code: int = 0
    stdin: str | None = None


def invalid_chart(_monkeypatch: pytest.MonkeyPatch) -> None:
    Path(CHART).write_text('{"tempo_bpm": -10}', encoding="utf-8")


def copy_chart(_monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copyfile(ROOT / "charts" / CHART, CHART)


def copy_midi_with_steady_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copyfile(ROOT / "fixtures/greensleeves.mid", "greensleeves.mid")
    monkeypatch.setattr("ghostband.cli.monotonic", count(0.0, 0.25).__next__)


def serve_once(monkeypatch: pytest.MonkeyPatch) -> None:
    def token(_bytes: int) -> str:
        return FIXED_SESSION_ID

    def skip_server(*_args: object, **_options: object) -> None:
        pass

    monkeypatch.setattr("ghostband.cli.secrets.token_urlsafe", token)
    monkeypatch.setattr("ghostband.cli.web.run_app", skip_server)


def render_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    copy_chart(monkeypatch)
    Path("engine").touch()

    def render(
        _chart: Chart, _output: Path, _engine: OfflineEngine, *, tempo_factor: float = 1.0
    ) -> Render:
        assert tempo_factor == 1.0
        return Render(1, ())

    monkeypatch.setattr("ghostband.cli.render_chart", render)


def install_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    def install(
        _paths: first_run.Paths, *, sheetsage2: bool = False, base: bool = False
    ) -> first_run.SetupStatus:
        assert base
        assert not sheetsage2
        return first_run.SetupStatus("ready", "ready")

    def configure(_paths: first_run.Paths) -> None:
        pass

    monkeypatch.setattr(first_run, "install", install)
    monkeypatch.setattr(first_run, "configure_environment", configure)


def engine_with_models(monkeypatch: pytest.MonkeyPatch, *, script: str = "") -> None:
    engine = Path("engine")
    engine.write_text(script, encoding="utf-8")
    engine.chmod(0o755)

    def complete(*_args: object, **_options: object) -> list[object]:
        return []

    monkeypatch.setattr("ghostband.cli.setup_models.inspect_models", complete)


def bench_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    engine_with_models(monkeypatch)

    def run(_engine: Path, _resources: Path, model: str, seconds: float) -> BenchResult:
        return BenchResult(model, seconds, p50_ms=10.0, p95_ms=20.0, underruns=0, observations=1)

    monkeypatch.setattr("ghostband.cli.engine_bench.run_engine", run)


def engine_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    engine_with_models(monkeypatch, script="#!/bin/sh\nexit 1\n")
    # An installed engine is always named by an absolute path, so it is not looked up on PATH.
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(Path("engine").resolve()))


def stand_in_engine(monkeypatch: pytest.MonkeyPatch, mode: Mode) -> None:
    copy_chart(monkeypatch)
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(executable(Path.cwd(), mode)))


def engine_never_listens(monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in_engine(monkeypatch, "silent")
    # Each clock reading advances 1 s, so the 10 s budget runs out in ten polls.
    monkeypatch.setattr("ghostband.engine_process.monotonic", count(0.0, 1.0).__next__)


def engine_crashes_mid_song(monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in_engine(monkeypatch, "crash")


def engine_held_by_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    copy_chart(monkeypatch)

    def held(_engine: Path, _socket: Path) -> None:
        raise EngineBusyError(4242, 0)

    # The holder's command line and local time differ per machine; test_engine_process reads them.
    def server(_busy: EngineBusyError) -> str:
        return "python3.12 ghostband (process 4242) has held it since 2026-11-09 21:05"

    monkeypatch.setattr("ghostband.engine_process.connect", held)
    monkeypatch.setattr("ghostband.engine_process.describe_holder", server)


def write_wav(name: str, samples: npt.NDArray[np.float64]) -> None:
    with wave.open(name, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(samples.astype("<i2").tobytes())


def silent_recording(_monkeypatch: pytest.MonkeyPatch) -> None:
    write_wav("silent.wav", np.zeros(12 * SAMPLE_RATE))


def short_recording(_monkeypatch: pytest.MonkeyPatch) -> None:
    time = np.arange(2 * SAMPLE_RATE) / SAMPLE_RATE
    write_wav("short.wav", 8_000 * np.sin(2 * np.pi * 440 * time))


def non_finite_recording(_monkeypatch: pytest.MonkeyPatch) -> None:
    sf.write("broken.wav", np.full(12 * SAMPLE_RATE, np.nan), SAMPLE_RATE, subtype="FLOAT")


def oversized_recording(_monkeypatch: pytest.MonkeyPatch) -> None:
    with Path("huge.wav").open("wb") as sparse:
        sparse.truncate(MAX_FILE_BYTES + 1)


def mislabelled_recording(_monkeypatch: pytest.MonkeyPatch) -> None:
    Path("notes.wav").write_text("not audio\n", encoding="utf-8")


def extractor_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copyfile(ROOT / "fixtures/greensleeves.wav", "greensleeves.wav")
    uv = Path("uv")
    uv.write_text("#!/bin/sh\necho 'chord model weights are missing' >&2\nexit 1\n")
    uv.chmod(0o755)

    def find(_name: str) -> str:
        return str(uv.resolve())

    monkeypatch.setattr("ghostband.extractors.permissive.which", find)


def broken_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = Path("presets")
    folder.mkdir()
    (folder / "late.toml").write_text('name = "Late night"\na = \n', encoding="utf-8")

    def relative_folder() -> Path:
        return folder

    monkeypatch.setattr("ghostband.presets.preset_directory", relative_folder)


HELP = [
    Scenario("help", ("--help",)),
    *(Scenario(f"help-{name}", (name, "--help")) for name in main.commands),
    Scenario("help-eval-live", ("eval", "live", "--help")),
]
OUTPUT = [
    Scenario("serve", ("--fake-engine",), serve_once),
    Scenario("import", ("import", "greensleeves.mid", "--time"), copy_midi_with_steady_clock),
    Scenario(
        "play",
        ("play", CHART, "--seconds", "0.05", "--record", "take.wav", "--fake-engine"),
        copy_chart,
    ),
    Scenario("render", ("render", CHART, "-o", "take.wav"), render_offline),
    Scenario("export-midi", ("export-midi", CHART, "-o", "greensleeves.mid"), copy_chart),
    Scenario("export-ireal", ("export", CHART, "--ireal", "-o", "greensleeves.txt"), copy_chart),
    Scenario("export-bundle", ("export", CHART, "--bundle", "greensleeves"), copy_chart),
    Scenario("schema", ("schema", "-o", "chart.schema.json")),
    Scenario("doctor", ("--fake-engine", "doctor")),
    Scenario("doctor-failing", ("doctor",), exit_code=ErrorCode.ENGINE_OFFLINE),
    Scenario("doctor-skipped-preset", ("--fake-engine", "doctor"), broken_preset),
    Scenario("setup", ("setup", "--with-base", "--yes"), install_quietly),
    Scenario("setup-confirm", ("setup", "--with-base"), install_quietly, stdin="y\n"),
    Scenario("setup-declined", ("setup", "--with-base"), install_quietly, exit_code=1, stdin="n\n"),
    Scenario("setup-no-input", ("setup", "--with-base"), install_quietly, exit_code=1, stdin=""),
    Scenario("bench", ("bench", "--report", "engine.md"), bench_runs),
    Scenario("bench-base", ("bench", "--model", "mrt2_base", "--report", "engine.md"), bench_runs),
    Scenario(
        "eval-live",
        (
            "eval",
            "live",
            "-o",
            "live.md",
            "--claims",
            "claims.json",
            "--sync",
            "sync.md",
            "--extraction",
            "extraction.csv",
            "--bench-dir",
            "bench",
        ),
    ),
]
ERRORS = [
    Scenario(
        "error-invalid-chart",
        ("import", CHART),
        invalid_chart,
        exit_code=ErrorCode.INVALID_CHART,
    ),
    Scenario(
        "error-invalid-input",
        ("play", CHART, "--seconds", "-1"),
        exit_code=ErrorCode.INVALID_INPUT,
    ),
    Scenario(
        "error-engine-offline",
        ("play", CHART, "--seconds", "1"),
        copy_chart,
        exit_code=ErrorCode.ENGINE_OFFLINE,
    ),
    Scenario(
        "error-engine-timeout",
        ("play", CHART, "--seconds", "1"),
        engine_never_listens,
        exit_code=ErrorCode.ENGINE_OFFLINE,
    ),
    Scenario(
        "error-engine-crashed",
        ("play", CHART, "--seconds", "5"),
        engine_crashes_mid_song,
        exit_code=ErrorCode.ENGINE_FAILED,
    ),
    Scenario(
        "error-engine-busy",
        ("play", CHART, "--seconds", "1"),
        engine_held_by_the_server,
        exit_code=ErrorCode.ENGINE_BUSY,
    ),
    Scenario(
        "error-decode-failed",
        ("import", "broken.wav"),
        non_finite_recording,
        exit_code=ErrorCode.DECODE_FAILED,
    ),
    Scenario(
        "error-extract-failed",
        ("import", "greensleeves.wav"),
        extractor_fails,
        exit_code=ErrorCode.EXTRACT_FAILED,
    ),
    Scenario(
        "error-io-failed",
        ("export-midi", "missing.chart.json", "-o", "missing.mid"),
        exit_code=ErrorCode.IO_FAILED,
    ),
    Scenario("error-unavailable", ("bench",), exit_code=ErrorCode.UNAVAILABLE),
    Scenario(
        "error-engine-failed",
        ("bench", "--report", "engine.md"),
        engine_exits,
        exit_code=ErrorCode.ENGINE_FAILED,
    ),
    Scenario(
        "error-silent", ("import", "silent.wav"), silent_recording, exit_code=ErrorCode.SILENT
    ),
    Scenario(
        "error-too-short",
        ("import", "short.wav"),
        short_recording,
        exit_code=ErrorCode.TOO_SHORT,
    ),
    Scenario(
        "error-too-large",
        ("import", "huge.wav"),
        oversized_recording,
        exit_code=ErrorCode.TOO_LARGE,
    ),
    Scenario(
        "error-format-mismatch",
        ("import", "notes.wav"),
        mislabelled_recording,
        exit_code=ErrorCode.FORMAT_MISMATCH,
    ),
]
SCENARIOS = {scenario.name: scenario for scenario in (*HELP, *OUTPUT, *ERRORS)}
# Run as a separate process on real terminals; they need no patched collaborators.
TERMINAL_SCENARIOS = ("help", "help-play", "error-invalid-input", "error-unavailable")


@pytest.fixture
def empty_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every install location at the working directory, as a fresh account sees it."""
    monkeypatch.setenv("GHOSTBAND_DATA_DIR", "data")
    monkeypatch.setenv("GHOSTBAND_MODEL_DIR", "models")
    monkeypatch.setenv("GHOSTBAND_ENGINE", "engine")
    for variable in INSTALL_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    # Relative to each run's directory: short enough for a Unix socket, and never an engine
    # the developer already has running at the default path.
    monkeypatch.setenv("GHOSTBAND_ENGINE_SOCKET", "engine.sock")
    return tmp_path


def transcript(args: tuple[str, ...], stdout: str, stderr: str, exit_code: int) -> str:
    text = f"$ ghostband {shlex.join(args)}\n{stdout}"
    if stderr:
        text += f"[stderr]\n{stderr}"
    return f"{text}[exit {exit_code}]\n"


def assert_fits(output: str, columns: int) -> None:
    assert "\x1b" not in output
    assert all(len(line) <= columns for line in output.splitlines())
    assert not any(unicodedata.category(character) == "So" for character in output)


def assert_golden(name: str, columns: int, actual: str) -> None:
    path = GOLDEN / str(columns) / f"{name}.txt"
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == actual


def run_in_process(
    scenario: Scenario,
    columns: int,
    directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    no_color: bool,
) -> str:
    directory.mkdir()
    monkeypatch.chdir(directory)
    monkeypatch.setenv("HOME", str(directory))
    monkeypatch.setenv("COLUMNS", str(columns))
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    scenario.prepare(monkeypatch)
    result = CliRunner().invoke(
        main,
        scenario.args,
        input=scenario.stdin,
        color=True,
        prog_name="ghostband",
        # CliRunner forces help to 80 columns; this is the width click picks on a real terminal,
        # which the terminal tests below confirm.
        terminal_width=min(columns, HELP_MAX_WIDTH) - 2,
    )
    assert result.exit_code == scenario.exit_code, result.output
    assert_fits(result.output, columns)
    return transcript(scenario.args, result.stdout, result.stderr, result.exit_code)


@pytest.mark.usefixtures("empty_install")
@pytest.mark.parametrize("columns", WIDTHS)
@pytest.mark.parametrize("name", SCENARIOS)
def test_cli_render_matches_golden(
    name: str, columns: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = SCENARIOS[name]
    piped = run_in_process(scenario, columns, tmp_path / "piped", monkeypatch, no_color=False)
    plain = run_in_process(scenario, columns, tmp_path / "plain", monkeypatch, no_color=True)
    assert plain == piped
    assert_golden(name, columns, piped)


def test_cli_render_covers_every_registry_error() -> None:
    assert {scenario.exit_code for scenario in ERRORS} == set(ErrorCode)


def read_terminal(leader: int) -> str:
    data = bytearray()
    while True:
        try:
            chunk = os.read(leader, 4096)
        except OSError:
            # The leader reports EIO once the process has closed its end and the data is read.
            break
        if not chunk:
            break
        data.extend(chunk)
    return data.decode().replace("\r\n", "\n")


def run_on_terminals(
    args: tuple[str, ...], columns: int, *, no_color: bool
) -> tuple[int, str, str]:
    """Run the CLI with stdout and stderr each on its own terminal of the given width."""

    async def exercise() -> tuple[int, str, str]:
        terminals = [os.openpty() for _ in range(2)]
        for _, follower in terminals:
            fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 24, columns, 0, 0))
        environment = {**os.environ, "COLUMNS": str(columns)}
        if no_color:
            environment["NO_COLOR"] = "1"
        else:
            environment.pop("NO_COLOR", None)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "from ghostband.cli import main; main(prog_name='ghostband')",
                *args,
                stdout=terminals[0][1],
                stderr=terminals[1][1],
                env=environment,
            )
        finally:
            for _, follower in terminals:
                os.close(follower)
        try:
            # Read while the process runs: a terminal buffers too little to hold a help page.
            stdout, stderr = await asyncio.gather(
                *(asyncio.to_thread(read_terminal, leader) for leader, _ in terminals)
            )
            return await process.wait(), stdout, stderr
        finally:
            for leader, _ in terminals:
                os.close(leader)

    return asyncio.run(exercise())


@pytest.mark.parametrize("no_color", [False, True])
@pytest.mark.parametrize("columns", WIDTHS)
@pytest.mark.parametrize("name", TERMINAL_SCENARIOS)
def test_cli_render_on_terminal_matches_golden(
    name: str, columns: int, no_color: bool, empty_install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(empty_install)
    monkeypatch.setenv("HOME", str(empty_install))
    scenario = SCENARIOS[name]
    code, stdout, stderr = run_on_terminals(scenario.args, columns, no_color=no_color)
    assert code == scenario.exit_code
    assert_fits(stdout + stderr, columns)
    expected = (GOLDEN / str(columns) / f"{name}.txt").read_text(encoding="utf-8")
    assert transcript(scenario.args, stdout, stderr, code) == expected
