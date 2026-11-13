"""Additional HTTP/WebSocket payloads for local clients.

The existing `state` event includes `chart_geometry: ChartGeometry | None` beside
`chart`. Positions use counted beats; see ChartGeometry's field descriptions.
"""

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class ChartGeometry(BaseModel):
    """Session API `chart_geometry`; every position is in musician-counted beats."""

    beats_per_bar: int = Field(description="Counted beats per bar: two dotted quarters in 6/8.")
    pulses_per_beat: int = Field(description="Denominator notes per counted beat: three in 6/8.")
    beat_phase: float = Field(description="Counted beat grid offset modulo one, including pickups.")
    bar_origin: float = Field(description="First full bar starts here; zero without a pickup.")


class SetupEvent(TypedDict):
    """`setup_status` is replayed on WebSocket connection and sent when setup changes.

    `state` is not_started, installing, ready or failed. Byte counts describe the current
    stage; `detail` contains the failure reason. POST /api/setup/retry resumes setup.
    """

    event: Literal["setup_status"]
    state: str
    stage: str
    done_bytes: int
    total_bytes: int
    detail: str
    retryable: bool


class RecordingInfo(TypedDict):
    """GET /api/recordings lists stopped WAVs, retained across server restarts."""

    id: str
    download_url: str
