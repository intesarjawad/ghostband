"""Download GuitarSet from Zenodo, check each archive's MD5 and unpack what the evaluation reads.

Run it as `uv run python -m scripts.fetch_guitarset`, from the repository root.

GuitarSet (Xi, Bittner, Pauwels, Ye and Bello, ISMIR 2018, https://doi.org/10.5281/zenodo.3371780)
is licensed CC-BY-4.0. The annotations and the mono microphone recordings land flat in
`annotation/` and `audio/` under the cache, which defaults to
`~/Library/Caches/ghostband/guitarset`. Archives are kept beside them so a rerun checks and
reuses them instead of downloading again.
"""

import argparse
import hashlib
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ghostband.setup_models import DOWNLOAD_CHUNK_BYTES, Download, download_file

RECORD = 3371780
FILES_URL = f"https://zenodo.org/api/records/{RECORD}/files"
DEFAULT_CACHE = Path.home() / "Library/Caches/ghostband/guitarset"


@dataclass(frozen=True)
class Archive:
    """One Zenodo file with the size and MD5 the record publishes for it."""

    name: str
    size_bytes: int
    md5: str
    folder: str
    suffix: str

    def url(self, base_url: str = FILES_URL) -> str:
        return f"{base_url}/{self.name}/content"


ARCHIVES = (
    Archive(
        "annotation.zip", 39_132_574, "b39b78e63d3446f2e54ddb7a54df9b10", "annotation", ".jams"
    ),
    Archive("audio_mono-mic.zip", 656_927_981, "275966d6610ac34999b58426beb119c3", "audio", ".wav"),
)


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_verified(
    url: str, destination: Path, *, size_bytes: int, md5: str, download: Download = download_file
) -> Path:
    """Download unless a file with the published size and MD5 is already in place."""
    if destination.is_file() and md5_file(destination) == md5:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        download(url, partial)
        size = partial.stat().st_size
        if size != size_bytes:
            raise ValueError(f"{destination.name}: is {size} bytes; expected {size_bytes}")
        if md5_file(partial) != md5:
            raise ValueError(f"{destination.name}: MD5 does not match the Zenodo record")
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return destination


def extract_flat(
    archive: zipfile.ZipFile, destination: Path, accept: Callable[[str], bool]
) -> list[Path]:
    """Write accepted members as plain files in one folder, refusing names that could escape it."""
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for info in archive.infolist():
        if info.is_dir() or info.filename.startswith("__MACOSX/") or not accept(info.filename):
            continue
        if Path(info.filename).name != info.filename:
            raise ValueError(f"{info.filename}: archive member is not a plain file name")
        target = destination / info.filename
        if target.is_file() and target.stat().st_size == info.file_size:
            written.append(target)
            continue
        partial = target.with_name(target.name + ".part")
        with archive.open(info) as source, partial.open("wb") as output:
            while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
                output.write(chunk)
        partial.replace(target)
        written.append(target)
    return written


def _ends_with(suffix: str) -> Callable[[str], bool]:
    return lambda name: name.endswith(suffix)


def fetch(
    cache: Path, *, base_url: str = FILES_URL, download: Download = download_file
) -> dict[str, int]:
    """Fetch and unpack every archive, returning how many files each folder holds."""
    counts: dict[str, int] = {}
    for archive in ARCHIVES:
        path = fetch_verified(
            archive.url(base_url),
            cache / "archives" / archive.name,
            size_bytes=archive.size_bytes,
            md5=archive.md5,
            download=download,
        )
        with zipfile.ZipFile(path) as opened:
            files = extract_flat(opened, cache / archive.folder, _ends_with(archive.suffix))
        counts[archive.folder] = len(files)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    for folder, count in fetch(args.cache).items():
        sys.stdout.write(f"{args.cache / folder}: {count} files\n")


if __name__ == "__main__":
    main()
