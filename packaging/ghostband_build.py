"""Stage checkout resources for every PEP 517 build, including ordinary uv builds."""

from pathlib import Path
from shutil import copy2, copytree, ignore_patterns

import uv_build
from uv_build import (  # noqa: F401 - PEP 517 hooks
    get_requires_for_build_editable,
    get_requires_for_build_sdist,
    get_requires_for_build_wheel,
    prepare_metadata_for_build_editable,
    prepare_metadata_for_build_wheel,
)


def stage_resources() -> None:
    root = Path(__file__).resolve().parents[1]
    package = root / "conductor/ghostband"
    for name in ("permissive", "sheetsage2"):
        source = root / "extract" / name
        if source.is_dir():
            copytree(
                source,
                package / "extract_envs" / name,
                dirs_exist_ok=True,
                ignore=ignore_patterns(".venv", "__pycache__", "*.pyc"),
            )
    claims = root / "docs/benchmarks/claims.json"
    if claims.is_file():
        copy2(claims, package / "tempo_claims.json")


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    stage_resources()
    return uv_build.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory: str, config_settings: dict[str, object] | None = None) -> str:
    stage_resources()
    return uv_build.build_sdist(sdist_directory, config_settings)


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    stage_resources()
    return uv_build.build_editable(wheel_directory, config_settings, metadata_directory)
