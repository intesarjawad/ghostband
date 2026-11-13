import os
import subprocess
import tarfile
import textwrap
from importlib.metadata import version
from pathlib import Path

import pytest
from ghostband import release, setup_models

from scripts.package_engine import package_engine


def test_packaged_engine_matches_downloader_contract(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    for name in release.ENGINE_MEMBERS:
        (build / name).write_bytes(name.encode())
    tag = f"v{version('ghostband')}"
    archive = package_engine(build, build / "LICENSE", tmp_path / "dist", tag)
    assert archive.name == setup_models.ENGINE_ARCHIVE_NAME
    assert setup_models.engine_archive_url() == release.engine_archive_url(tag)
    with tarfile.open(archive) as bundle:
        assert bundle.getnames() == list(release.ENGINE_MEMBERS)

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(archive.read_bytes())

    binary = setup_models.install_engine(tmp_path / "installed", download=download)
    assert binary.read_bytes() == b"ghostband-engine"
    assert binary.with_name("mlx.metallib").read_bytes() == b"mlx.metallib"


def test_release_fails_before_packaging_without_metal(tmp_path: Path) -> None:
    (tmp_path / "ghostband-engine").write_bytes(b"engine")
    with pytest.raises(FileNotFoundError, match=r"mlx\.metallib"):
        package_engine(tmp_path, tmp_path / "LICENSE", tmp_path / "dist", "v1.0.0")


@pytest.mark.parametrize(("package", "tag"), [("1.2.3", "v1.2.3"), ("1.2.3rc2", "v1.2.3-rc.2")])
def test_release_candidates_use_the_workflow_tag(
    package: str,
    tag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def package_version(_name: str) -> str:
        return package

    monkeypatch.setattr(release, "version", package_version)
    assert release.engine_archive_name() == release.engine_archive_name(tag)
    assert release.engine_archive_url() == release.engine_archive_url(tag)
    assert release.wheel_url(tag).endswith(f"/{tag}/ghostband-{package}-py3-none-any.whl")


@pytest.mark.parametrize("tag", ["v1.2.3", "v1.2.3-rc.2"])
def test_workflow_renders_a_formula_for_the_matching_release_wheel(
    tmp_path: Path, tag: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("conductor", "packaging"):
        (tmp_path / name).symlink_to(root / name, target_is_directory=True)
    build = tmp_path / "build"
    build.mkdir()
    for name in release.ENGINE_MEMBERS:
        (build / name).write_bytes(name.encode())
    archive = package_engine(build, build / "LICENSE", tmp_path / "assets", tag)
    workflow = (root / ".github/workflows/release.yml").read_text()
    step = workflow.split("- name: Write the formula for this tag\n        run: |\n")[1]
    script = textwrap.dedent(step.split("      - name: Create the draft release")[0])
    result = subprocess.run(  # noqa: S603 - execute only the local formula step, never publishing
        ["/bin/bash", "-e", "-c", script],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "GITHUB_REF_NAME": tag},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    formula = (tmp_path / "assets/ghostband.rb").read_text()
    assert f'url "{release.engine_archive_url(tag)}"' in formula
    assert f'wheel = "{release.wheel_url(tag)}"' in formula
    assert f'sha256 "{setup_models.sha256_file(archive)}"' in formula
    assert 'libexec.install "ghostband-engine", "mlx.metallib"' in formula
    assert "--from #{wheel}" in formula
