"""File identity and engine provenance kept with publishable recordings."""

from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghostband.setup_models import MODEL_REVISION, sha256_file

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TakeProvenance(BaseModel):
    """A live model take must still match the chart and recording it was captured from."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["ghostband-engine", "fake-engine"]
    ghostband_version: Annotated[str, Field(min_length=1)]
    model_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    chart_sha256: Digest
    recording_sha256: Digest

    @classmethod
    def capture(cls, chart: Path, recording: Path, *, fake_engine: bool) -> "TakeProvenance":
        return cls(
            engine="fake-engine" if fake_engine else "ghostband-engine",
            ghostband_version=version("ghostband"),
            model_revision=MODEL_REVISION,
            chart_sha256=sha256_file(chart),
            recording_sha256=sha256_file(recording),
        )

    def validate_files(self, chart: Path, recording: Path) -> None:
        if self.engine != "ghostband-engine":
            raise ValueError("fake-engine recordings cannot establish live acceptance")
        if self.chart_sha256 != sha256_file(chart) or self.recording_sha256 != sha256_file(
            recording
        ):
            raise ValueError("take provenance does not match chart or recording; record a new take")
