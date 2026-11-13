"""Exercise the offline executable, including the real model when explicitly requested."""

import json
import os
import subprocess
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest
from ghostband.presets import PRESETS, Guidance
from ghostband.render import OfflineEngine, render_chart

from conductor.tests.test_render import short_chart

if TYPE_CHECKING:
    from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(
    os.environ.get("GHOSTBAND_OFFLINE_TEST_ENGINE", str(ROOT / "engine/build/ghostband-engine"))
)


def engine_binary() -> Path:
    if not BINARY.is_file():
        pytest.skip("build the engine with cmake first")
    return BINARY


@pytest.mark.parametrize(
    ("command", "frame", "message"),
    [
        ({"cmd": "onset_mode", "mode": 1}, 1, "require frame 0"),
        ({"cmd": "params", "cfg_notes": 10}, 1, "require frame 0"),
        ({"cmd": "prompt", "slot": 0, "text": "folk", "weight": 1}, 1, "require frame 0"),
        ({"cmd": "params", "buffer_frames": 4}, 0, "cannot set buffer_frames"),
        ({"cmd": "play"}, 0, "Unsupported offline command"),
    ],
)
def test_offline_rejects_invalid_controls_before_loading_assets(
    tmp_path: Path,
    command: dict[str, object],
    frame: int,
    message: str,
) -> None:
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({"commands": [{"frame": frame, "command": command}]}))
    result = subprocess.run(  # noqa: S603
        [
            str(engine_binary()),
            "--offline",
            "--commands",
            str(timeline),
            "--model-dir",
            str(tmp_path / "missing"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert message in result.stderr


@pytest.mark.models
def test_offline_conditions_blends_and_seed_rotations_reach_the_model(tmp_path: Path) -> None:
    preset = PRESETS[1]
    models = Path.home() / "Library/Application Support/ghostband/models"
    sf = import_module("soundfile")
    outputs: list[NDArray[np.float32]] = []
    for index, (seed, cfg, slider) in enumerate(
        ((1, 5, 0), (1, 5, 0), (2, 5, 0), (1, 10, 0), (1, 5, 1))
    ):
        output = tmp_path / f"take-{index}.wav"
        engine = OfflineEngine(
            engine_binary(),
            models,
            preset.a,
            prompt_b=preset.b,
            guidance=Guidance(cfg_notes=cfg),
            seed_rotation=seed,
        )
        render_chart(short_chart(), output, engine, slider=slider)
        samples, rate = sf.read(output)
        audio = np.asarray(samples, dtype=np.float32)
        assert rate == 48000
        assert audio.shape == (96000, 2)
        assert np.isfinite(audio).all()
        assert np.max(np.abs(audio)) > 0
        outputs.append(audio)
    np.testing.assert_array_equal(outputs[0], outputs[1])
    for changed in outputs[2:]:
        assert not np.array_equal(outputs[0], changed)
