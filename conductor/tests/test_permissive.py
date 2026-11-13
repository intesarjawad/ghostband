from __future__ import annotations

import asyncio
import json
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np
import pytest
from ghostband.chart import Chart
from ghostband.extractors import permissive
from pydantic import ValidationError

if TYPE_CHECKING:
    from numpy.typing import NDArray


def local_uv(_name: str) -> str:
    return "/local/uv"


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(sys, "path", [str(ROOT / "extract/permissive"), *sys.path])
    return SimpleNamespace(chords=import_module("chords"), runtime=import_module("run"))


def test_all_chord_templates_and_sevenths(modules: SimpleNamespace) -> None:
    symbols, vectors = modules.chords.templates()
    features = np.repeat(vectors, 8, axis=0)
    actual = modules.chords.decode_chords(features, np.ones(len(features)))
    assert actual == [symbol for symbol in symbols for _ in range(8)]


def test_silence_and_temporal_smoothing(modules: SimpleNamespace) -> None:
    symbols, vectors = modules.chords.templates()
    c = vectors[symbols.index("C")]
    g = vectors[symbols.index("G")]
    features = np.tile(c, (20, 1))
    features[10] = 0.4 * c + 0.6 * g
    assert modules.chords.decode_chords(features, np.ones(20)) == ["C"] * 20
    features[10] = 0
    assert modules.chords.decode_chords(features, np.ones(20))[10] == "N.C."
    assert modules.chords.decode_chords(np.tile(c, (2, 1)), np.zeros(2)) == ["N.C."] * 2


def test_two_beat_relative_minor_survives_smoothing(modules: SimpleNamespace) -> None:
    symbols, vectors = modules.chords.templates()
    expected = ["C"] * 8 + ["Am"] * 2 + ["C"] * 8
    features = np.array([vectors[symbols.index(symbol)] for symbol in expected])
    assert modules.chords.decode_chords(features, np.ones(len(features))) == expected


def test_chord_evidence_is_invariant_to_uniform_activation_floor(modules: SimpleNamespace) -> None:
    symbols, vectors = modules.chords.templates()
    expected = ["C"] * 8 + ["G"] * 4 + ["Am"] * 4 + ["F"] * 8
    features = np.array([vectors[symbols.index(symbol)] for symbol in expected])
    features += 1.0
    assert modules.chords.decode_chords(features, np.ones(len(features))) == expected


@pytest.mark.parametrize("root", range(12))
def test_minor_seventh_must_outscore_relative_major_triad(
    modules: SimpleNamespace, root: int
) -> None:
    symbols, vectors = modules.chords.templates()
    features = np.tile(vectors[root * 4] * np.sqrt(3), (8, 1))
    features[:, (root + 9) % 12] = 0.7
    assert modules.chords.decode_chords(features, np.ones(8)) == [symbols[root * 4]] * 8


def test_fold_salience_and_half_open_beat_average(modules: SimpleNamespace) -> None:
    notes = np.zeros((4, 88))
    notes[:, 60 - 21] = 1
    notes[:, 72 - 21] = 2
    chroma = modules.chords.fold_salience(notes)
    assert chroma[:, 0].tolist() == [3] * 4
    assert chroma.sum() == 12
    averaged = modules.chords.beat_average(
        np.arange(4, dtype=float)[:, None], np.array([0, 0.5, 1, 1.5]), np.array([0, 1, 2, 3])
    )
    assert averaged[:, 0].tolist() == [0.5, 2.5, 0]
    assert np.isfinite(modules.chords.normalize(np.zeros((2, 12)))).all()


def test_inactive_note_probabilities_do_not_accumulate_across_octaves(
    modules: SimpleNamespace,
) -> None:
    notes = np.full((2, 88), 0.1)
    notes[0, 60 - 21] = 0.8
    notes[1, 60 - 21] = 0.8
    notes[1, 72 - 21] = 0.6
    chroma = modules.chords.fold_salience(notes)
    assert chroma[:, 0].tolist() == pytest.approx([0.8, 1.4])
    assert np.count_nonzero(chroma) == 2


@pytest.mark.parametrize("root", range(12))
@pytest.mark.parametrize("minor", [False, True])
def test_key_profiles_in_every_key(modules: SimpleNamespace, root: int, minor: bool) -> None:
    chords = modules.chords
    profile = chords.MINOR_PROFILE if minor else chords.MAJOR_PROFILE
    assert chords.estimate_key(np.roll(np.array([profile]), root, axis=1)) == (
        chords.ROOTS[root] + ("m" if minor else "")
    )
    assert chords.estimate_key(np.zeros((1, 12))) == "unknown"


def test_chart_preserves_detected_grid_and_partial_final_beat(modules: SimpleNamespace) -> None:
    data = modules.runtime.build_chart(
        Path("song.wav"),
        (np.arange(0.1, 4, 0.5), np.array([0.1, 2.1])),
        4.35,
        ["C"] * 4 + ["G7"] * 4,
        "C",
    )
    chart = Chart.model_validate(data)
    assert chart.tempo_bpm == pytest.approx(120)
    assert chart.time_signature == (4, 4)
    assert chart.beat_times_s[0] == 0.1
    assert chart.duration_beats == pytest.approx(8.5)
    assert [(c.start_beat, c.end_beat, c.symbol) for c in chart.chords] == [
        (0, 4, "C"),
        (4, 8.5, "G7"),
    ]
    assert chart.rights == "unknown"
    assert chart.melody == []


def test_missing_checkpoint_never_initializes_model(
    modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GHOSTBAND_BEAT_THIS_CHECKPOINT", str(tmp_path / "missing.ckpt"))
    with pytest.raises(FileNotFoundError, match="GHOSTBAND_BEAT_THIS_CHECKPOINT"):
        modules.runtime.extract_beats(tmp_path / "song.wav", 10)


@pytest.mark.parametrize(
    ("rhythm", "message"),
    [
        (([0, 0.5, 1], [0, 1]), None),
        (([0, 0.5, 0.5], [0, 0.5]), "increase"),
        (([0, 0.5, 2], [0, 0.5]), "duration"),
        (([0, 0.5, 1], [0, 0.8]), "coincide"),
        (([0, 0.5, 1], [0]), "Two beats"),
    ],
)
def test_beat_model_uses_local_checkpoint_and_validates_timestamps(
    modules: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rhythm: tuple[list[float], list[float]],
    message: str | None,
) -> None:
    beats, downbeats = rhythm
    checkpoint = tmp_path / "final0.ckpt"
    checkpoint.write_bytes(b"local checkpoint")
    monkeypatch.setenv("GHOSTBAND_BEAT_THIS_CHECKPOINT", str(checkpoint))

    class Model:
        def __init__(self, **options: object) -> None:
            assert options == {
                "checkpoint_path": str(checkpoint),
                "device": "cpu",
                "float16": False,
                "dbn": False,
            }

        def __call__(self, filename: str) -> tuple[list[float], list[float]]:
            assert filename == str(tmp_path / "song.wav")
            return beats, downbeats

    monkeypatch.setitem(sys.modules, "beat_this.inference", SimpleNamespace(File2Beats=Model))
    if message is not None:
        with pytest.raises(ValueError, match=message):
            modules.runtime.extract_beats(tmp_path / "song.wav", 2)
    else:
        actual, actual_downbeats = modules.runtime.extract_beats(tmp_path / "song.wav", 2)
        assert actual.tolist() == beats
        assert actual_downbeats.tolist() == downbeats


def test_note_model_keeps_salience_and_exports_four_column_events(
    modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    salience = np.zeros((3, 88))
    salience[:, 60 - 21] = 0.8

    class Model:
        def __init__(self, path: str) -> None:
            assert path == "local-model"

        def predict(self, audio: NDArray[np.float32]) -> dict[str, NDArray[np.float32]]:
            assert audio.any(), "Silent windows must bypass model normalization"
            return {"note": salience.astype(np.float32)}

    def predict(
        filename: str,
        model_or_model_path: Model,
    ) -> tuple[dict[str, NDArray[np.float64]], None, list[tuple[float, float, int, float, object]]]:
        assert filename == "song.wav"
        silent = model_or_model_path.predict(np.zeros((1, 43844, 1), dtype=np.float32))
        assert {name: values.shape for name, values in silent.items()} == {
            "note": (1, 172, 88),
            "onset": (1, 172, 88),
            "contour": (1, 172, 264),
        }
        assert all(not values.any() for values in silent.values())
        actual = model_or_model_path.predict(np.ones((1, 43844, 1), dtype=np.float32))
        np.testing.assert_allclose(actual["note"], salience)
        return {"note": salience}, None, [(0.1, 0.5, 60, 0.8, [0, 0])]

    monkeypatch.setitem(
        sys.modules, "basic_pitch", SimpleNamespace(ICASSP_2022_MODEL_PATH="local-model")
    )
    monkeypatch.setitem(
        sys.modules,
        "basic_pitch.constants",
        SimpleNamespace(ANNOT_N_FRAMES=172, N_FREQ_BINS_NOTES=88, N_FREQ_BINS_CONTOURS=264),
    )
    monkeypatch.setitem(
        sys.modules, "basic_pitch.inference", SimpleNamespace(Model=Model, predict=predict)
    )
    actual, notes = modules.runtime.extract_notes(Path("song.wav"))
    np.testing.assert_array_equal(actual, salience)
    assert notes == [(0.1, 0.5, 60, 0.8)]


def test_read_audio_rejects_nonfinite_and_empty(
    modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    for samples in (np.empty((0, 2)), np.array([[np.nan, 0]])):

        def read(
            *_args: object, samples: NDArray[np.float64] = samples, **_kw: object
        ) -> tuple[NDArray[np.float64], int]:
            return samples, 22050

        monkeypatch.setitem(
            sys.modules,
            "soundfile",
            SimpleNamespace(read=read),
        )
        with pytest.raises(ValueError, match="nonempty and finite"):
            modules.runtime.read_audio(Path("song.wav"))


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (permissive.Beats, {"beats_s": [0, 0], "downbeats_s": [0, 1]}),
        (permissive.Beats, {"beats_s": [0, 1], "downbeats_s": [0, 0.5]}),
        (permissive.Beats, {"beats_s": [0, float("nan")], "downbeats_s": [0, 1]}),
        (permissive.Notes, {"notes": [[1, 0, 60, 0.5]]}),
        (permissive.Notes, {"notes": [[0, 1, 128, 0.5]]}),
        (permissive.Notes, {"notes": [[0, 1, 60.1, 0.5]]}),
        (permissive.Notes, {"notes": [[0, 1, 60, 2]]}),
    ],
)
def test_invalid_output_is_rejected(
    model: type[permissive.Beats | permissive.Notes], data: object
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_adapter_uses_isolated_frozen_offline_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    output = {"beats_s": [0, 0.5, 1], "downbeats_s": [0, 1]}

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return json.dumps(output).encode(), b"diagnostics"

    async def spawn(*args: object, **kwargs: object) -> Process:
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(permissive, "which", local_uv)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("GHOSTBAND_PERMISSIVE_ENV", str(tmp_path))
    monkeypatch.setenv("GHOSTBAND_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("GHOSTBAND_BEAT_THIS_CHECKPOINT", raising=False)
    assert asyncio.run(permissive.run_permissive("beats", tmp_path / "song.wav")) == output
    args, kwargs = calls[0]
    assert args[:6] == ("/local/uv", "run", "--offline", "--frozen", "--project", str(tmp_path))
    assert args[6:] == ("python", str(tmp_path / "run.py"), "beats", str(tmp_path / "song.wav"))
    assert kwargs["stdout"] == asyncio.subprocess.PIPE
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    # Setup installs the checkpoint beside the models; the extractor must not download one.
    assert environment["GHOSTBAND_BEAT_THIS_CHECKPOINT"] == str(
        tmp_path / "models" / "beat-this" / "final0.ckpt"
    )


def test_adapter_reports_extractor_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"missing local checkpoint"

    async def spawn(*_args: object, **_kwargs: object) -> Process:
        return Process()

    monkeypatch.setattr(permissive, "which", local_uv)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="missing local checkpoint"):
        asyncio.run(permissive.run_permissive("chart", Path("song.wav")))
