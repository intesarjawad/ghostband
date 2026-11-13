import asyncio
import sys
from importlib import import_module
from importlib.metadata import Distribution
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("License-Expression", "MIT", None),
        ("License", "Apache License, Version 2.0", None),
        ("Classifier", "License :: OSI Approved :: BSD License", None),
        ("License-Expression", "CC-BY-NC-4.0", "non-commercial"),
        ("License", "Creative Commons Attribution NonCommercial", "non-commercial"),
        ("License", "CC BY NC SA 4.0", "non-commercial"),
        ("Classifier", "License :: Other/Proprietary License (NC)", "non-commercial"),
        ("License", "UNKNOWN", "missing licence"),
        ("License-Expression", "NONE", "missing licence"),
        ("License-Expression", "NOASSERTION", "missing licence"),
        ("Classifier", "License :: OSI Approved", "missing licence"),
        ("Summary", "Example package", "missing licence"),
    ],
)
def test_license_metadata(
    field: str, value: str, error: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    checker = import_module("check_licenses")
    metadata = tmp_path / "example-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(f"Name: example\nVersion: 1.0\n{field}: {value}\n")
    result = checker.check_distribution(Distribution.at(metadata))
    if error is None:
        assert result is None
    else:
        assert error in result


def test_license_project_check_uses_target_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    checker = import_module("check_licenses")
    calls: list[tuple[object, ...]] = []

    async def wait() -> int:
        return 1

    async def spawn(*args: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(wait=wait)

    def executable(_name: str) -> str:
        return "/local/uv"

    monkeypatch.setattr(checker, "which", executable)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("sys.argv", ["check_licenses.py", "--project", str(tmp_path)])
    assert asyncio.run(checker.main()) == 1
    assert calls == [
        (
            "/local/uv",
            "run",
            "--offline",
            "--frozen",
            "--project",
            str(tmp_path),
            "python",
            str(ROOT / "scripts/check_licenses.py"),
            "--installed",
        )
    ]


@pytest.mark.parametrize(
    "structured",
    ["License-Expression: BSD-3-Clause\n", "Classifier: License :: OSI Approved :: BSD License\n"],
)
def test_structured_license_precedes_bundled_license_text(
    structured: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    checker = import_module("check_licenses")
    metadata = tmp_path / "scipy-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Name: scipy\nVersion: 1.0\n" + structured + "License: Copyright SciPy Developers\n"
        " Bundled GPL text permits redistribution commercially or noncommercially.\n"
    )
    assert checker.check_distribution(Distribution.at(metadata)) is None


def test_free_text_license_uses_only_first_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    checker = import_module("check_licenses")
    metadata = tmp_path / "example-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Name: example\nVersion: 1.0\nLicense: BSD-3-Clause\n"
        " Bundled GPL text permits redistribution commercially or noncommercially.\n"
    )
    assert checker.check_distribution(Distribution.at(metadata)) is None


@pytest.mark.parametrize(
    ("heading", "error"),
    [
        ("Copyright 2022 Spotify AB\n\n Apache License\n Version 2.0, January 2004\n", None),
        ("BSD-3-Clause\n\nBundled GPL permits use commercially or noncommercially.\n", None),
        ("Creative Commons Attribution NonCommercial 4.0\n", "non-commercial"),
        ("Copyright 2022 Example\nAll rights reserved.\n", "missing licence"),
        ("MITCHELL contributor terms\n", "missing licence"),
        ("", "missing licence"),
    ],
)
def test_declared_license_files_identify_license(
    heading: str, error: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    checker = import_module("check_licenses")
    metadata = tmp_path / "basic_pitch-0.4.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Name: basic-pitch\nVersion: 0.4.0\nLicense: UNKNOWN\n"
        "License-File: LICENSE\nLicense-File: NOTICE\n"
    )
    if heading:
        (metadata / "LICENSE").write_text(heading)
    (metadata / "NOTICE").write_text("Basic Pitch\nCopyright 2022 Spotify AB\n")
    result = checker.check_distribution(Distribution.at(metadata))
    if error is None:
        assert result is None
    else:
        assert error in result
