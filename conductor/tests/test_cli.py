import json
import wave
from importlib.metadata import version
from pathlib import Path

import pytest
from click.testing import CliRunner
from ghostband import doctor as doctor_checks
from ghostband import first_run
from ghostband.chart import Chart
from ghostband.cli import main
from ghostband.evaluation import live as live_report
from ghostband.presets import PRESETS, Preset, PresetProblem
from ghostband.render import OfflineEngine, Render

ROOT = Path(__file__).resolve().parents[2]


def test_cli_import_play_record_and_export(tmp_path: Path) -> None:
    runner = CliRunner()
    source = ROOT / "fixtures/greensleeves.mid"
    chart_path = tmp_path / "tune.chart.json"
    imported = runner.invoke(main, ["import", str(source), "-o", str(chart_path), "--time"])
    assert imported.exit_code == 0, imported.output
    chart = json.loads(chart_path.read_text())
    assert chart["rights"] == "unknown"
    assert chart["extractor"] == "midi"
    assert "Import complete 100%" in imported.output
    recording = tmp_path / "take.wav"
    played = runner.invoke(
        main,
        [
            "play",
            str(chart_path),
            "--preset",
            "0",
            "--seconds",
            "0.1",
            "--record",
            str(recording),
            "--fake-engine",
        ],
    )
    assert played.exit_code == 0, played.output
    with wave.open(str(recording)) as audio:
        assert audio.getnchannels() == 2
        assert audio.getframerate() == 48000
        assert audio.getnframes() >= 4800
    take = json.loads((tmp_path / "take.take.json").read_text())
    assert take["chart"] == str(chart_path)
    assert take["preset"] == "Warm jazz / chiptune"
    assert take["recording"] == "take.wav"
    assert take["run"] == 1
    assert take["play_time_s"] > 0
    assert len(take["beat_audible"]) >= 2
    assert take["beat_audible"][0][0] == 0
    midi = tmp_path / "out.mid"
    exported = runner.invoke(main, ["export-midi", str(chart_path), "-o", str(midi)])
    assert exported.exit_code == 0, exported.output
    assert midi.read_bytes().startswith(b"MThd")


def test_cli_play_writes_the_measurement_sidecar_fields(tmp_path: Path) -> None:
    recording = tmp_path / "take.wav"
    result = CliRunner().invoke(
        main,
        [
            "play",
            str(ROOT / "charts/greensleeves.chart.json"),
            "--seconds",
            "0.05",
            "--record",
            str(recording),
            "--take-run",
            "2",
            "--take-condition",
            "chords",
            "--take-slider",
            "1",
            "--take-tempo-factor",
            "1.3",
            "--take-onset-mode",
            "1",
            "--fake-engine",
        ],
    )
    assert result.exit_code == 0, result.output
    sidecar = json.loads((tmp_path / "take.take.json").read_text())
    assert sidecar["run"] == 2
    assert sidecar["condition"] == "chords"
    assert sidecar["slider"] == 1
    assert sidecar["tempo_factor"] == 1.3
    assert sidecar["onset_mode"] == 1


def test_cli_tempo_take_plays_a_factor_outside_the_measured_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "claims.json").write_text(
        json.dumps({"tempo_factors_pass": [], "tempo_range": None})
    )
    monkeypatch.setattr(live_report, "default_directory", lambda: tmp_path)
    arguments = ["play", str(ROOT / "charts/greensleeves.chart.json"), "--seconds", "0.05"]
    recording = ["--record", str(tmp_path / "take.wav"), "--fake-engine"]

    take = CliRunner().invoke(main, [*arguments, "--take-tempo-factor", "0.5", *recording])

    assert take.exit_code == 0, take.output
    assert json.loads((tmp_path / "take.take.json").read_text())["tempo_factor"] == 0.5


def test_cli_render_uses_the_selected_model_and_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakePaths:
        data = tmp_path / "data"
        models = tmp_path / "models"
        resources = tmp_path / "models/resources"
        engine_binary = tmp_path / "engine"
        permissive = tmp_path / "permissive"
        sheetsage2 = tmp_path / "sheetsage2"

    FakePaths.engine_binary.write_text("")

    def fake_presets() -> tuple[tuple[Preset, ...], list[PresetProblem]]:
        return (PRESETS, [])

    monkeypatch.setattr(first_run.Paths, "defaults", FakePaths)
    monkeypatch.setattr("ghostband.cli.load_presets", fake_presets)
    calls: list[tuple[Chart, Path, OfflineEngine, float]] = []

    def fake_render(
        chart: Chart,
        output: Path,
        engine: OfflineEngine,
        *,
        tempo_factor: float = 1.0,
    ) -> Render:
        calls.append((chart, output, engine, tempo_factor))
        return Render(1, ())

    monkeypatch.setattr("ghostband.cli.render_chart", fake_render)
    output = tmp_path / "take.wav"
    result = CliRunner().invoke(
        main,
        [
            "render",
            str(ROOT / "charts/greensleeves.chart.json"),
            "--model",
            "mrt2_base",
            "--preset",
            "1",
            "--output",
            str(output),
            "--tempo",
            "0.9",
        ],
    )
    assert result.exit_code == 0, result.output
    chart, destination, engine, tempo = calls[0]
    assert destination == output
    assert tempo == 0.9
    assert engine.model == "mrt2_base"
    assert engine.preset == PRESETS[1].name
    assert engine.prompt == PRESETS[1].a
    assert engine.prompt_b == PRESETS[1].b
    assert engine.guidance == PRESETS[1].guidance
    assert chart.title == "Greensleeves"


def test_cli_engine_missing_is_actionable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Relative, so the socket path stays inside the Unix limit.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GHOSTBAND_ENGINE_SOCKET", "engine.sock")
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(tmp_path / "ghostband-engine"))
    result = CliRunner().invoke(
        main, ["play", str(ROOT / "charts/greensleeves.chart.json"), "--seconds", "1"]
    )
    assert result.exit_code == 3
    assert "ghostband: Engine offline: engine executable" in result.stderr
    assert "next: Run ghostband setup, then ghostband doctor" in result.stderr
    assert "Traceback" not in result.output


def test_doctor_prints_skipped_presets_with_their_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "Library/Application Support/ghostband/presets"
    folder.mkdir(parents=True)
    broken = folder / "broken.toml"
    broken.write_text('name = "N"\na = \n', encoding="utf-8")
    missing = folder / "missing.toml"
    missing.write_text('a = "x"\nb = "y"\n', encoding="utf-8")
    result = CliRunner().invoke(main, ["--fake-engine", "doctor"])
    assert result.exit_code == 0, result.output
    # Long paths wrap onto their own lines, so compare the words.
    flat = " ".join(result.output.split())
    assert str(broken) in flat
    assert "line 2" in flat
    assert str(missing) in flat
    assert "name" in flat
    assert "fake engine selected" in flat


def test_cli_play_accepts_a_user_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "Library/Application Support/ghostband/presets"
    folder.mkdir(parents=True)
    (folder / "late.toml").write_text(
        'name = "Late night"\na = "ambient piano"\nb = "drum and bass"\n', encoding="utf-8"
    )
    result = CliRunner().invoke(
        main,
        [
            "play",
            str(ROOT / "charts/greensleeves.chart.json"),
            "--preset",
            "4",
            "--seconds",
            "0.05",
            "--record",
            str(tmp_path / "take.wav"),
            "--fake-engine",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Playing" in result.output


@pytest.fixture
def empty_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHOSTBAND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GHOSTBAND_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(tmp_path / "engine"))


@pytest.mark.usefixtures("empty_install")
def test_cli_unavailable_commands() -> None:
    result = CliRunner().invoke(main, ["bench"])
    assert result.exit_code == 7
    assert "ghostband: Unavailable:" in result.stderr
    assert "next: Run ghostband doctor" in result.stderr


@pytest.mark.usefixtures("empty_install")
def test_bench_of_the_base_model_names_the_setup_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    result = CliRunner().invoke(main, ["bench", "--model", "mrt2_base"])
    assert result.exit_code == 7
    assert "run ghostband setup --with-base" in result.stderr


def test_bench_rejects_a_nonpositive_duration_and_an_unknown_model() -> None:
    runner = CliRunner()
    for arguments in (["--seconds", "0"], ["--model", "mrt2_large"]):
        result = runner.invoke(main, ["bench", *arguments])
        assert result.exit_code == 2
        assert "ghostband: Invalid input:" in result.stderr


def test_setup_installs_with_the_requested_optional_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[first_run.Paths, bool, bool]] = []

    def install(
        paths: first_run.Paths, *, sheetsage2: bool = False, base: bool = False
    ) -> first_run.SetupStatus:
        calls.append((paths, sheetsage2, base))
        return first_run.SetupStatus("ready", "ready")

    def ignore(_paths: first_run.Paths) -> None:
        pass

    monkeypatch.setattr(first_run, "install", install)
    monkeypatch.setattr(first_run, "configure_environment", ignore)
    result = CliRunner().invoke(main, ["setup", "--sheetsage2", "--with-base", "--yes"])
    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0][1:] == (True, True)
    assert "ghostband: Setup complete in" in result.output


def test_setup_asks_before_the_large_download_unless_told_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def install(
        _paths: first_run.Paths, *, base: bool = False, **_options: object
    ) -> first_run.SetupStatus:
        calls.append(base)
        return first_run.SetupStatus("ready", "ready")

    def ignore(_paths: first_run.Paths) -> None:
        pass

    monkeypatch.setattr(first_run, "install", install)
    monkeypatch.setattr(first_run, "configure_environment", ignore)
    declined = CliRunner().invoke(main, ["setup", "--with-base"], input="n\n")
    assert declined.exit_code != 0
    assert calls == []
    accepted = CliRunner().invoke(main, ["setup", "--with-base"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert calls == [True]


def test_doctor_names_a_missing_model_and_its_restoring_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = "models/resources/alpha.txt: missing; Run ghostband setup to restore the model files"

    def check(_paths: first_run.Paths, *, fake_engine: bool) -> tuple[list[str], str]:
        assert not fake_engine
        return [problem], ""

    monkeypatch.setattr(doctor_checks, "check", check)
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 3
    flat = " ".join(result.stderr.split())
    assert problem in flat
    assert "ghostband setup" in flat


def test_cli_schema_is_json(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["schema"])
    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    assert schema["properties"]["schema_version"]["const"] == 1
    path = tmp_path / "schema.json"
    written = runner.invoke(main, ["schema", "-o", str(path)])
    assert written.exit_code == 0
    assert json.loads(path.read_text()) == schema


def test_cli_invalid_options_use_registry() -> None:
    result = CliRunner().invoke(main, ["play", "chart.json", "--seconds", "-1"])
    assert result.exit_code == 2
    assert "ghostband: Invalid input:" in result.stderr
    assert "next: Run ghostband --help." in result.stderr


def test_version_prints_the_package_version_without_touching_the_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(first_run.Paths, "defaults", fail_defaults)
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output == f"ghostband, version {version('ghostband')}\n"


def fail_defaults() -> first_run.Paths:
    raise AssertionError("--version must not resolve install paths")


def test_doctor_fake_does_not_connect_to_audio() -> None:
    result = CliRunner().invoke(main, ["--fake-engine", "doctor"])
    assert result.exit_code == 0, result.output
    assert "fake engine selected" in result.output
