"""Run the isolated permissive extractor and validate its JSON boundary."""

import argparse
import asyncio
import json
import os
from itertools import pairwise
from pathlib import Path
from shutil import which
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghostband import setup_models
from ghostband.chart import Chart

EXTRACT_ENV = Path(__file__).resolve().parents[3] / "extract" / "permissive"
Command = Literal["chart", "beats", "notes"]
Time = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Amplitude = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Pitch = Annotated[int, Field(ge=0, le=127, strict=True)]


class Beats(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    beats_s: Annotated[list[Time], Field(min_length=2)]
    downbeats_s: Annotated[list[Time], Field(min_length=2)]

    @model_validator(mode="after")
    def validate_grid(self) -> Self:
        for times in (self.beats_s, self.downbeats_s):
            if any(current <= previous for previous, current in pairwise(times)):
                raise ValueError("Beat timestamps must be strictly increasing")
        if any(min(abs(beat - time) for beat in self.beats_s) > 1e-3 for time in self.downbeats_s):
            raise ValueError("Downbeats must coincide with beats")
        return self


class Notes(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    notes: list[tuple[Time, Time, Pitch, Amplitude]]

    @model_validator(mode="after")
    def validate_intervals(self) -> Self:
        if any(end <= start for start, end, _, _ in self.notes):
            raise ValueError("Note offsets must follow onsets")
        return self


async def run_permissive(command: Command, wav_path: Path) -> dict[str, object]:
    """Extract locally with frozen dependencies, returning schema-validated JSON."""
    if command not in {"chart", "beats", "notes"}:
        raise ValueError("Command must be chart, beats or notes")
    executable = which("uv")
    if executable is None:
        raise FileNotFoundError("Install uv before running the extractor")
    env_dir = Path(os.environ.get("GHOSTBAND_PERMISSIVE_ENV", str(EXTRACT_ENV))).resolve()
    process = await asyncio.create_subprocess_exec(
        executable,
        "run",
        "--offline",
        "--frozen",
        "--project",
        str(env_dir),
        "python",
        str(env_dir / "run.py"),
        command,
        str(wav_path.resolve()),
        env={
            "GHOSTBAND_BEAT_THIS_CHECKPOINT": str(
                setup_models.model_directory() / setup_models.BEAT_THIS_CHECKPOINT.name
            ),
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(
            f"the permissive extractor exited with status {process.returncode}: "
            f"{stderr.decode().strip()}"
        )
    if command == "chart":
        chart = Chart.model_validate_json(stdout)
        if chart.extractor != "permissive" or chart.rights != "unknown":
            raise ValueError("Extracted charts must have permissive provenance and unknown rights")
        return chart.model_dump(mode="json")
    model = (
        Beats.model_validate_json(stdout)
        if command == "beats"
        else Notes.model_validate_json(stdout)
    )
    return model.model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("chart", "beats", "notes"))
    parser.add_argument("wav", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_permissive(args.command, args.wav)), allow_nan=False))


if __name__ == "__main__":
    main()
