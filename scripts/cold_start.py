"""Time a first run from its install command to the first live playback.

Run it from the repository root, on the account that is being timed:

    uv run python scripts/cold_start.py --route local --report cold-start.json

The dropped track is the first mix of the locked AAM sample. `scripts/fetch_aam.py` puts it
in the cache before the run, so its own download is not part of any stage; `--second` names
the track dropped once the band is installed. The install row is timed for the uvx and brew
routes; the local wheel is installed by setup, so its row is printed but not timed. This
harness has no browser, so `ready-preview` is the moment the page and its first state are
served: the point at which the interface can show its take while the weights arrive.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import TYPE_CHECKING, cast

import aiohttp
from ghostband import first_run

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
PORT = 7777
AUTH_HEADER = "X-Ghostband-Token"
WS_PREFIX = "ghostband."
STATUS_POLL_S = 0.25
TOKEN_LINE = re.compile(r"\?t=(\S+)")
DEFAULT_LOCK = ROOT / "docs/benchmarks/aam-subset.json"
DEFAULT_CACHE = Path.home() / "Library/Caches/ghostband/aam"
DEFAULT_SECOND = ROOT / "fixtures/greensleeves.mid"

# What each route runs to install; the local wheel is installed by setup before the run.
INSTALL_COMMANDS: dict[str, tuple[str, ...]] = {
    "uvx": ("uvx", "ghostband=={version}", "--version"),
    "brew": ("brew", "install", "ghostband"),
    "local": ("uv", "tool", "install", "--force", "{wheel}"),
}
# Targets this harness enforces; `--budget NAME=SECONDS` overrides one for a slower machine.
BUDGETS: dict[str, float] = {
    "install": 900.0,
    "ready-preview": 5.0,
    "chart": 120.0,
    "ready-live": 1800.0,
    "playing": 5.0,
}


@dataclass(frozen=True)
class Options:
    """Everything one run needs besides the installed band."""

    route: str
    version: str
    aam: Path | None
    cache: Path
    second: Path
    status: Path
    report: Path | None
    fake_engine: bool
    timeout_s: float


def wheel_path(version: str) -> Path:
    return ROOT / "dist" / f"ghostband-{version}-py3-none-any.whl"


def command_arguments(route: str, version: str) -> list[str]:
    """The words each route's install command is built from, with its release or wheel filled in."""
    return [
        argument.format(version=version, wheel=wheel_path(version))
        for argument in INSTALL_COMMANDS[route]
    ]


def install_command(route: str, version: str) -> str:
    """The command each route installs with, as the report prints it."""
    return " ".join(command_arguments(route, version))


def launch_command(route: str, version: str, *, fake_engine: bool) -> list[str]:
    """Start the band the way the route installed it."""
    binary = {
        "uvx": ["uvx", f"ghostband=={version}"],
        "brew": ["ghostband"],
        "local": [str(Path(sys.executable).with_name("ghostband"))],
    }[route]
    return [*binary, *(["--fake-engine"] if fake_engine else [])]


def first_mix(lock: Path, cache: Path) -> Path:
    """The mix the lock keeps first, already extracted from the AAM cache."""
    tracks: list[int] = json.loads(lock.read_text(encoding="utf-8"))["tracks"]
    return cache / "mixes" / f"{tracks[0]:04d}_mix.flac"


def pinned_mix(options: Options) -> Path:
    """The first track to drop: `--aam`, or the first mix the lock keeps."""
    return options.aam if options.aam is not None else first_mix(DEFAULT_LOCK, options.cache)


def missing_mix(options: Options) -> str | None:
    """Why no first track can be dropped, or nothing when one is on disk."""
    if options.aam is not None:
        return None if options.aam.is_file() else f"{options.aam} is missing"
    if not DEFAULT_LOCK.is_file():
        return f"{DEFAULT_LOCK} is missing; run `uv run python -m scripts.fetch_aam` first"
    mix = pinned_mix(options)
    if mix.is_file():
        return None
    return (
        f"{mix} is missing; run `uv run python -m scripts.fetch_aam --cache {options.cache}` first"
    )


def stage_budgets(route: str) -> dict[str, float]:
    """The local wheel is installed by setup, so its install row is not timed."""
    return {
        name: budget for name, budget in BUDGETS.items() if name != "install" or route != "local"
    }


def problems(stages: Mapping[str, float | None], budgets: Mapping[str, float]) -> list[str]:
    """Name every budgeted stage that was never marked or ran past its budget."""
    found: list[str] = []
    for name, budget in budgets.items():
        seconds = stages.get(name)
        if seconds is None:
            found.append(f"no {name} mark")
        elif seconds > budget:
            found.append(f"{name} took {seconds:.1f} s, budget {budget:.1f} s")
    return found


@dataclass(frozen=True)
class StatusSample:
    """One read of the first-run progress file, timed from the launch."""

    at_s: float
    state: str
    stage: str
    done_bytes: int


def throughput(samples: Sequence[StatusSample]) -> float | None:
    """Download bytes per second between the first and last progress sample of the weights."""
    if len(samples) < 2:
        return None
    seconds = samples[-1].at_s - samples[0].at_s
    transferred = samples[-1].done_bytes - samples[0].done_bytes
    if seconds <= 0 or transferred <= 0:
        return None
    return transferred / seconds


class StatusWatch:
    """Sample the first-run progress file so the install and its download are timed."""

    def __init__(self, path: Path, started_s: float) -> None:
        self.path = path
        self.started_s = started_s
        self.samples: list[StatusSample] = []
        self.finished_s: float | None = None
        self.error: str | None = None
        self._done = asyncio.Event()

    async def run(self) -> None:
        try:
            if self.path.stat().st_mtime < time.time() - 1:
                return  # a progress file older than this run belongs to an earlier setup
        except OSError:
            pass
        while not self._done.is_set():
            self.sample(time.monotonic())
            if self.finished_s is not None:
                return
            await asyncio.sleep(STATUS_POLL_S)

    def sample(self, now_s: float) -> None:
        try:
            status = first_run.read_status(self.path)
        except (OSError, ValueError, KeyError):
            return
        self.samples.append(
            StatusSample(now_s - self.started_s, status.state, status.stage, status.done_bytes)
        )
        if status.state == "failed":
            self.error = status.detail
        if status.state in {"ready", "failed"}:
            self.finished_s = self.samples[-1].at_s

    def stop(self) -> None:
        self._done.set()

    def download(self) -> dict[str, float | int] | None:
        samples = [sample for sample in self.samples if sample.stage == "models"]
        rate = throughput(samples)
        if rate is None:
            return None
        return {
            "first_bytes": samples[0].done_bytes,
            "last_bytes": samples[-1].done_bytes,
            "seconds": round(samples[-1].at_s - samples[0].at_s, 3),
            "bytes_per_s": round(rate),
        }


class Marks:
    """Seconds from the launch to each milestone, kept once per name."""

    def __init__(self) -> None:
        self.started_s = time.monotonic()
        self.values: dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.values.setdefault(name, time.monotonic() - self.started_s)


def preview_before_download(watch: StatusWatch | None, preview_s: float | None) -> bool | None:
    """Whether the page was served before the weights finished arriving."""
    if watch is None or preview_s is None or watch.finished_s is None:
        return None
    return preview_s < watch.finished_s


class FirstRunError(RuntimeError):
    """The run reached its end without a usable first run."""


@dataclass(frozen=True)
class Run:
    """One measured run: its marks, its download and whatever stopped it."""

    marks: dict[str, float]
    download: dict[str, float | int] | None
    preview_before_download: bool | None
    error: str | None
    log_tail: str


def port_in_use() -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def start_server(options: Options, log: Path) -> subprocess.Popen[bytes]:
    if port_in_use():
        raise RuntimeError(f"port {PORT} is already in use; stop that server first")
    command = launch_command(options.route, options.version, fake_engine=options.fake_engine)
    with log.open("wb") as output:
        return subprocess.Popen(  # noqa: S603 - the command comes from this module's table
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env={**os.environ, "COLUMNS": "1000"},
        )


def read_token(log: Path, server: subprocess.Popen[bytes], timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = TOKEN_LINE.search(log.read_text(errors="replace"))
        if found:
            return found.group(1)
        if server.poll() is not None:
            raise RuntimeError(f"ghostband exited with status {server.returncode}")
        time.sleep(0.1)
    raise TimeoutError(f"ghostband did not print its address within {timeout_s:.0f} s")


def stop_server(server: subprocess.Popen[bytes]) -> None:
    if server.poll() is not None:
        return
    server.send_signal(signal.SIGINT)
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


async def pump(socket_: aiohttp.ClientWebSocketResponse, timeout_s: float) -> dict[str, object]:
    """Read one conductor event, refusing an ended socket or a reported error."""
    async with asyncio.timeout(timeout_s):
        message = await socket_.receive()
    if message.type != aiohttp.WSMsgType.TEXT:
        raise RuntimeError(f"WebSocket ended: {message.type.name}")
    event: dict[str, object] = json.loads(message.data)
    if event.get("event") == "error":
        raise RuntimeError(f"ghostband reported {event.get('code')}: {event.get('message')}")
    return event


async def until(
    socket_: aiohttp.ClientWebSocketResponse,
    accept: Callable[[dict[str, object]], bool],
    timeout_s: float,
) -> dict[str, object]:
    """Wait for the first conductor event that satisfies a test."""
    deadline = time.monotonic() + timeout_s
    while (remaining := deadline - time.monotonic()) > 0:
        event = await pump(socket_, max(0.1, remaining))
        if accept(event):
            return event
    raise TimeoutError(f"no matching event within {timeout_s:.0f} s")


def is_state(event: dict[str, object]) -> bool:
    return event.get("event") == "state"


def is_chart(event: dict[str, object]) -> bool:
    return is_state(event) and event.get("chart") is not None


def is_live(event: dict[str, object]) -> bool:
    health = event.get("engine_health")
    return (
        is_state(event)
        and isinstance(health, dict)
        and cast("dict[str, object]", health).get("online") is True
    )


def is_clock(event: dict[str, object]) -> bool:
    return event.get("event") == "clock"


def stopped(event: dict[str, object]) -> bool:
    return is_state(event) and event.get("transport") == "stopped"


async def open_page(http: aiohttp.ClientSession, base: str) -> None:
    """Fetch what the interface opens, which is the moment it can preview."""
    async with http.get(f"{base}/") as response:
        response.raise_for_status()
        await response.read()


async def drop(
    http: aiohttp.ClientSession,
    socket_: aiohttp.ClientWebSocketResponse,
    base: str,
    track: Path,
    timeout_s: float,
) -> None:
    """Upload one track and wait until its chart is shown."""
    form = aiohttp.FormData()
    form.add_field("file", track.read_bytes(), filename=track.name)
    async with asyncio.timeout(timeout_s):
        async with http.post(f"{base}/api/upload", data=form) as response:
            response.raise_for_status()
            upload_id = (await response.json())["upload_id"]
    await socket_.send_json({"type": "import", "upload_id": upload_id})
    await until(socket_, is_chart, timeout_s)


async def stop_playback(socket_: aiohttp.ClientWebSocketResponse, timeout_s: float) -> None:
    await socket_.send_json({"type": "transport", "action": "stop"})
    await until(socket_, stopped, timeout_s)


async def wait_installed(watch: StatusWatch, timeout_s: float) -> None:
    """Wait for the weights, which is what a live engine needs."""
    async with asyncio.timeout(timeout_s):
        while watch.finished_s is None:
            await asyncio.sleep(STATUS_POLL_S)


async def drive(options: Options, log: Path, watch: StatusWatch, marks: Marks) -> None:
    """Run the stages in order, marking each one as it happens."""
    server = start_server(options, log)
    base = f"http://127.0.0.1:{PORT}"
    marks.mark("launch")
    try:
        token = read_token(log, server, options.timeout_s)
        async with aiohttp.ClientSession(headers={AUTH_HEADER: token}) as http:
            await open_page(http, base)
            async with http.ws_connect(f"{base}/ws", protocols=[f"{WS_PREFIX}{token}"]) as socket_:
                await until(socket_, is_state, options.timeout_s)
                marks.mark("ready-preview")

                await drop(http, socket_, base, pinned_mix(options), options.timeout_s)
                marks.mark("chart")

                if not options.fake_engine and watch.samples and watch.finished_s is None:
                    await wait_installed(watch, options.timeout_s)
                await socket_.send_json({"type": "transport", "action": "play"})
                await until(socket_, is_live, options.timeout_s)
                marks.mark("ready-live")
                await until(socket_, is_clock, options.timeout_s)
                marks.mark("playing")
                await stop_playback(socket_, options.timeout_s)

                await drop(http, socket_, base, options.second, options.timeout_s)
                marks.mark("chart-2")
                await socket_.send_json({"type": "transport", "action": "play"})
                # A clock frame from the first take can arrive after its stop; only one that
                # follows the new live state belongs to the second take.
                await until(socket_, is_live, options.timeout_s)
                await until(socket_, is_clock, options.timeout_s)
                marks.mark("playing-2")
                await stop_playback(socket_, options.timeout_s)
    finally:
        stop_server(server)


async def measure(options: Options) -> Run:
    """Install, then time one run from the launch to the second drop."""
    install_s: float | None = None
    error: str | None = None
    marks = Marks()
    watch: StatusWatch | None = None
    with tempfile.TemporaryDirectory(prefix="ghostband-cold-start-") as directory:
        log = Path(directory) / "server.log"
        try:
            install_s = await install(options)
            marks = Marks()
            watch = StatusWatch(options.status, marks.started_s)
            watcher = asyncio.create_task(watch.run())
            try:
                await drive(options, log, watch, marks)
            finally:
                watch.stop()
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            if watch.error is not None:
                raise FirstRunError(f"the first run failed during install: {watch.error}")
        except Exception as failure:
            error = f"{type(failure).__name__}: {failure}"
        values = {"install": install_s, **marks.values}
        preview = preview_before_download(watch, marks.values.get("ready-preview"))
        measured: dict[str, float] = {}
        if error is None:
            for name, seconds in values.items():
                if seconds is not None:
                    measured[name] = round(seconds, 3)
        return Run(
            marks=measured,
            download=None if watch is None or error is not None else watch.download(),
            preview_before_download=preview,
            error=error,
            log_tail=log.read_text(errors="replace")[-2000:] if log.is_file() else "",
        )


async def install(options: Options) -> float | None:
    """Run the route's install command; the local wheel was installed before the run."""
    if options.route == "local":
        return None
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command_arguments(options.route, options.version),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "UV_NO_PROGRESS": "1"},
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=options.timeout_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        tail = output.decode(errors="replace").strip().splitlines()[-3:]
        raise RuntimeError(f"{install_command(options.route, options.version)} failed: {tail}")
    return time.monotonic() - started


def findings(options: Options, run: Run) -> list[str]:
    """Everything that keeps this run from counting as a met first run."""
    found = problems(run.marks, stage_budgets(options.route))
    return found if run.error is None else [run.error, *found]


def report(options: Options, run: Run) -> dict[str, object]:
    """The record a person reads when a stage misses its budget."""
    found = findings(options, run)
    budgets = stage_budgets(options.route)
    return {
        "version": options.version,
        "route": options.route,
        "install_command": install_command(options.route, options.version),
        "result": "pass" if not found else "fail",
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "fake_engine": options.fake_engine,
        "stages": run.marks,
        "budgets": budgets,
        "download": run.download,
        "preview_before_download": run.preview_before_download,
        "problems": found,
    }


def parse(argv: Sequence[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(
        prog="cold_start.py",
        description="Time a first run from the install command to the first live playback.",
    )
    parser.add_argument("--route", choices=sorted(INSTALL_COMMANDS), default="local")
    parser.add_argument(
        "--version", default=package_version("ghostband"), help="release to install and time"
    )
    parser.add_argument("--aam", type=Path, help="first track to drop; defaults to the AAM lock")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="AAM cache root")
    parser.add_argument("--second", type=Path, default=DEFAULT_SECOND)
    parser.add_argument("--status", type=Path, help="first-run progress file to watch")
    parser.add_argument("--report", type=Path, help="write the report here")
    parser.add_argument(
        "--budget", action="append", default=[], metavar="NAME=SECONDS", help="override a budget"
    )
    parser.add_argument("--timeout", type=float, default=3600.0, help="longest wait for a stage")
    parser.add_argument(
        "--fake-engine", action="store_true", help="check the harness without models or audio"
    )
    arguments = parser.parse_args(argv)
    for override in arguments.budget:
        name, _, seconds = override.partition("=")
        if name not in BUDGETS:
            parser.error(f"unknown budget {name}; choose from {', '.join(BUDGETS)}")
        BUDGETS[name] = float(seconds)
    status = arguments.status
    if status is None:
        status = first_run.Paths.defaults().status
    return Options(
        route=arguments.route,
        version=arguments.version,
        aam=arguments.aam,
        cache=arguments.cache,
        second=arguments.second,
        status=status,
        report=arguments.report,
        fake_engine=arguments.fake_engine,
        timeout_s=arguments.timeout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    options = parse(argv)
    issue = missing_mix(options)
    if issue is not None:
        print(f"FAIL: {issue}", file=sys.stderr)
        return 1
    run = asyncio.run(measure(options))
    written = report(options, run)
    if options.report is not None:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    found = findings(options, run)
    if not found:
        stages = ", ".join(f"{name} {seconds:g} s" for name, seconds in run.marks.items())
        print(f"PASS: {options.route}: {stages}")
        return 0
    for problem in found:
        print(f"FAIL: {problem}", file=sys.stderr)
    if run.log_tail:
        print(run.log_tail, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
