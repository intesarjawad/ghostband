"""Run by test_wheel_install with only the installed wheel on sys.path."""

import hashlib
import io
import os
import tarfile
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from click.testing import CliRunner
from ghostband import first_run, setup_models
from ghostband.cli import main
from ghostband.session import measured_tempo_range


def check() -> None:
    assert not (Path.cwd() / "conductor").exists()
    os.environ["GHOSTBAND_DATA_DIR"] = str(Path.cwd() / "data")
    for name in (
        "GHOSTBAND_ENGINE",
        "GHOSTBAND_MODEL_DIR",
        "GHOSTBAND_RESOURCES",
        "GHOSTBAND_PERMISSIVE_ENV",
        "GHOSTBAND_SHEETSAGE2_CACHE",
    ):
        os.environ.pop(name, None)
    for name in ("permissive", "sheetsage2"):
        resource = files("ghostband").joinpath("extract_envs", name)
        assert resource.joinpath("uv.lock").is_file()
        assert resource.joinpath("run.py").is_file()
    assert files("ghostband").joinpath("tempo_claims.json").is_file()
    assert measured_tempo_range() == (1.0, 1.0)
    payload = b"model"
    model = setup_models.ModelFile(
        "resources/test", len(payload), hashlib.sha256(payload).hexdigest()
    )
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for name in ("ghostband-engine", "mlx.metallib"):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    requested: list[str] = []

    def network(request: Request, *, timeout: int) -> io.BytesIO:
        assert timeout > 0
        requested.append(request.full_url)
        return io.BytesIO(
            archive.getvalue() if request.full_url == setup_models.engine_archive_url() else payload
        )

    async def uv(project: Path, *arguments: str) -> None:
        assert (project / "uv.lock").is_file()
        assert arguments == ("sync", "--frozen", "--no-dev")

    with (
        patch.object(first_run, "_uv", uv),
        patch.object(setup_models, "urlopen", network),
        patch.object(setup_models, "MODEL_FILES", (model,)),
    ):
        result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0, result.output
    paths = first_run.Paths.defaults()
    assert paths.engine.is_file()
    assert paths.engine.with_name("mlx.metallib").read_bytes() == payload
    assert first_run.read_status(paths.status).state == "ready"
    assert requested == [setup_models.engine_archive_url(), model.url]


if __name__ == "__main__":
    check()
