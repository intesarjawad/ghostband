import asyncio
import shutil
import wave
from pathlib import Path

import pytest
from ghostband.chart import Chart
from ghostband.errors import GhostbandError
from ghostband.importing import import_tune, read_chart, validate_file

ROOT = Path(__file__).resolve().parents[2]


def write_wav(path: Path, seconds: int, sample: bytes = b"\x01\x00") -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        for _ in range(seconds):
            audio.writeframesraw(sample * 8000)


@pytest.mark.parametrize(
    ("seconds", "sample", "code", "what"),
    [
        (5, b"\x01\x00", 10, "minimum duration is 10 s"),
        (10, b"\x00\x00", 9, "the file is silent"),
    ],
)
def test_audio_rejections_after_real_decode(
    tmp_path: Path, seconds: int, sample: bytes, code: int, what: str
) -> None:
    source = tmp_path / "source.wav"
    write_wav(source, seconds, sample)
    with pytest.raises(GhostbandError) as caught:
        asyncio.run(import_tune(source, workspace=tmp_path / "decoded"))
    assert int(caught.value.code) == code
    assert what in caught.value.message
    assert "ghostband import" in caught.value.fix


def test_size_and_magic_are_checked_before_decode(tmp_path: Path) -> None:
    huge = tmp_path / "huge.wav"
    with huge.open("wb") as output:
        output.seek(200 * 1024 * 1024)
        output.write(b"\0")
    with pytest.raises(GhostbandError) as caught:
        validate_file(huge)
    assert int(caught.value.code) == 11
    mismatch = tmp_path / "mp3-named.wav"
    mismatch.write_bytes(b"ID3" + bytes(40))
    with pytest.raises(GhostbandError) as caught:
        validate_file(mismatch)
    assert int(caught.value.code) == 12


def test_truncated_audio_reports_decoder_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "truncated.mp3"
    source.write_bytes(b"ID3" + bytes(20))
    with pytest.raises(GhostbandError) as caught:
        asyncio.run(import_tune(source, workspace=tmp_path / "decoded"))
    assert int(caught.value.code) == 4
    assert caught.value.message.startswith("Could not decode the file:")
    assert len(caught.value.detail) > 0


EXTRACTOR_CASES: list[tuple[int, bool, tuple[str, list[str]]]] = [
    (10, True, ("sheetsage2", [])),
    (361, True, ("permissive", ["long"])),
    (10, False, ("permissive", ["fallback"])),
]


@pytest.mark.parametrize(("seconds", "installed", "expected"), EXTRACTOR_CASES)
def test_offline_extractor_selection_and_decoded_rates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seconds: int,
    installed: bool,
    expected: tuple[str, list[str]],
) -> None:
    source = tmp_path / "song.wav"
    write_wav(source, seconds)
    cache = tmp_path / "cache"
    if installed:
        cache.mkdir()
    monkeypatch.setenv("GHOSTBAND_SHEETSAGE2_CACHE", str(cache))
    decoded_rates: list[int] = []
    selected: list[str] = []
    notices: list[str] = []
    progress: list[tuple[str, int]] = []
    chart = Chart.model_validate_json((ROOT / "charts/greensleeves.chart.json").read_text())

    async def decode(source_path: Path, destination: Path, rate: int) -> None:
        decoded_rates.append(rate)
        shutil.copyfile(source_path, destination)

    async def sheetsage(wav_path: Path, *, output_dir: Path, cache_dir: Path) -> Chart:
        assert wav_path.name == "audio.wav"
        assert output_dir.name == "extracted"
        assert cache_dir == cache
        selected.append("sheetsage2")
        return chart.model_copy(update={"extractor": "sheetsage2"})

    async def permissive(command: str, wav_path: Path) -> dict[str, object]:
        assert command == "chart"
        assert wav_path.name == "audio.wav"
        selected.append("permissive")
        return chart.model_copy(update={"extractor": "permissive"}).model_dump()

    monkeypatch.setattr("ghostband.importing._decode", decode)
    monkeypatch.setattr("ghostband.importing.run_sheetsage2", sheetsage)
    monkeypatch.setattr("ghostband.importing.run_permissive", permissive)
    result = asyncio.run(
        import_tune(
            source,
            workspace=tmp_path / "decoded",
            notice=notices.append,
            progress=lambda stage, pct: progress.append((stage, pct)),
        )
    )
    assert selected == [expected[0]]
    assert decoded_rates == [44100, 48000]
    assert notices == expected[1]
    assert result.rights == "unknown"
    assert result.source == "song.wav"
    assert progress == [("validate", 0), ("extract", 25), ("complete", 100)]


@pytest.mark.models
@pytest.mark.parametrize(
    "name", ["long.wav", "phone.mp3", "seven-eight.wav", "rubato.wav", "drums-only.wav"]
)
def test_real_worst_case_imports(name: str, tmp_path: Path) -> None:
    chart = asyncio.run(import_tune(ROOT / "fixtures/worst" / name, workspace=tmp_path / "decoded"))
    assert chart.duration_beats > 0
    assert chart.rights == "unknown"
    if name == "long.wav":
        assert chart.extractor == "permissive"
    elif name == "seven-eight.wav":
        assert chart.time_signature == (7, 8)
    elif name == "rubato.wav":
        intervals = [
            round(right - left, 3)
            for left, right in zip(chart.beat_times_s, chart.beat_times_s[1:], strict=False)
        ]
        assert len(set(intervals)) > 1
    elif name == "drums-only.wav":
        assert chart.melody == []
        assert all(chord.symbol == "N.C." for chord in chart.chords)


def test_invalid_chart_names_fields_once_with_one_recovery(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text('{"tempo_bpm": -10, "title": ""}')
    with pytest.raises(GhostbandError) as caught:
        read_chart(path)
    event = caught.value.event()
    assert caught.value.code.name == "INVALID_CHART"
    assert len(caught.value.detail) < 350
    assert "schema_version" in caught.value.detail
    assert "title" in caught.value.detail
    assert "more" in caught.value.detail
    assert "https://" not in caught.value.detail
    assert event["fix"] == "Fix the listed fields in the chart JSON, then import it again."
