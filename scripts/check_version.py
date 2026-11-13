"""Refuse a release tag that disagrees with the package and engine versions.

Run it as `uv run python scripts/check_version.py v0.1.0` from the repository root. A final tag
`vX.Y.Z` needs package version `X.Y.Z`; a release candidate `vX.Y.Z-rc.N` needs `X.Y.ZrcN`. The
engine version in engine/CMakeLists.txt is numeric, so it must always equal `X.Y.Z`.
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"v(?P<release>\d+\.\d+\.\d+)(?:-rc\.(?P<candidate>\d+))?")
CMAKE_VERSION = re.compile(r"^project\(\s*\w+\s+VERSION\s+(?P<version>\S+)", re.MULTILINE)


def package_version(pyproject: Path) -> str:
    return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])


def engine_version(cmake_lists: Path) -> str:
    found = CMAKE_VERSION.search(cmake_lists.read_text(encoding="utf-8"))
    if found is None:
        raise ValueError(f"{cmake_lists}: no project() VERSION found")
    return found["version"]


def problems(tag: str, package: str, engine: str) -> list[str]:
    """Every way the tag disagrees with the two versions; empty when the release is consistent."""
    parsed = TAG.fullmatch(tag)
    if parsed is None:
        return [f"tag {tag} must look like v1.2.3 or v1.2.3-rc.1"]
    release, candidate = parsed["release"], parsed["candidate"]
    expected = release if candidate is None else f"{release}rc{candidate}"
    found: list[str] = []
    if package != expected:
        found.append(
            f"tag {tag} needs package version {expected}, but pyproject.toml has {package}"
        )
    if engine != release:
        found.append(f"tag {tag} needs engine version {release}, but CMakeLists.txt has {engine}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="the release tag, for example v0.1.0 or v0.1.0-rc.1")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to read")
    args = parser.parse_args()
    found = problems(
        args.tag,
        package_version(args.root / "pyproject.toml"),
        engine_version(args.root / "engine" / "CMakeLists.txt"),
    )
    for line in found:
        sys.stderr.write(f"check_version: {line}\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
