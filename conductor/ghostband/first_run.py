"""Install the engine, models and extraction environment for a first run."""

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path
from shutil import copytree, ignore_patterns, which
from threading import Lock, Thread

from ghostband import setup_models
from ghostband.api import SetupEvent

ROOT = Path(__file__).resolve().parents[2]
PERMISSIVE_SOURCE = files("ghostband").joinpath("extract_envs", "permissive")
SHEETSAGE2_SOURCE = files("ghostband").joinpath("extract_envs", "sheetsage2")
STATUS_NAME = "first-run.json"
UV_TIMEOUT_S = 900
ENGINE_FROM_ENVIRONMENT = "$GHOSTBAND_ENGINE"
ENGINE_FROM_SETUP = "installed by ghostband setup"
ENGINE_FROM_CHECKOUT = "source checkout"

Sync = Callable[[Path], None]
Fetch = Callable[[Path, Path], None]


@dataclass(frozen=True)
class EngineChoice:
    """The engine executable the conductor will start, and why this one."""

    path: Path
    source: str


def resolve_engine(
    data: Path, release: str, *, configured: str | None, checkout: Path | None = None
) -> EngineChoice:
    """Pick the engine: the override, the versioned install, then a checkout's build.

    When neither file exists yet the versioned install location is returned, because that is
    where ghostband setup will write it.
    """
    if configured:
        return EngineChoice(Path(configured), ENGINE_FROM_ENVIRONMENT)
    installed_engine = data / "engine" / release / setup_models.ENGINE_NAME
    built = (checkout or ROOT) / "engine" / "build" / setup_models.ENGINE_NAME
    if not installed_engine.is_file() and built.is_file():
        return EngineChoice(built, ENGINE_FROM_CHECKOUT)
    return EngineChoice(installed_engine, ENGINE_FROM_SETUP)


@dataclass(frozen=True)
class Paths:
    """Every location a first run writes, resolved from the user's environment."""

    data: Path
    models: Path
    engine: Path
    permissive: Path
    sheetsage2: Path
    sheetsage2_env: Path
    engine_source: str = ENGINE_FROM_SETUP

    @property
    def engine_binary(self) -> Path:
        return self.engine

    @property
    def status(self) -> Path:
        return self.data / STATUS_NAME

    @property
    def resources(self) -> Path:
        return self.models / "resources"

    @classmethod
    def defaults(cls) -> "Paths":
        data = setup_models.app_directory()
        engine = resolve_engine(
            data, version("ghostband"), configured=os.environ.get("GHOSTBAND_ENGINE")
        )
        permissive = os.environ.get("GHOSTBAND_PERMISSIVE_ENV")
        sheetsage2 = os.environ.get("GHOSTBAND_SHEETSAGE2_CACHE")
        return cls(
            data=data,
            models=setup_models.model_directory(),
            engine=engine.path,
            permissive=Path(permissive) if permissive else data / "permissive",
            sheetsage2=Path(sheetsage2) if sheetsage2 else data / "models" / "sheetsage2",
            sheetsage2_env=data / "sheetsage2-env",
            engine_source=engine.source,
        )


@dataclass(frozen=True)
class SetupStatus:
    """The progress file the interface reads while a first run installs."""

    state: str
    stage: str
    done_bytes: int = 0
    total_bytes: int = 0
    detail: str = ""

    def event(self) -> SetupEvent:
        return {
            "event": "setup_status",
            "state": self.state,
            "stage": self.stage,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "detail": self.detail,
            "retryable": self.state in {"failed", "not_started"},
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        temporary.write_text(json.dumps(self.__dict__, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


_STATUS_LOCK = Lock()


def configure_environment(paths: Paths) -> None:
    """Point the conductor at installed assets without overriding explicit settings."""
    os.environ.setdefault("GHOSTBAND_DATA_DIR", str(paths.data))
    os.environ.setdefault("GHOSTBAND_MODEL_DIR", str(paths.models))
    os.environ.setdefault("GHOSTBAND_RESOURCES", str(paths.resources))
    # Keep a checkout's already-synced extractor until setup installs one here.
    if paths.permissive.is_dir() or not (ROOT / "extract/permissive/.venv").is_dir():
        os.environ.setdefault("GHOSTBAND_PERMISSIVE_ENV", str(paths.permissive))
    repository_sheetsage2 = ROOT / "models/sheetsage2"
    if paths.sheetsage2.is_dir() or not repository_sheetsage2.is_dir():
        os.environ.setdefault("GHOSTBAND_SHEETSAGE2_CACHE", str(paths.sheetsage2))


def installed(paths: Paths) -> bool:
    """Whether the live band has an engine and every pinned model file."""
    return setup_models.engine_installed(paths.engine_binary) and not setup_models.inspect_models(
        paths.models, verify_hashes=False
    )


def _write(path: Path, status: SetupStatus) -> None:
    with _STATUS_LOCK:
        status.write(path)


def copy_environment(source: Traversable, destination: Path) -> None:
    """Copy a pinned extraction project while keeping its synced virtual environment."""
    if not source.is_dir():
        if not (destination / "pyproject.toml").is_file():
            raise FileNotFoundError(
                f"{source} is not available; reinstall ghostband before running setup"
            )
        return
    with as_file(source) as directory:
        copytree(
            directory,
            destination,
            dirs_exist_ok=True,
            ignore=ignore_patterns(".venv", "__pycache__", "*.pyc"),
        )


async def _uv(project: Path, *arguments: str) -> None:
    executable = which("uv")
    if executable is None:
        raise FileNotFoundError("Install uv before running ghostband setup")
    process = await asyncio.create_subprocess_exec(
        executable,
        "--project",
        str(project),
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "UV_NO_PROGRESS": "1"},
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=UV_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "uv failed")


def sync_project(project: Path) -> None:
    """Resolve one locked extraction environment, without installing its dev group."""
    asyncio.run(_uv(project, "sync", "--frozen", "--no-dev"))


def fetch_sheetsage2(project: Path, cache: Path) -> None:
    """Fetch non-commercial SheetSage2 weights only after explicit acceptance."""
    asyncio.run(
        _uv(
            project,
            "run",
            "--frozen",
            "python",
            str(project / "run.py"),
            "fetch",
            "--cache-dir",
            str(cache),
            "--accept-noncommercial-license",
        )
    )


def install_permissive(paths: Paths, *, sync: Sync = sync_project) -> Path:
    """Make permissive extraction usable before the slower model download starts."""
    copy_environment(PERMISSIVE_SOURCE, paths.permissive)
    sync(paths.permissive)
    return paths.permissive


def install_sheetsage2(
    paths: Paths,
    *,
    sync: Sync = sync_project,
    fetch: Fetch = fetch_sheetsage2,
) -> Path:
    copy_environment(SHEETSAGE2_SOURCE, paths.sheetsage2_env)
    sync(paths.sheetsage2_env)
    fetch(paths.sheetsage2_env, paths.sheetsage2)
    return paths.sheetsage2


def install(  # noqa: PLR0913 - keyword-only options and test seams
    paths: Paths,
    *,
    sheetsage2: bool = False,
    base: bool = False,
    download: setup_models.Download = setup_models.download_file,
    sync: Sync = sync_project,
    fetch: Fetch = fetch_sheetsage2,
) -> SetupStatus:
    """Install in dependency order so imports work while weights download."""
    _write(paths.status, SetupStatus("installing", "permissive"))
    try:
        install_permissive(paths, sync=sync)
        _write(paths.status, SetupStatus("installing", "engine"))
        setup_models.install_engine(paths.engine.parent, download=download)
        files = (*setup_models.MODEL_FILES, *(setup_models.BASE_MODEL_FILES if base else ()))
        _write(
            paths.status,
            SetupStatus(
                "installing", "models", total_bytes=sum(entry.size_bytes for entry in files)
            ),
        )
        setup_models.install_models(
            paths.models,
            files=files,
            download=download,
            progress=lambda _name, done, total: _write(
                paths.status, SetupStatus("installing", "models", done, total)
            ),
        )
        if sheetsage2:
            _write(paths.status, SetupStatus("installing", "sheetsage2"))
            install_sheetsage2(paths, sync=sync, fetch=fetch)
    except BaseException as error:
        _write(paths.status, SetupStatus("failed", "install", detail=str(error)))
        raise
    status = SetupStatus("ready", "ready")
    _write(paths.status, status)
    return status


def start_install(paths: Paths) -> Thread:
    """Run installation on a daemon thread while the interface can already open."""

    def run() -> None:
        try:
            install(paths)
        except (OSError, ValueError, RuntimeError) as error:
            _write(paths.status, SetupStatus("failed", "install", detail=str(error)))

    thread = Thread(target=run, name="ghostband-first-run", daemon=True)
    thread.start()
    return thread


def read_status(path: Path) -> SetupStatus:
    """Read the last complete progress file written by install."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return SetupStatus(
        state=str(data["state"]),
        stage=str(data["stage"]),
        done_bytes=int(data["done_bytes"]),
        total_bytes=int(data["total_bytes"]),
        detail=str(data["detail"]),
    )


def first_run_required(paths: Paths) -> bool:
    """Whether anything the live band needs is absent from this account."""
    return not installed(paths)
