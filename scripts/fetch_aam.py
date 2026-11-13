"""Download the AAM subset the extraction evaluation reads, with locked checksums.

Run it as `uv run python -m scripts.fetch_aam`, from the repository root.

AAM (Ostermann and Vatolkin, https://doi.org/10.5281/zenodo.5794629) is licensed CC-BY-4.0.
The annotation and MIDI archives are small, so they are downloaded whole and checked against the
MD5 the Zenodo API lists. The 15 GB mix archive is not: its central directory is read with HTTP
range requests, 200 track numbers are drawn with a fixed seed, and only those members are
extracted. Zenodo lists no checksum for a single mix, so `--write-lock` records each extracted
mix's MD5 once in `docs/benchmarks/aam-subset.json`, and every later run checks against it.

Files land in `annotations/`, `midi/` and `mixes/` under the cache, which defaults to
`~/Library/Caches/ghostband/aam`.
"""

import argparse
import hashlib
import io
import json
import random
import sys
import zipfile
from collections.abc import Buffer, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from ghostband.setup_models import DOWNLOAD_CHUNK_BYTES, DOWNLOAD_TIMEOUT_S, Download, download_file

from scripts.fetch_guitarset import extract_flat, fetch_verified, md5_file

ROOT = Path(__file__).resolve().parents[1]
RECORD = 5794629
API_URL = f"https://zenodo.org/api/records/{RECORD}"
DEFAULT_CACHE = Path.home() / "Library/Caches/ghostband/aam"
LOCK_PATH = ROOT / "docs/benchmarks/aam-subset.json"
SEED = 20261104
POOL = 1000
TRACK_COUNT = 200
ANNOTATIONS = "0001-1000-annotations-v1.1.0.zip"
MIDIS = "0001-1000-midis.zip"
MIXES = "0001-1000-audio-mixes.zip"
ANNOTATION_KINDS = ("beatinfo", "segments")
# Mixes are stored, not compressed, so one request of this size serves many zipfile reads.
BLOCK_BYTES = 4 * 1024 * 1024

RangeFetch = Callable[[int, int], bytes]


@dataclass(frozen=True)
class ZenodoFile:
    """One file of the record: its size, MD5 and download address."""

    name: str
    size_bytes: int
    md5: str
    url: str


def track_numbers() -> list[int]:
    """The 200 sampled track numbers, ascending; the seed fixes them for every run."""
    # A reproducible sample, not a secret.
    return sorted(random.Random(SEED).sample(range(1, POOL + 1), TRACK_COUNT))  # noqa: S311


def zenodo_files(api_url: str = API_URL) -> dict[str, ZenodoFile]:
    """List the record's files from the Zenodo API."""
    request = Request(api_url, headers={"User-Agent": "ghostband"})  # noqa: S310
    with urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310
        record = json.load(response)
    files: dict[str, ZenodoFile] = {}
    for entry in record["files"]:
        algorithm, _, digest = str(entry["checksum"]).partition(":")
        if algorithm != "md5":
            raise ValueError(f"{entry['key']}: expected an md5 checksum, got {algorithm}")
        files[entry["key"]] = ZenodoFile(
            entry["key"], int(entry["size"]), digest, entry["links"]["self"]
        )
    return files


def http_ranges(url: str) -> RangeFetch:
    """Read byte ranges of one URL; a server that ignores Range is an error, not a full download."""

    def fetch(offset: int, length: int) -> bytes:
        request = Request(  # noqa: S310
            url,
            headers={"Range": f"bytes={offset}-{offset + length - 1}", "User-Agent": "ghostband"},
        )
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310
            if response.status != 206:
                raise ValueError(f"{url}: the server ignored the range request")
            data: bytes = response.read()
        if len(data) != length:
            raise ValueError(f"{url}: asked for {length} bytes at {offset}, got {len(data)}")
        return data

    return fetch


class RangeFile(io.RawIOBase):
    """A read-only seekable view of a remote file, filled by range requests."""

    def __init__(self, fetch: RangeFetch, size: int) -> None:
        super().__init__()
        self._fetch = fetch
        self._size = size
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._size}[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self._position = base + offset
        return self._position

    def readinto(self, buffer: Buffer) -> int:
        view = memoryview(buffer).cast("B")
        count = min(len(view), self._size - self._position)
        if count <= 0:
            return 0
        view[:count] = self._fetch(self._position, count)
        self._position += count
        return count


def open_remote_zip(fetch: RangeFetch, size: int) -> zipfile.ZipFile:
    """Open a remote archive; only its central directory is read until a member is extracted."""
    return zipfile.ZipFile(io.BufferedReader(RangeFile(fetch, size), buffer_size=BLOCK_BYTES))


def extract_member(archive: zipfile.ZipFile, name: str, destination: Path) -> tuple[str, int]:
    """Copy one member to a path atomically and return its MD5 and size.

    zipfile checks the member's CRC-32 as the last bytes are read, so a damaged copy raises.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    try:
        with archive.open(name) as source, partial.open("wb") as output:
            while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
                digest.update(chunk)
                output.write(chunk)
                size += len(chunk)
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def mix_name(track: int) -> str:
    return f"{track:04d}_mix.flac"


def read_lock(path: Path, files: dict[str, ZenodoFile]) -> dict[str, dict[str, object]]:
    """Load the committed lock and refuse one that no longer describes this sample or record."""
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock["seed"] != SEED or lock["tracks"] != track_numbers():
        raise ValueError(f"{path}: the locked tracks are not this seed's sample")
    for name in (ANNOTATIONS, MIDIS, MIXES):
        if lock["archives"][name]["md5"] != files[name].md5:
            raise ValueError(f"{path}: {name} changed on Zenodo since the lock was written")
    mixes: dict[str, dict[str, object]] = lock["mixes"]
    return mixes


def write_lock(
    path: Path, files: dict[str, ZenodoFile], mixes: dict[str, dict[str, object]]
) -> None:
    lock = {
        "record": RECORD,
        "seed": SEED,
        "tracks": track_numbers(),
        "archives": {
            name: {"md5": files[name].md5, "size": files[name].size_bytes}
            for name in (ANNOTATIONS, MIDIS, MIXES)
        },
        "mixes": mixes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Transport:
    """Where the record and its bytes come from; tests point it at a local server."""

    api_url: str = API_URL
    download: Download = download_file
    ranges: Callable[[str], RangeFetch] = http_ranges


def fetch_small(
    files: dict[str, ZenodoFile], cache: Path, tracks: list[int], download: Download
) -> None:
    """Download the annotation and MIDI archives and unpack the sampled tracks' files."""
    prefixes = {f"{track:04d}_" for track in tracks}
    annotation_names = {f"{prefix}{kind}.arff" for prefix in prefixes for kind in ANNOTATION_KINDS}

    def is_midi(name: str) -> bool:
        return name.endswith(".mid") and name.split("_", 1)[0] + "_" in prefixes

    def is_annotation(name: str) -> bool:
        return name in annotation_names

    for archive_name, folder, accept in (
        (ANNOTATIONS, "annotations", is_annotation),
        (MIDIS, "midi", is_midi),
    ):
        entry = files[archive_name]
        path = fetch_verified(
            entry.url,
            cache / "archives" / entry.name,
            size_bytes=entry.size_bytes,
            md5=entry.md5,
            download=download,
        )
        with zipfile.ZipFile(path) as opened:
            extract_flat(opened, cache / folder, accept)


def fetch_mixes(
    files: dict[str, ZenodoFile],
    cache: Path,
    tracks: list[int],
    lock: dict[str, dict[str, object]] | None,
    fetch: RangeFetch,
) -> dict[str, dict[str, object]]:
    """Extract the sampled mixes, checking each against the lock when there is one."""
    recorded: dict[str, dict[str, object]] = {}
    with open_remote_zip(fetch, files[MIXES].size_bytes) as remote:
        missing = [mix_name(track) for track in tracks if mix_name(track) not in remote.namelist()]
        if missing:
            raise ValueError(f"{MIXES} has no {', '.join(missing[:3])}")
        for track in tracks:
            name = mix_name(track)
            destination = cache / "mixes" / name
            locked = lock[name] if lock is not None else None
            if locked and destination.is_file() and md5_file(destination) == locked["md5"]:
                recorded[name] = locked
                continue
            digest, size = extract_member(remote, name, destination)
            if locked and digest != locked["md5"]:
                destination.unlink()
                raise ValueError(f"{name}: MD5 {digest} does not match the lock")
            recorded[name] = {"md5": digest, "size": size}
    return recorded


def fetch_subset(
    cache: Path,
    *,
    lock_path: Path = LOCK_PATH,
    lock_new: bool = False,
    transport: Transport = Transport(),  # noqa: B008
) -> int:
    """Fetch the whole subset and return how many mixes it holds."""
    if lock_new and lock_path.exists():
        raise ValueError(f"{lock_path} exists; delete it first to write a new lock")
    if not lock_new and not lock_path.is_file():
        raise ValueError(f"{lock_path} is missing; run once with --write-lock to create it")
    files = zenodo_files(transport.api_url)
    lock = None if lock_new else read_lock(lock_path, files)
    tracks = track_numbers()
    fetch_small(files, cache, tracks, transport.download)
    mixes = fetch_mixes(files, cache, tracks, lock, transport.ranges(files[MIXES].url))
    if lock_new:
        write_lock(lock_path, files, mixes)
    return len(mixes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--write-lock",
        action="store_true",
        help="record each extracted mix's MD5 in the lock file; the lock must not exist yet",
    )
    args = parser.parse_args()
    count = fetch_subset(args.cache, lock_new=args.write_lock)
    sys.stdout.write(f"{args.cache}: {count} mixes verified\n")


if __name__ == "__main__":
    main()
