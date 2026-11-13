import io
import json
import os
import tarfile
from importlib.metadata import version
from pathlib import Path

import pytest
from ghostband import first_run, setup_models
from ghostband.setup_models import ModelFile

ALPHA = ModelFile(
    "resources/alpha.txt", 6, "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060"
)
ENGINE_BYTES = b"engine"
CONTENTS = {ALPHA.url: b"alpha\n"}


def paths_for(tmp_path: Path) -> first_run.Paths:
    return first_run.Paths(
        data=tmp_path / "data",
        models=tmp_path / "models",
        engine=tmp_path / "engine" / "ghostband-engine",
        permissive=tmp_path / "permissive",
        sheetsage2=tmp_path / "models" / "sheetsage2",
        sheetsage2_env=tmp_path / "sheetsage2-env",
    )


def archive_bytes() -> bytes:
    source = io.BytesIO(ENGINE_BYTES)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo("bin/ghostband-engine")
        info.size = len(ENGINE_BYTES)
        bundle.addfile(info, source)
        metal = tarfile.TarInfo("bin/mlx.metallib")
        metal.size = len(ENGINE_BYTES)
        bundle.addfile(metal, io.BytesIO(ENGINE_BYTES))
    return buffer.getvalue()


def fake_download(url: str, destination: Path) -> None:
    if url == setup_models.engine_archive_url():
        destination.write_bytes(archive_bytes())
    else:
        destination.write_bytes(CONTENTS[url])


def fake_sync(project: Path) -> None:
    environment = project / ".venv"
    environment.mkdir(parents=True, exist_ok=True)
    (environment / "pyvenv.cfg").write_text("home = test\n")


def project_source(tmp_path: Path, name: str) -> Path:
    source = tmp_path / f"{name}-source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (source / "run.py").write_text("")
    return source


def test_default_paths_follow_home_and_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(first_run, "ROOT", tmp_path / "checkout")
    for name in (
        "GHOSTBAND_DATA_DIR",
        "GHOSTBAND_MODEL_DIR",
        "GHOSTBAND_ENGINE",
        "GHOSTBAND_PERMISSIVE_ENV",
        "GHOSTBAND_SHEETSAGE2_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)
    paths = first_run.Paths.defaults()
    assert paths.data == tmp_path / "Library/Application Support/ghostband"
    assert paths.models == paths.data / "models"
    assert paths.engine == paths.data / "engine" / version("ghostband") / "ghostband-engine"
    assert paths.engine_source == "installed by ghostband setup"
    assert paths.permissive == paths.data / "permissive"
    assert paths.sheetsage2 == paths.data / "models" / "sheetsage2"
    assert paths.resources == paths.data / "models" / "resources"

    monkeypatch.setenv("GHOSTBAND_MODEL_DIR", str(tmp_path / "elsewhere"))
    assert first_run.Paths.defaults().models == tmp_path / "elsewhere"


def engine_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"engine")
    return path


def test_the_engine_comes_from_the_environment_before_anything_else(tmp_path: Path) -> None:
    data, checkout = tmp_path / "data", tmp_path / "checkout"
    engine_file(data / "engine" / "1.2.3" / "ghostband-engine")
    engine_file(checkout / "engine" / "build" / "ghostband-engine")
    choice = first_run.resolve_engine(
        data, "1.2.3", configured=str(tmp_path / "elsewhere"), checkout=checkout
    )
    assert choice == first_run.EngineChoice(tmp_path / "elsewhere", "$GHOSTBAND_ENGINE")


def test_the_installed_engine_for_this_version_beats_a_checkout_build(tmp_path: Path) -> None:
    data, checkout = tmp_path / "data", tmp_path / "checkout"
    installed = engine_file(data / "engine" / "1.2.3" / "ghostband-engine")
    engine_file(checkout / "engine" / "build" / "ghostband-engine")
    choice = first_run.resolve_engine(data, "1.2.3", configured=None, checkout=checkout)
    assert choice == first_run.EngineChoice(installed, "installed by ghostband setup")


def test_an_engine_installed_for_another_version_is_not_used(tmp_path: Path) -> None:
    data, checkout = tmp_path / "data", tmp_path / "checkout"
    engine_file(data / "engine" / "1.2.2" / "ghostband-engine")
    built = engine_file(checkout / "engine" / "build" / "ghostband-engine")
    choice = first_run.resolve_engine(data, "1.2.3", configured=None, checkout=checkout)
    assert choice == first_run.EngineChoice(built, "source checkout")


def test_without_any_engine_setup_will_write_the_versioned_location(tmp_path: Path) -> None:
    choice = first_run.resolve_engine(
        tmp_path / "data", "1.2.3", configured="", checkout=tmp_path / "checkout"
    )
    assert choice == first_run.EngineChoice(
        tmp_path / "data" / "engine" / "1.2.3" / "ghostband-engine", "installed by ghostband setup"
    )


def test_configuring_environment_keeps_explicit_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    for name in (
        "GHOSTBAND_MODEL_DIR",
        "GHOSTBAND_RESOURCES",
        "GHOSTBAND_PERMISSIVE_ENV",
        "GHOSTBAND_SHEETSAGE2_CACHE",
        "GHOSTBAND_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GHOSTBAND_ENGINE", "/custom/engine")
    first_run.configure_environment(paths)
    assert os.environ["GHOSTBAND_MODEL_DIR"] == str(paths.models)
    assert os.environ["GHOSTBAND_RESOURCES"] == str(paths.resources)
    assert os.environ["GHOSTBAND_ENGINE"] == "/custom/engine"


def test_install_puts_the_engine_models_and_permissive_environment_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    source = project_source(tmp_path, "permissive")
    monkeypatch.setattr(first_run, "PERMISSIVE_SOURCE", source)
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))

    status = first_run.install(paths, download=fake_download, sync=fake_sync)

    assert status.state == "ready"
    assert paths.engine.read_bytes() == ENGINE_BYTES
    assert paths.engine.stat().st_mode & 0o111
    assert (paths.models / ALPHA.name).read_bytes() == b"alpha\n"
    assert (paths.permissive / "pyproject.toml").is_file()
    assert (paths.permissive / ".venv" / "pyvenv.cfg").is_file()
    assert not paths.sheetsage2.exists()
    assert not paths.sheetsage2_env.exists()
    assert first_run.installed(paths)


def test_install_writes_a_progress_file_the_interface_can_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    monkeypatch.setattr(first_run, "PERMISSIVE_SOURCE", project_source(tmp_path, "permissive"))
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))

    first_run.install(paths, download=fake_download, sync=fake_sync)

    data = json.loads(paths.status.read_text())
    assert data == {
        "state": "ready",
        "stage": "ready",
        "done_bytes": 0,
        "total_bytes": 0,
        "detail": "",
    }
    assert first_run.read_status(paths.status) == first_run.SetupStatus("ready", "ready")


def test_permissive_extraction_is_ready_before_weight_downloads_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    monkeypatch.setattr(first_run, "PERMISSIVE_SOURCE", project_source(tmp_path, "permissive"))
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))
    events: list[str] = []

    def sync(project: Path) -> None:
        events.append("sync")
        environment = project / ".venv"
        environment.mkdir(parents=True)
        (environment / "pyvenv.cfg").write_text("home = test\n")

    def download(url: str, destination: Path) -> None:
        events.append("download")
        fake_download(url, destination)

    first_run.install(paths, download=download, sync=sync)

    assert events[0] == "sync"
    assert events.count("sync") == 1
    assert events.count("download") == 2


def test_the_base_model_is_downloaded_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = ModelFile(
        "models/mrt2_base/base.txt",
        5,
        "f34848ca92665c342abd5816c9e3eda0e82180671195362bcd0080544a3bc2ac",
    )
    monkeypatch.setattr(first_run, "PERMISSIVE_SOURCE", project_source(tmp_path, "permissive"))
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))
    monkeypatch.setattr(setup_models, "BASE_MODEL_FILES", (base,))
    contents = {**CONTENTS, base.url: b"base\n"}

    def download(url: str, destination: Path) -> None:
        if url == setup_models.engine_archive_url():
            destination.write_bytes(archive_bytes())
        else:
            destination.write_bytes(contents[url])

    without = paths_for(tmp_path / "without")
    first_run.install(without, download=download, sync=fake_sync)
    assert not (without.models / base.name).exists()

    with_base = paths_for(tmp_path / "with")
    first_run.install(with_base, base=True, download=download, sync=fake_sync)
    assert (with_base.models / base.name).read_bytes() == b"base\n"
    assert not setup_models.inspect_models(with_base.models, (ALPHA, base))


def test_sheetsage2_is_added_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    monkeypatch.setattr(first_run, "PERMISSIVE_SOURCE", project_source(tmp_path, "permissive"))
    monkeypatch.setattr(first_run, "SHEETSAGE2_SOURCE", project_source(tmp_path, "sheetsage2"))
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))
    fetched: list[tuple[Path, Path]] = []

    def fetch(project: Path, cache: Path) -> None:
        fetched.append((project, cache))
        cache.mkdir(parents=True)
        (cache / "mert.safetensors").write_bytes(b"weights")

    status = first_run.install(
        paths, sheetsage2=True, download=fake_download, sync=fake_sync, fetch=fetch
    )

    assert status == first_run.SetupStatus("ready", "ready")
    assert fetched == [(paths.sheetsage2_env, paths.sheetsage2)]
    assert (paths.sheetsage2 / "mert.safetensors").is_file()
    assert (paths.sheetsage2_env / "pyproject.toml").is_file()


def test_a_failed_install_leaves_the_reason_in_the_status_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    monkeypatch.setattr(first_run, "PERMISSIVE_SOURCE", project_source(tmp_path, "permissive"))
    monkeypatch.setattr(setup_models, "MODEL_FILES", (ALPHA,))

    def broken_download(url: str, _destination: Path) -> None:
        raise OSError(f"cannot reach {url}")

    with pytest.raises(OSError, match="cannot reach"):
        first_run.install(paths, download=broken_download, sync=fake_sync)

    status = first_run.read_status(paths.status)
    assert status.state == "failed"
    assert "cannot reach" in status.detail


def test_copying_an_environment_keeps_a_synced_virtualenv(
    tmp_path: Path,
) -> None:
    source = project_source(tmp_path, "permissive")
    destination = tmp_path / "installed"
    (destination / ".venv").mkdir(parents=True)
    (destination / ".venv" / "pyvenv.cfg").write_text("home = existing\n")

    first_run.copy_environment(source, destination)

    assert (destination / "pyproject.toml").is_file()
    assert (destination / ".venv" / "pyvenv.cfg").read_text() == "home = existing\n"
