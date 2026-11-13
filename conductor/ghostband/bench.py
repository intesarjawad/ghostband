"""Measure frame time and underruns on the real engine and decide what runs live."""

import json
import tempfile
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from typing import Protocol, cast

from ghostband import first_run, setup_models
from ghostband.engine_client import Command, EngineClient
from ghostband.engine_process import EngineProcess

MODELS = ("mrt2_small", "mrt2_base")
BASE_MODEL = "mrt2_base"
# One model frame is 40 ms of audio; the 95th percentile must leave headroom.
LIVE_P95_LIMIT_MS = 32.0
# A shorter run is too few frames to trust a 95th percentile.
LIVE_MIN_SECONDS = 120.0
LOAD_TIMEOUT_S = 180.0
BENCH_MARGIN_S = 60.0
OFFLINE_ONLY = "offline render only on this Mac"
REPORT_NAME = "engine.md"


class Requester(Protocol):
    def request(self, command: Command, *, timeout_s: float = 2.0) -> Command: ...


@dataclass(frozen=True)
class BenchResult:
    """One benchmark run of a model on this machine."""

    model: str
    seconds: float
    p50_ms: float
    p95_ms: float
    underruns: int
    observations: int

    @property
    def live(self) -> bool:
        """Whether the run is long enough, fast enough and never underran."""
        return (
            self.seconds >= LIVE_MIN_SECONDS
            and self.p95_ms < LIVE_P95_LIMIT_MS
            and self.underruns == 0
        )

    def summary(self) -> str:
        return f"p95 {self.p95_ms:.1f} ms, {self.underruns} underruns over {self.seconds:g} s"


@dataclass(frozen=True)
class ModelStatus:
    """What the settings may offer for a model, with the figure behind it."""

    live: bool
    message: str

    def as_state(self) -> dict[str, object]:
        return asdict(self)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value):
        raise ValueError(f"engine bench reply needs a finite number for {name}")
    if value < 0:
        raise ValueError(f"engine bench reply needs a nonnegative {name}")
    return float(value)


def parse_reply(model: str, seconds: float, reply: Command) -> BenchResult:
    """Validate an engine bench reply at the socket boundary."""
    frame = reply.get("frame_time")
    if not isinstance(frame, dict):
        raise ValueError("engine bench reply needs frame_time")
    frame_time = cast("dict[str, object]", frame)
    underruns = reply.get("underruns")
    observations = frame_time.get("observations")
    if type(underruns) is not int or underruns < 0:
        raise ValueError("engine bench reply needs a nonnegative integer underruns")
    if type(observations) is not int or observations < 1:
        raise ValueError("engine bench reply needs at least one frame observation")
    return BenchResult(
        model,
        seconds,
        _number(frame_time.get("p50_ms"), "frame_time.p50_ms"),
        _number(frame_time.get("p95_ms"), "frame_time.p95_ms"),
        underruns,
        observations,
    )


def measure(client: Requester, model: str, resources: Path, seconds: float) -> BenchResult:
    """Load one model on a connected engine and time frame generation for seconds."""
    client.request(
        {"cmd": "load", "model": model, "resources": str(resources)}, timeout_s=LOAD_TIMEOUT_S
    )
    reply = client.request(
        {"cmd": "bench", "seconds": seconds, "latency_probes": 0},
        timeout_s=seconds + BENCH_MARGIN_S,
    )
    return parse_reply(model, seconds, reply)


def run_engine(engine: Path, resources: Path, model: str, seconds: float) -> BenchResult:
    """Start the engine on a private socket, benchmark one model, then stop it."""
    with tempfile.TemporaryDirectory(prefix="ghostband-bench-", dir="/tmp") as directory:
        process = EngineProcess.start(engine, Path(directory) / "engine.sock")
        try:
            with EngineClient.connect(process.socket_path) as client:
                result = measure(client, model, resources, seconds)
                client.send({"cmd": "quit"})
            return result
        finally:
            process.stop()


def records_directory() -> Path:
    return setup_models.app_directory() / "bench"


def save(result: BenchResult, directory: Path | None = None) -> Path:
    """Keep the latest run of a model where the settings and the report read it."""
    path = (directory or records_directory()) / f"{result.model}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def load(model: str, directory: Path | None = None) -> BenchResult | None:
    """The last saved run, or None when absent or unreadable."""
    path = (directory or records_directory()) / f"{model}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BenchResult(
            str(data["model"]),
            _number(data["seconds"], "seconds"),
            _number(data["p50_ms"], "p50_ms"),
            _number(data["p95_ms"], "p95_ms"),
            int(data["underruns"]),
            int(data["observations"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def base_model_status(directory: Path | None = None) -> ModelStatus:
    """mrt2_base is live only on a passing run; otherwise say why it is not."""
    result = load(BASE_MODEL, directory)
    if result is None:
        return ModelStatus(
            False,
            f"{OFFLINE_ONLY}: not measured yet; run ghostband bench --model {BASE_MODEL} "
            f"--seconds {LIVE_MIN_SECONDS:g}",
        )
    if result.live:
        return ModelStatus(True, f"live: {result.summary()}")
    return ModelStatus(False, f"{OFFLINE_ONLY}: {result.summary()}")


REPORT_HEADER = """# Engine benchmark

Frame time and underruns of the real engine, written by `ghostband bench`.
Each row is the latest run of that model on the Mac that ran it. A model is
offered for live playback only when its run lasted at least {seconds:g} s with
a 95th-percentile frame time under {limit:g} ms and zero underruns.

| Model | Seconds | Frame p50 ms | Frame p95 ms | Underruns | Observations | Live |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
"""


def render_report(results: list[BenchResult]) -> str:
    rows = [
        f"| {r.model} | {r.seconds:g} | {r.p50_ms:.3f} | {r.p95_ms:.3f} | {r.underruns} "
        f"| {r.observations} | {'yes' if r.live else 'no'} |\n"
        for r in results
    ]
    header = REPORT_HEADER.format(seconds=LIVE_MIN_SECONDS, limit=LIVE_P95_LIMIT_MS)
    return header + "".join(rows)


def write_report(path: Path, directory: Path | None = None) -> None:
    """Rewrite the report from the saved run of each model."""
    results = [result for model in MODELS if (result := load(model, directory)) is not None]
    path.write_text(render_report(results), encoding="utf-8")


def default_report() -> Path | None:
    """The checkout's report, or None when running from an installed package."""
    folder = first_run.ROOT / "docs" / "benchmarks"
    return folder / REPORT_NAME if folder.is_dir() else None
