"""Exercise a real wheel in a fresh environment, without checkout imports or downloads."""

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


def test_installed_wheel_setup_outside_checkout(tmp_path: Path) -> None:
    def run(*args: str, cwd: Path = tmp_path) -> None:
        result = subprocess.run(  # noqa: S603 - fixed tools and test-owned paths
            args,
            cwd=cwd,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    dist = tmp_path / "dist"
    run("uv", "build", "--wheel", "--out-dir", str(dist), cwd=ROOT)
    wheel = next(dist.glob("*.whl"))
    run("uv", "build", "--sdist", "--out-dir", str(dist), cwd=ROOT)
    source = next(dist.glob("*.tar.gz"))
    rebuilt = tmp_path / "rebuilt"
    run("uv", "build", str(source), "--wheel", "--out-dir", str(rebuilt))
    with ZipFile(wheel) as direct, ZipFile(next(rebuilt.glob("*.whl"))) as from_source:
        resources = [
            name
            for name in direct.namelist()
            if "/extract_envs/" in name or name.endswith("tempo_claims.json")
        ]
        assert resources
        for name in resources:
            assert direct.read(name) == from_source.read(name)
    environment = tmp_path / "venv"
    run("uv", "venv", "--python", sys.executable, str(environment))
    python = environment / "bin/python"
    run("uv", "pip", "install", "--python", str(python), str(wheel))
    run(str(python), "-I", str(ROOT / "conductor/tests/wheel_setup_probe.py"))
