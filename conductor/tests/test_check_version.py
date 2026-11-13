"""The release check accepts a tag only when the package and engine versions agree with it."""

import sys
import tomllib
from pathlib import Path

import pytest

from scripts import check_version
from scripts.check_version import engine_version, package_version, problems

ROOT = Path(__file__).resolve().parents[2]


def write_root(tmp_path: Path, package: str, engine: str) -> Path:
    (tmp_path / "engine").mkdir()
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{package}"\n')
    (tmp_path / "engine" / "CMakeLists.txt").write_text(
        f"cmake_minimum_required(VERSION 3.27)\nproject(Ghostband VERSION {engine} LANGUAGES C)\n"
    )
    return tmp_path


def test_a_final_tag_matches_the_package_and_engine() -> None:
    assert problems("v0.1.0", "0.1.0", "0.1.0") == []


def test_a_release_candidate_tag_matches_the_normalised_package_version() -> None:
    assert problems("v0.1.0-rc.1", "0.1.0rc1", "0.1.0") == []


def test_a_mismatched_tag_names_the_package_version_it_needs() -> None:
    assert problems("v0.2.0", "0.1.0", "0.1.0") == [
        "tag v0.2.0 needs package version 0.2.0, but pyproject.toml has 0.1.0",
        "tag v0.2.0 needs engine version 0.2.0, but CMakeLists.txt has 0.1.0",
    ]


def test_a_candidate_tag_does_not_match_the_final_package_version() -> None:
    assert problems("v0.1.0-rc.2", "0.1.0", "0.1.0") == [
        "tag v0.1.0-rc.2 needs package version 0.1.0rc2, but pyproject.toml has 0.1.0"
    ]


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1", "v0.1.0-beta.1", "v0.1.0-rc", "v0.1.0 "])
def test_a_malformed_tag_is_rejected(tag: str) -> None:
    assert problems(tag, "0.1.0", "0.1.0") == [f"tag {tag} must look like v1.2.3 or v1.2.3-rc.1"]


def test_versions_are_read_from_the_project_files(tmp_path: Path) -> None:
    root = write_root(tmp_path, "3.4.5rc2", "3.4.5")
    assert package_version(root / "pyproject.toml") == "3.4.5rc2"
    assert engine_version(root / "engine" / "CMakeLists.txt") == "3.4.5"


def test_a_missing_engine_version_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "CMakeLists.txt"
    path.write_text("project(Ghostband LANGUAGES C)\n")
    with pytest.raises(ValueError, match="no project"):
        engine_version(path)


def run_main(
    root: Path, tag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["check_version.py", tag, "--root", str(root)])
    code = check_version.main()
    return code, capsys.readouterr().err


def test_main_fails_on_a_mismatched_tag_and_passes_on_a_matching_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_root(tmp_path, "0.1.0", "0.1.0")
    code, message = run_main(root, "v0.9.0", monkeypatch, capsys)
    assert code == 1
    assert "tag v0.9.0 needs package version 0.9.0, but pyproject.toml has 0.1.0" in message
    assert run_main(root, "v0.1.0", monkeypatch, capsys) == (0, "")


def test_the_repository_versions_agree_with_each_other() -> None:
    release = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert engine_version(ROOT / "engine" / "CMakeLists.txt") == release
