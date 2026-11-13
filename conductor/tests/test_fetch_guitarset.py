import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from scripts import fetch_guitarset
from scripts.fetch_guitarset import Archive, extract_flat, fetch, fetch_verified, md5_file


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_md5_of_a_file(tmp_path: Path) -> None:
    path = tmp_path / "abc"
    path.write_bytes(b"abc")
    assert md5_file(path) == "900150983cd24fb0d6963f7d28e17f72"


def test_archives_are_pinned_to_the_zenodo_record() -> None:
    names = [archive.name for archive in fetch_guitarset.ARCHIVES]
    assert names == ["annotation.zip", "audio_mono-mic.zip"]
    assert fetch_guitarset.ARCHIVES[0].url() == (
        "https://zenodo.org/api/records/3371780/files/annotation.zip/content"
    )
    assert fetch_guitarset.DEFAULT_CACHE.parts[-3:] == ("Caches", "ghostband", "guitarset")


def test_download_is_verified_and_reused(tmp_path: Path) -> None:
    payload = b"guitar"
    destination = tmp_path / "a.zip"
    calls: list[str] = []

    def download(url: str, target: Path) -> None:
        calls.append(url)
        target.write_bytes(payload)

    md5 = hashlib.md5(payload).hexdigest()  # noqa: S324
    for _ in range(2):
        fetch_verified("u", destination, size_bytes=6, md5=md5, download=download)
    assert calls == ["u"]
    assert destination.read_bytes() == payload


@pytest.mark.parametrize(
    ("size", "md5", "message"),
    [(5, hashlib.md5(b"guitar").hexdigest(), "is 6 bytes"), (6, "0" * 32, "MD5 does not match")],  # noqa: S324
)
def test_a_wrong_download_is_refused_and_leaves_nothing(
    tmp_path: Path, size: int, md5: str, message: str
) -> None:
    destination = tmp_path / "a.zip"

    def download(_url: str, target: Path) -> None:
        target.write_bytes(b"guitar")

    with pytest.raises(ValueError, match=message):
        fetch_verified("u", destination, size_bytes=size, md5=md5, download=download)
    assert list(tmp_path.iterdir()) == []


def test_a_damaged_cached_archive_is_downloaded_again(tmp_path: Path) -> None:
    destination = tmp_path / "a.zip"
    destination.write_bytes(b"damaged")
    md5 = hashlib.md5(b"guitar").hexdigest()  # noqa: S324

    def download(_url: str, target: Path) -> None:
        target.write_bytes(b"guitar")

    fetch_verified("u", destination, size_bytes=6, md5=md5, download=download)
    assert destination.read_bytes() == b"guitar"


def test_only_accepted_plain_members_are_extracted(tmp_path: Path) -> None:
    data = zip_bytes(
        {"a.jams": b"one", "b.txt": b"two", "__MACOSX/._a.jams": b"junk", "c.jams": b"three"}
    )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        written = extract_flat(archive, tmp_path / "out", lambda name: name.endswith(".jams"))
    assert [path.name for path in written] == ["a.jams", "c.jams"]
    assert (tmp_path / "out" / "c.jams").read_bytes() == b"three"
    assert not (tmp_path / "out" / "b.txt").exists()


def test_members_that_could_escape_the_folder_are_refused(tmp_path: Path) -> None:
    data = zip_bytes({"../evil.jams": b"x"})
    with (
        zipfile.ZipFile(io.BytesIO(data)) as archive,
        pytest.raises(ValueError, match="not a plain file name"),
    ):
        extract_flat(archive, tmp_path / "out", lambda _name: True)
    assert not (tmp_path / "evil.jams").exists()


def test_a_member_already_extracted_is_left_alone(tmp_path: Path) -> None:
    destination = tmp_path / "out"
    destination.mkdir()
    existing = destination / "a.jams"
    existing.write_bytes(b"one")
    stamp = existing.stat().st_mtime_ns
    with zipfile.ZipFile(io.BytesIO(zip_bytes({"a.jams": b"one"}))) as archive:
        extract_flat(archive, destination, lambda _name: True)
    assert existing.stat().st_mtime_ns == stamp


def test_fetch_downloads_and_unpacks_each_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    annotation = zip_bytes({"00_A_comp.jams": b"{}", "00_A_solo.jams": b"{}", "readme.txt": b"x"})
    audio = zip_bytes({"00_A_comp_mic.wav": b"RIFF", "00_A_solo_mic.wav": b"RIFF"})
    served = {"annotation.zip": annotation, "audio_mono-mic.zip": audio}
    monkeypatch.setattr(
        fetch_guitarset,
        "ARCHIVES",
        (
            Archive(
                "annotation.zip",
                len(annotation),
                hashlib.md5(annotation).hexdigest(),  # noqa: S324
                "annotation",
                ".jams",
            ),
            Archive(
                "audio_mono-mic.zip",
                len(audio),
                hashlib.md5(audio).hexdigest(),  # noqa: S324
                "audio",
                ".wav",
            ),
        ),
    )
    urls: list[str] = []

    def download(url: str, target: Path) -> None:
        urls.append(url)
        target.write_bytes(served[url.split("/")[-2]])

    counts = fetch(tmp_path / "cache", base_url="https://example.test/files", download=download)
    assert counts == {"annotation": 2, "audio": 2}
    assert urls == [
        "https://example.test/files/annotation.zip/content",
        "https://example.test/files/audio_mono-mic.zip/content",
    ]
    assert sorted(path.name for path in (tmp_path / "cache" / "annotation").iterdir()) == [
        "00_A_comp.jams",
        "00_A_solo.jams",
    ]
    assert (
        fetch(tmp_path / "cache", base_url="https://example.test/files", download=download)
        == counts
    )
    assert len(urls) == 2
