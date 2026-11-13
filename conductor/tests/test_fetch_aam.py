import hashlib
import io
import json
import zipfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from scripts import fetch_aam
from scripts.fetch_aam import (
    ANNOTATIONS,
    MIDIS,
    MIXES,
    RangeFile,
    Transport,
    ZenodoFile,
    extract_member,
    fetch_subset,
    http_ranges,
    open_remote_zip,
    track_numbers,
    zenodo_files,
)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # noqa: S324


def stored_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@contextmanager
def serve(
    body: bytes, *, ranges: bool, content_type: str = "application/octet-stream"
) -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            header = self.headers.get("Range")
            if ranges and header:
                first, last = header.removeprefix("bytes=").split("-")
                chunk = body[int(first) : int(last) + 1]
                self.send_response(206)
            else:
                chunk = body
                self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/file"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_the_sample_is_two_hundred_distinct_tracks_from_a_fixed_seed() -> None:
    tracks = track_numbers()
    assert len(tracks) == len(set(tracks)) == 200
    assert tracks == sorted(tracks)
    # Drawn once with random.Random(20261104).sample(range(1, 1001), 200).
    assert tracks[:6] == [2, 6, 7, 11, 13, 20]
    assert tracks[-4:] == [974, 987, 988, 999]
    assert sum(tracks) == 101_769


def test_a_remote_zip_is_read_through_ranges_without_the_whole_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_aam, "BLOCK_BYTES", 64 * 1024)
    members = {f"{n:04d}_mix.flac": bytes([n]) * 300_000 for n in range(1, 21)}
    data = stored_zip(members)
    asked: list[tuple[int, int]] = []

    def fetch(offset: int, length: int) -> bytes:
        asked.append((offset, length))
        return data[offset : offset + length]

    with open_remote_zip(fetch, len(data)) as remote:
        assert remote.namelist() == list(members)
        directory_reads = len(asked)
        assert sum(length for _, length in asked) < 0.1 * len(data)
        with remote.open("0007_mix.flac") as source:
            assert source.read() == members["0007_mix.flac"]
    assert len(asked) > directory_reads
    assert sum(length for _, length in asked) < 0.2 * len(data)


def test_range_file_seeks_and_stops_at_the_end() -> None:
    data = bytes(range(100))
    remote = RangeFile(lambda offset, length: data[offset : offset + length], len(data))
    assert remote.seek(-10, 2) == 90
    assert remote.read() == data[90:]
    assert remote.seek(5) == 5
    assert remote.seek(5, 1) == 10
    assert remote.read(3) == data[10:13]
    assert remote.tell() == 13
    assert remote.read(1000) == data[13:]
    assert remote.read(1) == b""
    with pytest.raises(ValueError, match="negative seek"):
        remote.seek(-1)


def test_http_ranges_asks_for_bytes_and_rejects_a_server_that_ignores_them() -> None:
    data = bytes(range(200))
    with serve(data, ranges=True) as url:
        assert http_ranges(url)(10, 5) == data[10:15]
    with serve(data, ranges=False) as url, pytest.raises(ValueError, match="ignored the range"):
        http_ranges(url)(10, 5)


def test_zenodo_files_read_md5_and_size() -> None:
    record = {
        "files": [
            {
                "key": "x.zip",
                "size": 12,
                "checksum": "md5:" + "a" * 32,
                "links": {"self": "https://zenodo.test/x.zip/content"},
            }
        ]
    }
    with serve(json.dumps(record).encode(), ranges=False) as url:
        files = zenodo_files(url)
    assert files == {
        "x.zip": ZenodoFile("x.zip", 12, "a" * 32, "https://zenodo.test/x.zip/content")
    }
    record["files"][0]["checksum"] = "sha256:abc"  # type: ignore[index]
    with (
        serve(json.dumps(record).encode(), ranges=False) as url,
        pytest.raises(ValueError, match="expected an md5"),
    ):
        zenodo_files(url)


def test_extract_member_reports_md5_and_refuses_a_damaged_member(tmp_path: Path) -> None:
    payload = b"flac" * 1000
    data = bytearray(stored_zip({"0001_mix.flac": payload}))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        assert extract_member(archive, "0001_mix.flac", tmp_path / "m" / "0001_mix.flac") == (
            md5(payload),
            4000,
        )
    data[data.index(payload) + 10] ^= 0xFF
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive, pytest.raises(zipfile.BadZipFile):
        extract_member(archive, "0001_mix.flac", tmp_path / "m" / "bad.flac")
    assert sorted(path.name for path in (tmp_path / "m").iterdir()) == ["0001_mix.flac"]


class Record:
    """A fake Zenodo record: three archives for the sampled tracks plus tracks left out."""

    def __init__(self) -> None:
        sampled = track_numbers()
        decoy = next(n for n in range(1, 1001) if n not in sampled)
        everything = [*sampled, decoy]
        self.decoy = decoy
        self.mix_bytes = {f"{n:04d}_mix.flac": f"mix{n}".encode() * 50 for n in everything}
        self.archives = {
            ANNOTATIONS: stored_zip(
                {
                    f"{n:04d}_{kind}.arff": f"{kind} {n}\n".encode()
                    for n in everything
                    for kind in ("beatinfo", "onsets", "segments")
                }
            ),
            MIDIS: stored_zip(
                {
                    f"{n:04d}_{name}.mid": f"{name}{n}".encode()
                    for n in everything
                    for name in ("Flute", "Drums")
                }
            ),
            MIXES: stored_zip(self.mix_bytes),
        }

    def api(self) -> bytes:
        return json.dumps(
            {
                "files": [
                    {
                        "key": name,
                        "size": len(data),
                        "checksum": "md5:" + md5(data),
                        "links": {"self": f"fake://{name}"},
                    }
                    for name, data in self.archives.items()
                ]
            }
        ).encode()

    def transport(self, api_url: str) -> Transport:
        def download(url: str, target: Path) -> None:
            target.write_bytes(self.archives[url.removeprefix("fake://")])

        def ranges(url: str) -> Callable[[int, int], bytes]:
            data = self.archives[url.removeprefix("fake://")]
            return lambda offset, length: data[offset : offset + length]

        return Transport(api_url=api_url, download=download, ranges=ranges)


def test_subset_is_fetched_locked_and_checked_again(tmp_path: Path) -> None:
    record = Record()
    lock = tmp_path / "lock" / "aam-subset.json"
    cache = tmp_path / "cache"
    with serve(record.api(), ranges=False) as api:
        transport = record.transport(api)
        assert fetch_subset(cache, lock_path=lock, lock_new=True, transport=transport) == 200
        written = json.loads(lock.read_text())
        assert written["tracks"] == track_numbers()
        assert written["seed"] == 20261104
        first = "0002_mix.flac"
        assert written["mixes"][first] == {
            "md5": md5(record.mix_bytes[first]),
            "size": len(record.mix_bytes[first]),
        }
        assert written["archives"][MIXES]["md5"] == md5(record.archives[MIXES])
        assert (cache / "mixes" / first).read_bytes() == record.mix_bytes[first]
        assert len(list((cache / "mixes").iterdir())) == 200
        assert not (cache / "mixes" / f"{record.decoy:04d}_mix.flac").exists()
        assert len(list((cache / "annotations").iterdir())) == 400
        assert not (cache / "annotations" / "0002_onsets.arff").exists()
        assert len(list((cache / "midi").iterdir())) == 400
        assert fetch_subset(cache, lock_path=lock, transport=transport) == 200
        # A mix that no longer matches the lock is refused, and removed when freshly extracted.
        (cache / "mixes" / first).unlink()
        bad = json.loads(lock.read_text())
        bad["mixes"][first]["md5"] = "0" * 32
        lock.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="does not match the lock"):
            fetch_subset(cache, lock_path=lock, transport=transport)
        assert not (cache / "mixes" / first).exists()


def test_lock_rules(tmp_path: Path) -> None:
    record = Record()
    lock = tmp_path / "aam-subset.json"
    with serve(record.api(), ranges=False) as api:
        transport = record.transport(api)
        with pytest.raises(ValueError, match="run once with --write-lock"):
            fetch_subset(tmp_path / "cache", lock_path=lock, transport=transport)
        fetch_subset(tmp_path / "cache", lock_path=lock, lock_new=True, transport=transport)
        with pytest.raises(ValueError, match="delete it first"):
            fetch_subset(tmp_path / "cache", lock_path=lock, lock_new=True, transport=transport)
        original = json.loads(lock.read_text())
        for field, value, message in (
            ("seed", 1, "not this seed's sample"),
            ("tracks", [1, 2, 3], "not this seed's sample"),
        ):
            lock.write_text(json.dumps({**original, field: value}))
            with pytest.raises(ValueError, match=message):
                fetch_subset(tmp_path / "cache", lock_path=lock, transport=transport)
        original["archives"][MIDIS]["md5"] = "f" * 32
        lock.write_text(json.dumps(original))
        with pytest.raises(ValueError, match="changed on Zenodo"):
            fetch_subset(tmp_path / "cache", lock_path=lock, transport=transport)


def test_a_mix_missing_from_the_archive_is_reported(tmp_path: Path) -> None:
    record = Record()
    first = f"{track_numbers()[0]:04d}_mix.flac"
    del record.mix_bytes[first]
    record.archives[MIXES] = stored_zip(record.mix_bytes)
    with serve(record.api(), ranges=False) as api, pytest.raises(ValueError, match=first):
        fetch_subset(
            tmp_path / "cache",
            lock_path=tmp_path / "lock.json",
            lock_new=True,
            transport=record.transport(api),
        )
