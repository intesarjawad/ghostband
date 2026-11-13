import io
import tarfile
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from ghostband import setup_models
from ghostband.setup_models import ModelFile

ALPHA = ModelFile(
    "resources/alpha.txt", 6, "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060"
)
BRAVO = ModelFile(
    "models/bravo.txt", 6, "5da8f23decf397b13f4f55b6fb8a61936238bfe08ed9d901132974f1beccc45c"
)
CONTENTS = {
    ALPHA.url: b"alpha\n",
    BRAVO.url: b"bravo\n",
}
ENGINE_BYTES = b"engine"


@contextmanager
def serve(payload: bytes) -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/asset"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def engine_archive(tmp_path: Path, *, name: str = "bin/ghostband-engine") -> bytes:
    source = tmp_path / "source"
    source.mkdir()
    binary = source / "ghostband-engine"
    binary.write_bytes(ENGINE_BYTES)
    archive = tmp_path / "engine.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(binary, arcname=name)
        if name == "bin/ghostband-engine":
            bundle.add(binary, arcname="bin/mlx.metallib")
    return archive.read_bytes()


def test_the_default_set_is_the_beat_checkpoint_then_twelve_pinned_files() -> None:
    assert len(setup_models.MODEL_FILES) == 13
    assert sum(entry.size_bytes for entry in setup_models.MODEL_FILES) == 1_921_131_032
    assert setup_models.MODEL_FILES[0].name == "beat-this/final0.ckpt"
    assert setup_models.MODEL_FILES[0].url == setup_models.BEAT_THIS_URL
    assert {entry.name for entry in setup_models.MODEL_FILES} >= {
        "models/mrt2_small/mrt2_small.mlxfn",
        "models/mrt2_small/mrt2_small_state.safetensors",
        "resources/musiccoca/music_encoder.tflite",
        "resources/spectrostream/decoder.safetensors",
    }
    assert all(len(entry.sha256) == 64 for entry in setup_models.MODEL_FILES)


def test_the_base_model_is_two_pinned_files_outside_the_default_set() -> None:
    assert [entry.name for entry in setup_models.BASE_MODEL_FILES] == [
        "models/mrt2_base/mrt2_base.mlxfn",
        "models/mrt2_base/mrt2_base_state.safetensors",
    ]
    assert sum(entry.size_bytes for entry in setup_models.BASE_MODEL_FILES) == 2_788_354_715
    assert all(len(entry.sha256) == 64 for entry in setup_models.BASE_MODEL_FILES)
    assert not set(setup_models.BASE_MODEL_FILES) & set(setup_models.MODEL_FILES)


def test_each_model_needs_its_own_weights_and_the_shared_resources() -> None:
    assert setup_models.model_files("mrt2_small") == setup_models.MODEL_FILES
    base = setup_models.model_files("mrt2_base")
    assert [entry.name for entry in base if entry.name.startswith("models/")] == [
        "models/mrt2_base/mrt2_base.mlxfn",
        "models/mrt2_base/mrt2_base_state.safetensors",
    ]
    assert len(base) == 12
    with pytest.raises(ValueError, match="unknown model"):
        setup_models.model_files("mrt2_large")


def test_audio_input_is_the_three_encoder_files_inside_the_default_set() -> None:
    assert [entry.name for entry in setup_models.AUDIO_INPUT_FILES] == [
        "resources/musiccoca/music_encoder.tflite",
        "resources/spectrostream/encoder.safetensors",
        "resources/spectrostream/spectrostream_encoder.mlxfn",
    ]
    assert sum(entry.size_bytes for entry in setup_models.AUDIO_INPUT_FILES) == 512_268_959
    assert set(setup_models.AUDIO_INPUT_FILES) <= set(setup_models.MODEL_FILES)


def test_fetching_audio_input_restores_only_the_deleted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_models, "AUDIO_INPUT_FILES", (ALPHA, BRAVO))
    requested: list[str] = []
    progress: list[tuple[str, int, int]] = []

    def download(url: str, destination: Path) -> None:
        requested.append(url)
        destination.write_bytes(CONTENTS[url])

    def fetch() -> tuple[Path, ...]:
        return setup_models.fetch_audio_input(
            tmp_path,
            download=download,
            progress=lambda name, done, total: progress.append((name, done, total)),
        )

    assert fetch() == (tmp_path / ALPHA.name, tmp_path / BRAVO.name)
    assert progress == [(ALPHA.name, 6, 12), (BRAVO.name, 12, 12)]
    assert fetch() == ()

    (tmp_path / BRAVO.name).unlink()
    assert fetch() == (tmp_path / BRAVO.name,)
    assert requested == [ALPHA.url, BRAVO.url, BRAVO.url]


def test_fetching_audio_input_defaults_to_the_model_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_models, "AUDIO_INPUT_FILES", (ALPHA,))
    monkeypatch.setenv("GHOSTBAND_MODEL_DIR", str(tmp_path))

    def download(url: str, destination: Path) -> None:
        destination.write_bytes(CONTENTS[url])

    installed = setup_models.fetch_audio_input(download=download)
    assert installed == (tmp_path / ALPHA.name,)


def test_inspection_names_each_missing_short_and_altered_file(tmp_path: Path) -> None:
    (tmp_path / ALPHA.name).parent.mkdir(parents=True)
    (tmp_path / ALPHA.name).write_bytes(CONTENTS[ALPHA.url])
    (tmp_path / BRAVO.name).parent.mkdir(parents=True)
    (tmp_path / BRAVO.name).write_bytes(b"bravo\n")
    assert setup_models.inspect_models(tmp_path, (ALPHA, BRAVO)) == []

    (tmp_path / BRAVO.name).write_bytes(b"brav")
    (tmp_path / ALPHA.name).write_bytes(b"xxxxx\n")
    problems = setup_models.inspect_models(tmp_path, (ALPHA, BRAVO))
    assert [problem.path for problem in problems] == [tmp_path / ALPHA.name, tmp_path / BRAVO.name]
    assert problems[0].reason == "sha256 does not match the pinned revision"
    assert problems[1].reason == "is 4 bytes; expected 6"
    assert "Run ghostband setup" in str(problems[0])


def test_missing_file_is_named_with_the_restoring_command(tmp_path: Path) -> None:
    problems = setup_models.inspect_models(tmp_path, (ALPHA,))
    assert str(problems[0]) == (
        f"{tmp_path / ALPHA.name}: missing; Run ghostband setup to restore the model files"
    )


def test_install_downloads_only_missing_or_damaged_files(tmp_path: Path) -> None:
    requested: list[str] = []
    progress: list[tuple[str, int, int]] = []

    def download(url: str, destination: Path) -> None:
        requested.append(url)
        destination.write_bytes(CONTENTS[url])

    installed = setup_models.install_models(
        tmp_path,
        files=(ALPHA, BRAVO),
        download=download,
        progress=lambda name, done, total: progress.append((name, done, total)),
    )
    assert installed == (tmp_path / ALPHA.name, tmp_path / BRAVO.name)
    assert requested == [ALPHA.url, BRAVO.url]
    assert progress == [(ALPHA.name, 6, 12), (BRAVO.name, 12, 12)]
    assert (tmp_path / ALPHA.name).read_bytes() == b"alpha\n"

    assert setup_models.install_models(tmp_path, files=(ALPHA, BRAVO), download=download) == ()
    assert requested == [ALPHA.url, BRAVO.url]


def test_a_download_that_fails_its_checksum_leaves_no_file(tmp_path: Path) -> None:
    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(b"xxxxx\n")

    with pytest.raises(ValueError, match="sha256"):
        setup_models.install_models(tmp_path, files=(ALPHA,), download=download)
    assert not (tmp_path / ALPHA.name).exists()
    assert not (tmp_path / ALPHA.name).with_name("alpha.txt.part").exists()


def test_download_streams_the_response_body(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "asset.bin"
    with serve(b"payload") as url:
        setup_models.download_file(url, destination)
    assert destination.read_bytes() == b"payload"


def test_engine_archive_downloads_and_installs_one_executable(tmp_path: Path) -> None:
    archive = engine_archive(tmp_path)
    engine_dir = tmp_path / "installed"
    with serve(archive) as url:
        binary = setup_models.install_engine(engine_dir, url=url)
    assert binary == engine_dir / "ghostband-engine"
    assert binary.read_bytes() == ENGINE_BYTES
    assert binary.stat().st_mode & 0o111
    assert binary.with_name("mlx.metallib").read_bytes() == ENGINE_BYTES


def test_engine_archive_rejects_a_member_outside_its_directory(tmp_path: Path) -> None:
    archive = tmp_path / "escape.tar.gz"
    archive.write_bytes(engine_archive(tmp_path, name="../ghostband-engine"))
    with pytest.raises(ValueError, match="unsafe"):
        setup_models.extract_engine(archive, tmp_path / "contents")
    assert not (tmp_path / "ghostband-engine").exists()


def test_engine_archive_without_the_executable_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("not an engine")
    archive = tmp_path / "missing.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname="note.txt")
    with pytest.raises(ValueError, match="expected one ghostband-engine"):
        setup_models.extract_engine(archive, tmp_path / "contents")


def test_engine_archive_with_absolute_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "absolute.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("/ghostband-engine")
        info.size = len(ENGINE_BYTES)
        bundle.addfile(info, io.BytesIO(ENGINE_BYTES))
    with pytest.raises(ValueError, match="unsafe"):
        setup_models.extract_engine(archive, tmp_path / "contents")


def test_an_existing_engine_without_metal_is_repaired(tmp_path: Path) -> None:
    archive = engine_archive(tmp_path)
    engine_dir = tmp_path / "installed"
    engine_dir.mkdir()
    (engine_dir / "ghostband-engine").write_bytes(b"incomplete")
    with serve(archive) as url:
        binary = setup_models.install_engine(engine_dir, url=url)
    assert setup_models.engine_installed(binary)
    assert binary.read_bytes() == ENGINE_BYTES


def test_engine_without_metal_is_not_installed(tmp_path: Path) -> None:
    archive = tmp_path / "engine.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("ghostband-engine")
        member.size = len(ENGINE_BYTES)
        bundle.addfile(member, io.BytesIO(ENGINE_BYTES))
    with (
        serve(archive.read_bytes()) as url,
        pytest.raises(ValueError, match=r"missing mlx\.metallib"),
    ):
        setup_models.install_engine(tmp_path / "installed", url=url)
    assert not (tmp_path / "installed/ghostband-engine").exists()
