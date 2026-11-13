from dataclasses import replace
from pathlib import Path

import pytest
from ghostband import doctor, first_run, setup_models
from ghostband.setup_models import ModelFile

ALPHA = ModelFile(
    "resources/alpha.txt", 6, "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060"
)


def paths_for(tmp_path: Path) -> first_run.Paths:
    return first_run.Paths(
        data=tmp_path / "data",
        models=tmp_path / "models",
        engine=tmp_path / "engine" / "ghostband-engine",
        permissive=tmp_path / "permissive",
        sheetsage2=tmp_path / "models" / "sheetsage2",
        sheetsage2_env=tmp_path / "sheetsage2-env",
    )


def find_afconvert(_name: str) -> str | None:
    return "/usr/bin/afconvert"


def find_nothing(_name: str) -> str | None:
    return None


def green_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> first_run.Paths:
    paths = paths_for(tmp_path)
    paths.engine.parent.mkdir(parents=True)
    paths.engine.write_bytes(b"engine")
    assert not first_run.installed(paths)
    paths.engine.with_name("mlx.metallib").write_bytes(b"metal")
    (paths.models / ALPHA.name).parent.mkdir(parents=True)
    (paths.models / ALPHA.name).write_bytes(b"alpha\n")
    (paths.permissive / ".venv").mkdir(parents=True)
    (paths.permissive / ".venv" / "pyvenv.cfg").write_text("home = test\n")
    monkeypatch.setattr(doctor, "which", find_afconvert)
    return paths


def test_a_complete_install_is_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = green_paths(tmp_path, monkeypatch)
    problems, summary = doctor.check(paths, fake_engine=False, files=(ALPHA,))
    assert problems == []
    assert summary == (
        f"engine {paths.engine} (installed by ghostband setup); "
        "audio decoder, model files and permissive extractor found"
    )


def test_a_deleted_model_file_is_named_with_the_restoring_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = green_paths(tmp_path, monkeypatch)
    (paths.models / ALPHA.name).unlink()
    problems, _ = doctor.check(paths, fake_engine=False, files=(ALPHA,))
    assert problems == [
        f"{paths.models / ALPHA.name}: missing; Run ghostband setup to restore the model files"
    ]


def test_an_altered_model_file_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = green_paths(tmp_path, monkeypatch)
    (paths.models / ALPHA.name).write_bytes(b"xxxxx\n")
    problems, _ = doctor.check(paths, fake_engine=False, files=(ALPHA,))
    assert problems == [
        f"{paths.models / ALPHA.name}: sha256 does not match the pinned revision; "
        "Run ghostband setup to restore the model files"
    ]


def test_a_missing_metal_library_is_named_with_the_restoring_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = green_paths(tmp_path, monkeypatch)
    library = paths.engine.with_name("mlx.metallib")
    library.unlink()
    problems, _ = doctor.check(paths, fake_engine=False, files=(ALPHA,))
    assert problems == [f"engine library {library} is missing; run ghostband setup"]


def test_engine_decoder_and_environment_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = green_paths(tmp_path, monkeypatch)
    paths.engine.unlink()
    monkeypatch.setattr(doctor, "which", find_nothing)
    (paths.permissive / ".venv" / "pyvenv.cfg").unlink()
    problems, _ = doctor.check(paths, fake_engine=False, files=(ALPHA,))
    assert f"engine executable {paths.engine} is missing; run ghostband setup" in problems
    assert "afconvert is missing" in problems
    assert any("is not synced" in problem for problem in problems)


def test_the_summary_names_where_the_engine_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = replace(green_paths(tmp_path, monkeypatch), engine_source="$GHOSTBAND_ENGINE")
    _, summary = doctor.check(paths, fake_engine=False, files=(ALPHA,))
    assert summary.startswith(f"engine {paths.engine} ($GHOSTBAND_ENGINE); ")


def test_fake_engine_needs_no_models_or_audio(tmp_path: Path) -> None:
    problems, summary = doctor.check(paths_for(tmp_path), fake_engine=True, files=(ALPHA,))
    assert problems == []
    assert summary == "fake engine selected"


def test_preset_problems_are_exposed_for_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "Library/Application Support/ghostband/presets"
    folder.mkdir(parents=True)
    (folder / "broken.toml").write_text('name = "N"\na = \n')
    problems = doctor.preset_problems()
    assert [problem.path for problem in problems] == [folder / "broken.toml"]
    assert "line 2" in str(problems[0])


def test_installed_requires_the_engine_and_every_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))
    assert not first_run.installed(paths)
    paths.engine.parent.mkdir(parents=True)
    paths.engine.write_bytes(b"engine")
    assert not first_run.installed(paths)
    paths.engine.with_name("mlx.metallib").write_bytes(b"metal")
    (paths.models / ALPHA.name).parent.mkdir(parents=True)
    (paths.models / ALPHA.name).write_bytes(b"alpha\n")
    assert first_run.installed(paths)
