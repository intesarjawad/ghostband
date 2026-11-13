"""Each copy rule fails on a breaking fixture and passes on a corrected one."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_copy.sh"
LICENCE_LINE = (
    "Code Apache-2.0. Model weights CC-BY-4.0 with Google's usage terms; Google claims no "
    "rights in what you make with them. Optional SheetSage2 chart upgrade: non-commercial."
)
# Built from parts so that the repository's own personal-email check does not match this file.
PERSONAL_EMAIL = "listener@" + "gmail.com"
VERSION = "0.1.0"


def repository(
    tmp_path: Path,
    claims: dict[str, object] | None = None,
    *,
    offline: tuple[str, str] | None = None,
) -> Path:
    """A minimal repository with the files the claim rules read."""
    benchmarks = tmp_path / "docs" / "benchmarks"
    benchmarks.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "ghostband"\nversion = "{VERSION}"\n', encoding="utf-8"
    )
    (benchmarks / "claims.json").write_text(json.dumps(claims or {}), encoding="utf-8")
    if offline is not None:
        package = tmp_path / "conductor" / "ghostband"
        package.mkdir(parents=True)
        version, result = offline
        (package / "offline_check.json").write_text(
            json.dumps({"version": version, "result": result}), encoding="utf-8"
        )
    return tmp_path


def check(root: Path, *files: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("uv") is None:
        pytest.skip("the copy check runs through uv")
    return subprocess.run(  # noqa: S603
        [str(SCRIPT), "--root", str(root), *(str(root / name) for name in files)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_personal_email_fails(tmp_path: Path) -> None:
    root = repository(tmp_path)
    write(root, "docs/notes.md", f"Line one.\nWrite to {PERSONAL_EMAIL}.\n")
    finished = check(root)
    assert finished.returncode == 1
    assert "docs/notes.md:2: uses a personal email address" in finished.stderr


def readme(hero: str) -> str:
    return f"# Ghostband\n\n![The band playing](docs/demo.gif)\n\n{hero}\n\n## Install\n\nFree.\n"


def test_free_on_the_first_screen_needs_the_licence_line(tmp_path: Path) -> None:
    root = repository(tmp_path)
    write(root, "README.md", readme("A free band on your Mac."))
    finished = check(root)
    assert finished.returncode == 1
    assert 'README.md:5: says "free" above the first heading' in finished.stderr


def test_free_with_the_linked_licence_line_passes(tmp_path: Path) -> None:
    root = repository(tmp_path)
    # The line is linked to the licence section and wrapped, as the README writes it.
    first, second = LICENCE_LINE.split("; ")
    write(root, "README.md", readme(f"A free band.\n\n[{first};\n{second}](#licences)"))
    finished = check(root)
    assert finished.returncode == 0, finished.stderr


def test_free_below_the_first_screen_needs_no_licence_line(tmp_path: Path) -> None:
    root = repository(tmp_path)
    write(root, "README.md", readme("A band on your Mac."))
    finished = check(root, "README.md")
    assert finished.returncode == 0, finished.stderr


@pytest.mark.parametrize(
    ("text", "failing", "passing"),
    [
        ("Loop it, slow it, practise along.", {}, {"tempo_range": [0.7, 1.0]}),
        ("Slow it down to learn a part.", {"tempo_range": [1.0, 1.5]}, {"tempo_range": [0.5, 1]}),
        ("Speed it up for practice.", {"tempo_range": [0.5, 1.0]}, {"tempo_range": [1.0, 1.3]}),
        ("Practise at 80 percent.", {"tempo_range": [0.9, 1.0]}, {"tempo_range": [0.7, 1.0]}),
        ("Practise at 70%.", {"tempo_range": [0.8, 1.0]}, {"tempo_range": [0.7, 1.0]}),
        ("The melody survives a style change.", {}, {"melody_survives": True}),
        ("It keeps your tune.", {"melody_survives": False}, {"melody_survives": True}),
    ],
)
def test_claims_need_their_measurement(
    tmp_path: Path, text: str, failing: dict[str, object], passing: dict[str, object]
) -> None:
    broken = repository(tmp_path / "failing", failing)
    write(broken, "README.md", f"# Ghostband\n\n## Use\n\n{text}\n")
    finished = check(broken)
    assert finished.returncode == 1
    assert "README.md:5:" in finished.stderr
    measured = repository(tmp_path / "passing", passing)
    write(measured, "README.md", f"# Ghostband\n\n## Use\n\n{text}\n")
    finished = check(measured)
    assert finished.returncode == 0, finished.stderr


@pytest.mark.parametrize("offline", [None, ("0.0.9", "pass"), (VERSION, "fail")])
def test_offline_claims_need_a_passing_check_for_this_version(
    tmp_path: Path, offline: tuple[str, str] | None
) -> None:
    broken = repository(tmp_path / "failing", offline=offline)
    write(broken, "README.md", "Nothing leaves your Mac.\nIt works offline.\n")
    finished = check(broken)
    assert finished.returncode == 1
    assert "README.md:1:" in finished.stderr
    assert "README.md:2:" in finished.stderr
    checked = repository(tmp_path / "passing", offline=(VERSION, "pass"))
    write(checked, "README.md", "Nothing leaves your Mac.\nIt works offline.\n")
    finished = check(checked)
    assert finished.returncode == 0, finished.stderr


def test_other_docs_may_say_offline_without_the_check(tmp_path: Path) -> None:
    root = repository(tmp_path)
    write(root, "docs/adr/0001-render.md", "The offline render skips the live loop.\n")
    finished = check(root)
    assert finished.returncode == 0, finished.stderr
