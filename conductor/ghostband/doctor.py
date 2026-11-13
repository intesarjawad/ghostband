"""Check local prerequisites without opening an audio device."""

from collections.abc import Iterable
from shutil import which

from ghostband import first_run, release, setup_models
from ghostband.presets import PresetProblem, load_presets


def preset_problems() -> list[PresetProblem]:
    """Presets that were skipped, with the file, field and line that failed."""
    return load_presets()[1]


def engine_problem(paths: first_run.Paths) -> list[str]:
    """The executable and decoder the real engine needs; ghostband starts the engine itself."""
    problems: list[str] = []
    if not setup_models.engine_installed(paths.engine_binary):
        if not paths.engine_binary.is_file():
            problems.append(
                f"engine executable {paths.engine_binary} is missing; run ghostband setup"
            )
        library = paths.engine_binary.with_name(release.METAL_LIBRARY)
        if not library.is_file():
            problems.append(f"engine library {library} is missing; run ghostband setup")
    if which("afconvert") is None:
        problems.append("afconvert is missing")
    return problems


def model_problems(
    paths: first_run.Paths, files: Iterable[setup_models.ModelFile] | None = None
) -> list[str]:
    """Every missing, short or altered model file with the command that repairs it."""
    return [str(problem) for problem in setup_models.inspect_models(paths.models, files)]


def permissive_problem(paths: first_run.Paths) -> list[str]:
    """Whether the permissive extractor environment has been synced by setup."""
    if (paths.permissive / ".venv" / "pyvenv.cfg").is_file():
        return []
    return [
        f"permissive extraction environment {paths.permissive} is not synced; run ghostband setup"
    ]


def check(
    paths: first_run.Paths,
    *,
    fake_engine: bool,
    files: Iterable[setup_models.ModelFile] | None = None,
) -> tuple[list[str], str]:
    """Return actionable problems and the one-line summary to print when green."""
    if fake_engine:
        return [], "fake engine selected"
    problems = [
        *engine_problem(paths),
        *model_problems(paths, files),
        *permissive_problem(paths),
    ]
    return problems, (
        f"engine {paths.engine} ({paths.engine_source}); "
        "audio decoder, model files and permissive extractor found"
    )
