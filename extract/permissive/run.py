"""Offline beat, chord and key extraction with permissively licensed models."""

import argparse
import json
import os
import sys
from contextlib import redirect_stdout
from importlib import import_module
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np
from chords import FloatArray, beat_average, decode_chords, estimate_key, fold_salience, normalize
from melody import Note, beat_positions, extract_melody
from numpy.typing import NDArray
from sections import find_sections

if TYPE_CHECKING:
    from basic_pitch.inference import Model

SAMPLE_RATE = 22050
CHROMA_HOP = 512


class _BeatModel(Protocol):
    def __call__(self, filename: str) -> tuple[FloatArray, FloatArray]: ...


def read_audio(wav_path: Path) -> tuple[FloatArray, float]:
    """Read finite mono audio and resample to the feature extraction rate."""
    soundfile = import_module("soundfile")
    audio: FloatArray
    rate: int
    audio, rate = soundfile.read(str(wav_path), dtype="float64", always_2d=True)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Audio must be nonempty and finite")
    duration_s = len(audio) / rate
    mono = audio.mean(axis=1)
    if rate != SAMPLE_RATE:
        librosa = import_module("librosa")
        mono = librosa.resample(mono, orig_sr=rate, target_sr=SAMPLE_RATE)
    return np.asarray(mono, dtype=np.float64), duration_s


def extract_beats(wav_path: Path, duration_s: float) -> tuple[FloatArray, FloatArray]:
    """Use a local Beat This checkpoint without DBN or implicit downloads."""
    default = Path(__file__).resolve().parents[2] / "models/beat-this/final0.ckpt"
    checkpoint = Path(os.environ.get("GHOSTBAND_BEAT_THIS_CHECKPOINT", str(default))).resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(
            "Set GHOSTBAND_BEAT_THIS_CHECKPOINT to a local Beat This final0 checkpoint"
        )
    inference = import_module("beat_this.inference")
    model: _BeatModel = inference.File2Beats(
        checkpoint_path=str(checkpoint), device="cpu", float16=False, dbn=False
    )
    beats, downbeats = model(str(wav_path))
    beats = np.asarray(beats, dtype=np.float64)
    downbeats = np.asarray(downbeats, dtype=np.float64)
    for times in (beats, downbeats):
        if (
            times.ndim != 1
            or not np.isfinite(times).all()
            or (times < 0).any()
            or (times >= duration_s).any()
            or ((times[1:] - times[:-1]) <= 0).any()
        ):
            raise ValueError("Beat timestamps must increase within the audio duration")
    if len(beats) < 2 or len(downbeats) < 2:
        raise ValueError("Two beats and two downbeats are required to infer tempo and meter")
    if any(float(np.min(np.abs(beats - time))) > 1e-3 for time in downbeats):
        raise ValueError("Downbeats must coincide with detected beats")
    return beats, downbeats


def extract_notes(wav_path: Path) -> tuple[FloatArray, list[Note]]:
    """Read piano-note salience and note events from the bundled Basic Pitch model."""
    inference = import_module("basic_pitch.inference")
    constants = import_module("basic_pitch.constants")
    model_type: type[Model] = inference.Model
    model_path: Path = import_module("basic_pitch").ICASSP_2022_MODEL_PATH
    frames: int = constants.ANNOT_N_FRAMES
    note_bins: int = constants.N_FREQ_BINS_NOTES
    contour_bins: int = constants.N_FREQ_BINS_CONTOURS

    class SilentSafeModel(model_type):
        def predict(self, x: NDArray[np.float32]) -> dict[str, NDArray[np.float32]]:
            # CoreML produces NaNs on entirely silent windows.
            if not x.any():
                return {
                    name: np.zeros((len(x), frames, bins), dtype=np.float32)
                    for name, bins in (
                        ("note", note_bins),
                        ("onset", note_bins),
                        ("contour", contour_bins),
                    )
                }
            return super().predict(x)

    output: dict[str, FloatArray]
    events: list[tuple[float, float, int, float, object]]
    output, _, events = inference.predict(
        str(wav_path), model_or_model_path=SilentSafeModel(model_path)
    )
    salience = np.asarray(output["note"], dtype=np.float64)
    fold_salience(salience)
    notes = [
        (float(start), float(end), int(pitch), float(amp)) for start, end, pitch, amp, _ in events
    ]
    return salience, notes


def build_chart(
    wav_path: Path,
    rhythm: tuple[FloatArray, FloatArray],
    duration_s: float,
    symbols: list[str],
    key: str,
) -> dict[str, object]:
    """Express detected intervals in musician-counted beats, preserving the final partial beat."""
    beats, downbeats = rhythm
    period_s = float(np.median(beats[1:] - beats[:-1]))
    duration_beats = len(beats) - 1 + (duration_s - float(beats[-1])) / period_s
    downbeat_indices = [int(np.abs(beats - time).argmin()) for time in downbeats]
    meter = round(float(np.median([end - start for start, end in pairwise(downbeat_indices)])))
    if meter <= 0 or len(symbols) != len(beats):
        raise ValueError("Meter must be positive and chords must align with beats")
    chords: list[dict[str, object]] = []
    for index, symbol in enumerate(symbols):
        end = float(index + 1) if index < len(symbols) - 1 else duration_beats
        if chords and chords[-1]["symbol"] == symbol:
            chords[-1]["end_beat"] = end
        else:
            chords.append({"start_beat": float(index), "end_beat": end, "symbol": symbol})
    return {
        "schema_version": 1,
        "title": wav_path.stem,
        "source": wav_path.name,
        "rights": "unknown",
        "extractor": "permissive",
        "tempo_bpm": 60 / period_s,
        "beat_times_s": beats.tolist(),
        "time_signature": [meter, 4],
        "key": key,
        "sections": [{"name": "Song", "start_beat": 0.0, "end_beat": duration_beats}],
        "chords": chords,
        "melody": [],
        "duration_beats": duration_beats,
    }


def extract_chart(wav_path: Path, audio: FloatArray, duration_s: float) -> dict[str, object]:
    """Fuse beat-averaged CQT chroma and note salience, then add sections and a skyline melody."""
    beats, downbeats = extract_beats(wav_path, duration_s)
    salience, notes = extract_notes(wav_path)
    librosa = import_module("librosa")
    note_creation = import_module("basic_pitch.note_creation")
    chroma: FloatArray = np.asarray(
        librosa.feature.chroma_cqt(y=audio, sr=SAMPLE_RATE, hop_length=CHROMA_HOP),
        dtype=np.float64,
    ).T
    edges = np.empty(len(beats) + 1, dtype=np.float64)
    edges[:-1] = beats
    edges[-1] = duration_s
    chroma_times = np.arange(len(chroma), dtype=np.float64) * CHROMA_HOP / SAMPLE_RATE
    note_times: FloatArray = note_creation.model_frames_to_time(len(salience))
    cqt_beats = beat_average(chroma, chroma_times, edges)
    note_beats = beat_average(fold_salience(salience), note_times, edges)
    features = 0.5 * normalize(cqt_beats) + 0.5 * normalize(note_beats)
    sample_edges = np.minimum((edges * SAMPLE_RATE).astype(np.int64), len(audio))
    rms = np.array(
        [
            np.sqrt(np.mean(audio[start:end] ** 2)) if end > start else 0.0
            for start, end in pairwise(sample_edges)
        ],
        dtype=np.float64,
    )
    duration_beats = float(beat_positions(np.array([duration_s]), beats)[0])
    chart = build_chart(
        wav_path, (beats, downbeats), duration_s, decode_chords(features, rms), estimate_key(chroma)
    )
    chart["sections"] = find_sections(features, beats, downbeats, duration_beats)
    chart["melody"] = extract_melody(notes, beats, duration_s)
    return chart


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("chart", "beats", "notes"))
    parser.add_argument("wav", type=Path)
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            audio, duration_s = read_audio(args.wav)
            result: dict[str, object]
            if args.command == "chart":
                result = extract_chart(args.wav, audio, duration_s)
            elif args.command == "beats":
                beats, downbeats = extract_beats(args.wav, duration_s)
                result = {"beats_s": beats.tolist(), "downbeats_s": downbeats.tolist()}
            else:
                _, notes = extract_notes(args.wav)
                result = {"notes": notes}
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
