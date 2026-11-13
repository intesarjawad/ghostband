"""Export one recorded take, scored, for the Pages roll.

Run it as `uv run python scripts/export_pages_take.py TAKE.take.json --out ui/public/takes
--pool TAKES...` from the repository root.

It writes `<take>.json` and `<take>.mp3` into the output folder. The JSON holds the chart,
the scheduler's beat-to-second log, each chord change with the time the detector found it,
the chord heard on every beat, the take's score and the take's loudness in three registers
(30 to 180 Hz, 180 to 2000 Hz, 2000 to 12000 Hz) at 100 frames a second, on a
30 dB scale. The scoring is
`scripts/measure_sync.py`'s, unchanged. With `--pool`, it also scores those takes and
writes how many of their changes landed within 120 ms.
"""

import argparse
import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
from ghostband.chords import NoChord, parse_chord
from ghostband.evaluation import chord_detector
from ghostband.importing import read_chart
from ghostband.setup_models import sha256_file
from numpy.typing import NDArray

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_sync import (
    TOLERANCE_S,
    TakeFile,
    Timeline,
    audio_frames,
    chart_symbol,
    chord_changes,
    counted_beats,
    score_changes,
)

sf = import_module("soundfile")
ROOT = Path(__file__).resolve().parents[1]
ENVELOPE_RATE = 100
RANGE_DB = 30.0
REGISTERS = {"low": (30.0, 180.0), "mid": (180.0, 2000.0), "high": (2000.0, 12000.0)}
# Recorder fields that measure_sync.py does not read.
SETTINGS = ("tempo_factor", "onset_mode", "condition", "slider")


def read_take(path: Path, *, diagnostic: bool = False) -> tuple[TakeFile, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    settings = {key: raw.pop(key) for key in SETTINGS if key in raw}
    take = TakeFile.model_validate(raw)
    if (
        take.mode != "live"
        or take.dropped_frames not in (None, 0)
        or (
            not diagnostic
            and (raw.get("mode") != "live" or take.dropped_frames != 0 or take.provenance is None)
        )
    ):
        raise ValueError(
            f"{path}: export requires live mode, dropped_frames=0 and provenance; record a new take"
        )
    if take.provenance is not None:
        take.provenance.validate_files(chart_path(take), path.parent / take.recording)
    settings["mode"] = raw.get("mode", "unknown")
    return take, settings


def chart_path(take: TakeFile) -> Path:
    path = Path(take.chart)
    return path if path.is_absolute() else ROOT / path


def envelopes(mono: NDArray[np.float32], rate: int) -> dict[str, list[float]]:
    """Loudness per register, one frame per 1/ENVELOPE_RATE s, from 0 at -30 dB to 1 at loud.

    Loud is the register's own 98th percentile, so one spike does not dim the rest of the take.
    """
    spectrum = np.fft.rfft(mono)
    frequencies = np.fft.rfftfreq(mono.size, 1 / rate)
    hop = rate // ENVELOPE_RATE
    frames = mono.size // hop
    out: dict[str, list[float]] = {}
    for name, (low, high) in REGISTERS.items():
        band = np.fft.irfft(
            np.where((frequencies >= low) & (frequencies < high), spectrum, 0), mono.size
        )
        rms = np.sqrt((band[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1))
        loud = float(np.percentile(rms, 98))
        level = 20 * np.log10(np.maximum(rms, 1e-12) / max(loud, 1e-12))
        out[name] = np.round(np.clip(1 + level / RANGE_DB, 0, 1), 3).tolist()
    return out


def chord_tones(symbol: str) -> dict[str, Any]:
    chord = parse_chord(symbol)
    if isinstance(chord, NoChord):
        return {"bass": None, "pcs": []}
    return {"bass": chord.bass_pitch_class, "pcs": list(chord.pitch_classes)}


def provenance(path: Path, take: TakeFile) -> dict[str, Any]:
    if take.provenance is not None:
        return take.provenance.model_dump()
    return {
        "engine": None,
        "ghostband_version": None,
        "model_revision": None,
        "chart_sha256": sha256_file(chart_path(take)),
        "recording_sha256": sha256_file(path.parent / take.recording),
    }


def export(path: Path, *, diagnostic: bool = False) -> dict[str, Any]:
    take, settings = read_take(path, diagnostic=diagnostic)
    chart = read_chart(chart_path(take))
    timeline = Timeline.from_log(take.beat_audible, take.play_time_s)
    recording = path.parent / take.recording
    times, chroma = audio_frames(recording)
    changes = chord_changes(chart).items
    score = score_changes(times, chroma, timeline, changes)

    found: list[dict[str, float | None]] = []
    for change in changes:
        chart_s = timeline.at(change.beat)
        heard_s = (
            None
            if chart_s is None
            else chord_detector.change_time(times, chroma, change.before, change.after, chart_s)
        )
        found.append({"beat": change.beat, "chart_s": chart_s, "found_s": heard_s})

    heard: list[dict[str, Any]] = []
    for beat in counted_beats(timeline, chart):
        start_s, end_s = timeline.at(beat), timeline.at(beat + 1)
        symbol = chart_symbol(chart, beat)
        if start_s is None or end_s is None or end_s > times[-1] or symbol is None:
            continue
        guess = chord_detector.beat_window(times, chroma, start_s, end_s)
        heard.append(
            {
                "beat": beat,
                "expected": chord_detector.majmin(symbol),
                "heard": None if guess is None else guess.symbol,
            }
        )

    audio, rate = sf.read(str(recording), dtype="float32", always_2d=True)
    mono = np.asarray(audio, dtype=np.float32).mean(axis=1)
    errors = [abs(error) for error in score.errors_s]
    compared = [row for row in heard if row["expected"] != chord_detector.NO_CHORD and row["heard"]]
    return {
        "chart": {
            "title": chart.title,
            "tempo_bpm": chart.tempo_bpm,
            "beats_per_bar": chart.beats_per_bar,
            "key": chart.key,
            "sections": [section.model_dump() for section in chart.sections],
            "chords": [c.model_dump() | chord_tones(c.symbol) for c in chart.chords],
            "melody": [note.model_dump() for note in chart.melody],
        },
        "take": {
            "id": path.name.removesuffix(".take.json"),
            "preset": take.preset,
            "run": take.run,
            "mode": take.mode,
            "dropped_frames": take.dropped_frames,
            "provenance": provenance(path, take),
            "validation": "diagnostic" if diagnostic else "validated",
            **settings,
            "seconds": round(mono.size / rate, 3),
        },
        "beats": [
            [float(b), round(float(s), 4)]
            for b, s in zip(timeline.beats, timeline.seconds, strict=True)
        ],
        "changes": found,
        "heard": heard,
        "score": {
            "eligible": score.eligible,
            "misses": score.misses,
            "within_120": sum(error <= TOLERANCE_S for error in errors),
            "median_ms": round(float(np.median(errors)) * 1000, 1) if errors else None,
            "heard_agreed": sum(row["heard"] == row["expected"] for row in compared),
            "heard_compared": len(compared),
        },
        "envelope_rate": ENVELOPE_RATE,
        "envelope": envelopes(mono, rate),
    }


def pooled(paths: list[Path], *, diagnostic: bool = False) -> dict[str, Any]:
    eligible = within = 0
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for path in paths:
        take, _ = read_take(path, diagnostic=diagnostic)
        identity = (Path(take.chart).name, take.preset, take.run)
        if identity in seen:
            raise ValueError(f"duplicate pool take: {identity}")
        seen.add(identity)
        chart = read_chart(chart_path(take))
        timeline = Timeline.from_log(take.beat_audible, take.play_time_s)
        times, chroma = audio_frames(path.parent / take.recording)
        score = score_changes(times, chroma, timeline, chord_changes(chart).items)
        eligible += score.eligible
        within += sum(abs(error) <= TOLERANCE_S for error in score.errors_s)
        sources.append(
            {
                "name": path.name,
                "sidecar": json.loads(path.read_text(encoding="utf-8")),
                "sidecar_sha256": sha256_file(path),
                "provenance": provenance(path, take),
                "eligible": score.eligible,
                "within_120": sum(abs(error) <= TOLERANCE_S for error in score.errors_s),
            }
        )
    return {
        "takes": len(paths),
        "eligible": eligible,
        "within_120": within,
        "validation": "diagnostic" if diagnostic else "validated",
        "sources": sources,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("take", type=Path, help="the take's .take.json sidecar")
    parser.add_argument("--out", type=Path, required=True, help="folder for <take>.json and .mp3")
    parser.add_argument("--pool", type=Path, nargs="*", default=[], help="takes to pool")
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="score archived takes with unknown capture metadata; cannot establish acceptance",
    )
    args = parser.parse_args()
    data = export(args.take, diagnostic=args.diagnostic)
    if args.pool:
        data["pool"] = pooled(args.pool, diagnostic=args.diagnostic)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / data["take"]["id"]
    take, _ = read_take(args.take, diagnostic=args.diagnostic)
    recording = args.take.parent / take.recording
    mp3 = stem.with_suffix(".mp3")
    encode = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(recording),
        "-ac",
        "2",
        "-b:a",
        "128k",
    ]
    subprocess.run([*encode, "-map_metadata", "-1", str(mp3)], check=True)  # noqa: S603 - fixed executable
    data["audio"] = {"file": mp3.name, "sha256": sha256_file(mp3)}
    stem.with_suffix(".json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {stem}.json and {stem}.mp3", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
