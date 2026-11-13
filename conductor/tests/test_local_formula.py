"""The local formula installs the engine tarball and wheel from files the script just wrote."""

import hashlib
import subprocess
import tarfile
from pathlib import Path

import pytest
from ghostband.release import engine_archive_name

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/make_local_formula.sh"
FORMULA = ROOT / "packaging/homebrew/ghostband.rb"


def run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(SCRIPT), *arguments], cwd=ROOT, capture_output=True, text=True, check=False
    )


@pytest.fixture
def built(tmp_path: Path) -> tuple[Path, Path]:
    engine = tmp_path / "engine-binary"
    engine.write_bytes(b"engine bytes")
    engine.with_name("mlx.metallib").write_bytes(b"metal bytes")
    wheel = tmp_path / "ghostband-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel bytes")
    out = tmp_path / "out"
    finished = run_script("--out", str(out), "--engine", str(engine), "--wheel", str(wheel))
    assert finished.returncode == 0, finished.stderr
    return out.resolve(), wheel


def test_the_tarball_holds_the_engine_and_the_licence(built: tuple[Path, Path]) -> None:
    out, _ = built
    with tarfile.open(out / engine_archive_name("v0.1.0")) as bundle:
        assert sorted(bundle.getnames()) == ["LICENSE", "ghostband-engine", "mlx.metallib"]
        engine = bundle.getmember("ghostband-engine")
        assert engine.mode & 0o111
        extracted = bundle.extractfile(engine)
        assert extracted is not None
        assert extracted.read() == b"engine bytes"
        metal = bundle.extractfile("mlx.metallib")
        assert metal is not None
        assert metal.read() == b"metal bytes"


def test_the_formula_points_at_the_local_tarball_and_its_digest(built: tuple[Path, Path]) -> None:
    out, _ = built
    archive = out / engine_archive_name("v0.1.0")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    lines = (out / "ghostband.rb").read_text().splitlines()
    assert f'  url "file://{archive}"' in lines
    assert f'  sha256 "{digest}"' in lines


def test_every_launcher_run_of_uv_resolves_the_local_wheel(built: tuple[Path, Path]) -> None:
    out, wheel = built
    assert (out / wheel.name).read_bytes() == b"wheel bytes"
    launcher = [
        line for line in (out / "ghostband.rb").read_text().splitlines() if "tool run" in line
    ]
    assert len(launcher) == 2
    assert all(f"tool run --find-links {out} " in line for line in launcher)
    assert launcher[0].count("tool run") == 2
    formula = (out / "ghostband.rb").read_text()
    assert f'    wheel = "file://{out / wheel.name}"' in formula
    assert "https://github.com/" not in "\n".join(
        line for line in formula.splitlines() if "url " in line or "wheel =" in line
    )


def test_the_formula_template_is_otherwise_unchanged(built: tuple[Path, Path]) -> None:
    out, _ = built
    changed = [
        line
        for line in (out / "ghostband.rb").read_text().splitlines()
        if line not in FORMULA.read_text().splitlines()
    ]
    assert len(changed) == 5


def test_an_engine_without_its_metal_library_is_rejected(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.write_bytes(b"engine bytes")
    wheel = tmp_path / "ghostband-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel bytes")
    finished = run_script(
        "--out", str(tmp_path / "out"), "--engine", str(engine), "--wheel", str(wheel)
    )
    assert finished.returncode != 0
    assert "mlx.metallib" in finished.stderr


def test_unknown_options_are_rejected() -> None:
    finished = run_script("--bogus")
    assert finished.returncode == 2
    assert "Usage:" in finished.stderr
