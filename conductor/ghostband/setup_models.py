"""Pinned model assets, their checksums and the release engine archive."""

import hashlib
import os
import tarfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from shutil import copy2
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

from ghostband import release

MODEL_REVISION = "010aa0dcb0dfd27b24f0ad07b4dad63e8f9521cc"
MODEL_BASE_URL = f"https://huggingface.co/google/magenta-realtime-2/resolve/{MODEL_REVISION}"
ENGINE_NAME = release.ENGINE_NAME
ENGINE_ARCHIVE_NAME = release.engine_archive_name()
ENGINE_ARCHIVE_URL = release.engine_archive_url()
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60

Progress = Callable[[str, int, int], None]
Download = Callable[[str, Path], None]


def app_directory() -> Path:
    """The per-user data directory, overridable for tests and portable installs."""
    configured = os.environ.get("GHOSTBAND_DATA_DIR")
    if configured:
        return Path(configured)
    return Path.home() / "Library/Application Support/ghostband"


def model_directory() -> Path:
    """The model root holding resources/ and models/mrt2_small/."""
    configured = os.environ.get("GHOSTBAND_MODEL_DIR")
    if configured:
        return Path(configured)
    return app_directory() / "models"


def engine_archive_url() -> str:
    return os.environ.get("GHOSTBAND_ENGINE_ARCHIVE", ENGINE_ARCHIVE_URL)


@dataclass(frozen=True)
class ModelFile:
    """One upstream resource with the size and digest of its pinned revision."""

    name: str
    size_bytes: int
    sha256: str
    source: str = ""

    @property
    def url(self) -> str:
        return self.source or f"{MODEL_BASE_URL}/{self.name}"


BEAT_THIS_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"
# Audio import finds beats with this checkpoint; the extractor never downloads it itself.
BEAT_THIS_CHECKPOINT = ModelFile(
    "beat-this/final0.ckpt",
    81_058_141,
    "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331",
    BEAT_THIS_URL,
)

# The full default set recorded in ADR 0012: resources plus mrt2_small at the
# pinned revision, each an LFS object with a SHA-256 digest, after the small beat
# checkpoint so audio import works while the larger files download.
MODEL_FILES: tuple[ModelFile, ...] = (
    BEAT_THIS_CHECKPOINT,
    ModelFile(
        "models/mrt2_small/mrt2_small.mlxfn",
        455_654_550,
        "1a70b0de30b3e6ad054fe6a61a7765408f01127628e6362c1abc328809a3c422",
    ),
    ModelFile(
        "models/mrt2_small/mrt2_small_state.safetensors",
        8_676_998,
        "23f1e05a6beea306fe39970bd61193f2d3e5fbd8f08af93570bda4ca9ec33255",
    ),
    ModelFile(
        "resources/musiccoca/audio_preprocessor.tflite",
        8_729_640,
        "656ca4c358451c2b85932e66efcfd2ba62492f4435953d775bfc1d3c08329a30",
    ),
    ModelFile(
        "resources/musiccoca/mapper.tflite",
        86_166_664,
        "2f9743cc8f121a588b69c7f4d79a2a4111ce81864cbde8830054cd5e97f3d717",
    ),
    ModelFile(
        "resources/musiccoca/music_encoder.tflite",
        370_935_584,
        "0d4501af799834e383d904c34ee826c61eb53c69682bf15a981a46c1bb32793a",
    ),
    ModelFile(
        "resources/musiccoca/pretrained_vector_quantizer.tflite",
        72_422_108,
        "7a8a19e2119ad405818eae84a331a970f1a582b3389d4bfd27814f75b455a444",
    ),
    ModelFile(
        "resources/musiccoca/spm.model",
        517_448,
        "ff325a99b61ba5726cf6437cde6eefbb633dbaa363a684f7a97ed99b55202cca",
    ),
    ModelFile(
        "resources/musiccoca/text_encoder.tflite",
        418_674_324,
        "e1222e3418cbe8cc2623939571bae8e9ab6f0d511404b0d83da69f4e6e11b272",
    ),
    ModelFile(
        "resources/spectrostream/decoder.safetensors",
        209_853_216,
        "0ac6f100a24945fb434783fde6acd7902ceaa8bca492ca317edfc75dd51c42dd",
    ),
    ModelFile(
        "resources/spectrostream/encoder.safetensors",
        37_013_392,
        "f20c197ddcbb9cd43e1a97f9bee0d07d211f79966cd3862d9830a32885090f72",
    ),
    ModelFile(
        "resources/spectrostream/quantizer.safetensors",
        67_108_984,
        "0ba89dcb85344bb14f4f34b8f597c0d6adaa560002d4fd88879a2944c98a20f0",
    ),
    ModelFile(
        "resources/spectrostream/spectrostream_encoder.mlxfn",
        104_319_983,
        "887c25b21aa1714d19907fc96963c6440d5911f11571054cd5acf7306c260905",
    ),
)

# The larger model is opt-in: setup downloads it only for --with-base, and the
# live band offers it only after ghostband bench shows it keeps up.
BASE_MODEL_FILES: tuple[ModelFile, ...] = (
    ModelFile(
        "models/mrt2_base/mrt2_base.mlxfn",
        2_771_414_746,
        "ee2f19f2782182095fcd05c0fc1978f7f3e020b1cc0993e9d8e643e2f7de0bfb",
    ),
    ModelFile(
        "models/mrt2_base/mrt2_base_state.safetensors",
        16_939_969,
        "88b302502aa5b467b74b0591adefd7769cb620211bf18606b7656a9ea57eef5f",
    ),
)

# The files that turn a dropped clip into a style: the audio encoder and the
# two SpectroStream encoder files. Text-only playback never reads them.
AUDIO_INPUT_NAMES: tuple[str, ...] = (
    "resources/musiccoca/music_encoder.tflite",
    "resources/spectrostream/encoder.safetensors",
    "resources/spectrostream/spectrostream_encoder.mlxfn",
)
AUDIO_INPUT_FILES: tuple[ModelFile, ...] = tuple(
    entry for entry in MODEL_FILES if entry.name in AUDIO_INPUT_NAMES
)

RESTORE_COMMAND = "Run ghostband setup to restore the model files"


@dataclass(frozen=True)
class ModelProblem:
    """A missing, short or altered file, with the command that repairs it."""

    path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}; {RESTORE_COMMAND}"


def model_files(model: str) -> tuple[ModelFile, ...]:
    """Every file a model needs: its own weights and the shared resources."""
    if model == "mrt2_small":
        return MODEL_FILES
    if model == "mrt2_base":
        resources = tuple(entry for entry in MODEL_FILES if entry.name.startswith("resources/"))
        return (*resources, *BASE_MODEL_FILES)
    raise ValueError(f"unknown model {model}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_models(
    root: Path, files: Iterable[ModelFile] | None = None, *, verify_hashes: bool = True
) -> list[ModelProblem]:
    """Return every installed file that is missing, the wrong size or altered."""
    problems: list[ModelProblem] = []
    for entry in files if files is not None else MODEL_FILES:
        path = root / entry.name
        if not path.is_file():
            problems.append(ModelProblem(path, "missing"))
            continue
        size = path.stat().st_size
        if size != entry.size_bytes:
            problems.append(ModelProblem(path, f"is {size} bytes; expected {entry.size_bytes}"))
        elif verify_hashes and sha256_file(path) != entry.sha256:
            problems.append(ModelProblem(path, "sha256 does not match the pinned revision"))
    return problems


def download_file(url: str, destination: Path) -> None:
    """Stream one release or model file to a path the caller can verify first."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "ghostband"})  # noqa: S310
    # The URL is a pinned release location or an explicit user override, not user input.
    with (
        urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response,  # noqa: S310
        destination.open("wb") as output,
    ):
        while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
            output.write(chunk)


def _verify_download(destination: Path, entry: ModelFile) -> None:
    size = destination.stat().st_size
    if size != entry.size_bytes:
        raise ValueError(f"{destination}: is {size} bytes; expected {entry.size_bytes}")
    if sha256_file(destination) != entry.sha256:
        raise ValueError(f"{destination}: sha256 does not match the pinned revision")


def install_models(
    root: Path,
    *,
    files: Iterable[ModelFile] | None = None,
    download: Download = download_file,
    progress: Progress | None = None,
) -> tuple[Path, ...]:
    """Download only missing or damaged files, verifying each before installing it."""
    entries = tuple(files if files is not None else MODEL_FILES)
    installed: list[Path] = []
    done_bytes = 0
    total_bytes = sum(entry.size_bytes for entry in entries)
    for entry in entries:
        path = root / entry.name
        problem = inspect_models(root, (entry,))
        if not problem:
            done_bytes += entry.size_bytes
            continue
        partial = path.with_name(path.name + ".part")
        partial.unlink(missing_ok=True)
        partial.parent.mkdir(parents=True, exist_ok=True)
        try:
            download(entry.url, partial)
            _verify_download(partial, entry)
            partial.replace(path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        installed.append(path)
        done_bytes += entry.size_bytes
        if progress:
            progress(entry.name, done_bytes, total_bytes)
    return tuple(installed)


def fetch_audio_input(
    root: Path | None = None,
    *,
    download: Download = download_file,
    progress: Progress | None = None,
) -> tuple[Path, ...]:
    """Download and hash-check whichever audio-input files are missing or damaged."""
    return install_models(
        root if root is not None else model_directory(),
        files=AUDIO_INPUT_FILES,
        download=download,
        progress=progress,
    )


def engine_binary(engine_directory: Path) -> Path:
    return engine_directory / ENGINE_NAME


def extract_engine(archive: Path, destination: Path) -> Path:
    """Unpack one release tarball and return its single engine executable."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        try:
            for member in bundle.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError(f"member {member.name} escapes the archive")
            bundle.extractall(destination, filter="data")
        except (tarfile.TarError, ValueError) as error:
            raise ValueError(f"{archive}: unsafe or unreadable engine archive: {error}") from error
    candidates = sorted(path for path in destination.rglob(ENGINE_NAME) if path.is_file())
    if len(candidates) != 1:
        raise ValueError(f"{archive}: expected one {ENGINE_NAME} executable")
    binary = candidates[0]
    binary.chmod(binary.stat().st_mode | 0o111)
    return binary


def engine_installed(binary: Path) -> bool:
    return binary.is_file() and binary.with_name(release.METAL_LIBRARY).is_file()


def install_engine(
    engine_directory: Path,
    *,
    url: str | None = None,
    download: Download = download_file,
) -> Path:
    """Install the release engine beside the models and return its executable."""
    destination = engine_binary(engine_directory)
    if engine_installed(destination):
        return destination
    engine_directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="ghostband-engine-", dir=engine_directory.parent) as temporary:
        archive = Path(temporary) / ENGINE_ARCHIVE_NAME
        download(url or engine_archive_url(), archive)
        binary = extract_engine(archive, Path(temporary) / "contents")
        metal = binary.with_name(release.METAL_LIBRARY)
        if not metal.is_file():
            raise ValueError(f"{archive}: missing {release.METAL_LIBRARY} beside {ENGINE_NAME}")
        copy2(metal, destination.with_name(release.METAL_LIBRARY))
        copy2(binary, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    return destination
