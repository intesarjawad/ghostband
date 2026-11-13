"""Loopback HTTP and WebSocket access policy."""

import asyncio
import hmac
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from aiohttp import BodyPartReader, WSCloseCode, WSMsgType, web
from pydantic import TypeAdapter

from ghostband import first_run, setup_models
from ghostband.errors import MESSAGES, ErrorCode, GhostbandError
from ghostband.export import chart_to_midi
from ghostband.importing import import_prompt, import_tune, read_chart, validate_file
from ghostband.session import Session

if TYPE_CHECKING:
    from threading import Thread

    from ghostband.api import RecordingInfo

PORT = 7777
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
AUTH_HEADER = "X-Ghostband-Token"
WS_PREFIX = "ghostband."
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class HandlerMiddleware(Protocol):
    def __call__(self, request: web.Request, handler: Handler) -> Awaitable[web.StreamResponse]: ...


def security_policy(token: str, *, dev_origin: str | None = None) -> HandlerMiddleware:
    """Authenticate API requests and reject foreign hosts before routing."""
    hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    origins = {f"http://{host}" for host in hosts}
    if dev_origin is not None:
        parsed = urlsplit(dev_origin)
        if (
            os.environ.get("GHOSTBAND_DEV") != "1"
            or parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or not parsed.port
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
        ):
            raise ValueError("Use GHOSTBAND_DEV=1 with an HTTP loopback origin and explicit port")
        hosts.add(parsed.netloc)
        origins.add(dev_origin)

    @web.middleware
    async def authenticate(request: web.Request, handler: Handler) -> web.StreamResponse:
        if request.headers.get("Host") not in hosts:
            return web.Response(status=403)
        origin = request.headers.get("Origin")
        if origin is not None and origin not in origins:
            return web.Response(status=403)
        if request.path == "/ws":
            protocols = request.headers.get("Sec-WebSocket-Protocol", "").split(",")
            valid = any(
                hmac.compare_digest(value.strip().encode(), (WS_PREFIX + token).encode())
                for value in protocols
            )
        elif request.path.startswith("/api/"):
            valid = hmac.compare_digest(
                request.headers.get(AUTH_HEADER, "").encode(), token.encode()
            )
        else:
            valid = True
        if not valid:
            return web.Response(status=401)
        if request.content_length is not None and request.content_length > MAX_UPLOAD_BYTES:
            return web.Response(status=413)
        return await handler(request)

    return authenticate


_JSON_OBJECT = TypeAdapter(dict[str, object])


class LocalServer:
    """Keep uploads private and expose only opaque identifiers to clients."""

    def __init__(self, session: Session, directory: Path, token: str) -> None:
        self.session = session
        self.directory = directory
        self.token = token
        self.uploads: dict[str, tuple[Path, str]] = {}
        self.sockets: set[web.WebSocketResponse] = set()
        self.import_lock = asyncio.Lock()
        self.recordings = directory / "recordings"
        self.recordings.mkdir(mode=0o700, exist_ok=True)
        takes = sorted(self.recordings.glob("*.wav"), key=lambda path: path.stat().st_mtime_ns)
        self.record_path = takes[-1] if takes else self.recordings / (uuid4().hex + ".wav")
        self.chart_path = directory / "chart.json"
        if session.chart is None and self.chart_path.is_file():
            session.load_chart(read_chart(self.chart_path))
        self.save_chart()
        self.setup_paths = first_run.Paths.defaults()
        self.setup_thread: Thread | None = None
        self._last_setup_status: first_run.SetupStatus | None = None

    def setup_status(self) -> first_run.SetupStatus:
        if self.session.fake_engine:
            return first_run.SetupStatus("ready", "fake_engine")
        if self.setup_paths.status.is_file():
            return first_run.read_status(self.setup_paths.status)
        if first_run.installed(self.setup_paths):
            return first_run.SetupStatus("ready", "ready")
        return first_run.SetupStatus("not_started", "setup")

    async def publish_setup(self) -> None:
        status = self.setup_status()
        if status != self._last_setup_status:
            self._last_setup_status = status
            await self.broadcast(dict(status.event()))

    def start_setup(self) -> None:
        if self.setup_thread is None or not self.setup_thread.is_alive():
            first_run.SetupStatus("installing", "starting").write(self.setup_paths.status)
            self.setup_thread = first_run.start_install(self.setup_paths)

    async def retry_setup(self, _request: web.Request) -> web.Response:
        if self.session.fake_engine:
            return web.json_response(self.setup_status().event())
        self.start_setup()
        await self.publish_setup()
        return web.json_response(self.setup_status().event(), status=202)

    async def get_setup(self, _request: web.Request) -> web.Response:
        return web.json_response(self.setup_status().event())

    def save_chart(self) -> None:
        if self.session.chart is not None:
            temporary = self.chart_path.with_suffix(".json.part")
            temporary.write_text(
                self.session.chart.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(self.chart_path)

    def control(self, data: dict[str, object]) -> None:
        path = self.record_path
        if data.get("type") == "record" and data.get("action") == "start":
            path = self.recordings / (uuid4().hex + ".wav")
        previous_chart = self.session.chart
        self.session.control(data, record_path=path)
        self.record_path = path
        if self.session.chart is not previous_chart:
            self.save_chart()

    async def list_recordings(self, _request: web.Request) -> web.Response:
        recordings: list[RecordingInfo] = [
            {"id": path.stem, "download_url": f"/api/recordings/{path.stem}"}
            for path in sorted(self.recordings.glob("*.wav"))
            if path != self.session.record_path or not self.session.recording
        ]
        return web.json_response({"recordings": recordings})

    async def download_recording(self, request: web.Request) -> web.StreamResponse:
        identifier = request.match_info["recording_id"]
        if len(identifier) != 32 or any(char not in "0123456789abcdef" for char in identifier):
            raise web.HTTPNotFound()
        path = (self.recordings / (identifier + ".wav")).resolve()
        if not path.is_relative_to(self.recordings.resolve()):
            raise web.HTTPNotFound()
        if not path.is_file() or (path == self.session.record_path and self.session.recording):
            raise web.HTTPNotFound()
        return web.FileResponse(
            path, headers={"Content-Disposition": f'attachment; filename="{path.name}"'}
        )

    async def upload(self, request: web.Request) -> web.Response:
        if not request.content_type.startswith("multipart/"):
            raise GhostbandError(ErrorCode.INVALID_INPUT, "upload requires a multipart file")
        reader = await request.multipart()
        part = await reader.next()
        if not isinstance(part, BodyPartReader) or part.filename is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "upload requires a named file")
        display = Path(unquote(part.filename).replace("\\", "/")).name
        identifier = uuid4().hex
        destination = self.directory / (identifier + Path(display).suffix.lower())
        size = 0
        try:
            with destination.open("xb") as output:
                while chunk := await part.read_chunk():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise GhostbandError(ErrorCode.TOO_LARGE, display)
                    output.write(chunk)
            validate_file(destination)
            if await reader.next() is not None:
                raise GhostbandError(ErrorCode.INVALID_INPUT, "upload exactly one file")
            self.uploads[identifier] = (destination, display)
        finally:
            if identifier not in self.uploads:
                destination.unlink(missing_ok=True)
        return web.json_response({"upload_id": identifier})

    async def import_upload(self, data: dict[str, object]) -> None:
        identifier = data.get("upload_id")
        if not isinstance(identifier, str) or identifier not in self.uploads:
            raise GhostbandError(
                ErrorCode.INVALID_INPUT, "unknown upload id; POST /api/upload first"
            )
        extractor = data.get("extractor", "permissive")
        if not isinstance(extractor, str):
            raise GhostbandError(ErrorCode.INVALID_INPUT, "extractor must be a name")
        async with self.import_lock:
            path, display = self.uploads[identifier]
            progress_jobs: list[asyncio.Task[None]] = []

            def progress(stage: str, pct: int) -> None:
                progress_jobs.append(
                    asyncio.create_task(
                        self.broadcast({"event": "import_progress", "stage": stage, "pct": pct})
                    )
                )

            def notice(key: str) -> None:
                message = MESSAGES[key]
                progress_jobs.append(
                    asyncio.create_task(
                        self.broadcast(
                            {
                                "event": "error",
                                "code": key,
                                "message": message.what,
                                "fix": message.next,
                            }
                        )
                    )
                )

            try:
                chart = await import_tune(
                    path,
                    extractor=extractor,
                    workspace=self.directory / identifier,
                    progress=progress,
                    notice=notice,
                )
            finally:
                await asyncio.gather(*progress_jobs)
            self.session.load_chart(chart.model_copy(update={"source": display}))
            reference = self.directory / identifier / "reference_48k.wav"
            self.session.reference_path = reference if reference.is_file() else None
            self.save_chart()
            await self.broadcast(self.session.state())

    async def import_request(self, request: web.Request) -> web.Response:
        await self.import_upload(_JSON_OBJECT.validate_json(await request.read()))
        return web.json_response(self.session.state())

    async def export(self, request: web.Request) -> web.StreamResponse:
        kind = request.match_info["kind"]
        if kind == "wav":
            if self.session.recording or not self.record_path.is_file():
                raise GhostbandError(
                    ErrorCode.INVALID_INPUT, "stop and save a recording before exporting WAV"
                )
            return web.FileResponse(
                self.record_path, headers={"Content-Disposition": 'attachment; filename="take.wav"'}
            )
        chart = self.session.chart
        if chart is None:
            raise GhostbandError(ErrorCode.INVALID_INPUT, "import a chart before exporting")
        if kind == "midi":
            return web.Response(body=chart_to_midi(chart), content_type="audio/midi")
        if kind in {"chart", "json"}:
            return web.Response(
                text=chart.model_dump_json(indent=2) + "\n",
                content_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="chart.json"'},
            )
        raise GhostbandError(ErrorCode.INVALID_INPUT, f"export kind {kind}; use chart, midi or wav")

    async def prompt_audio(self, data: dict[str, object]) -> None:
        identifier, slot = data.get("upload_id"), data.get("slot")
        if (
            not isinstance(identifier, str)
            or identifier not in self.uploads
            or type(slot) is not int
            or not 0 <= slot <= 5
        ):
            raise GhostbandError(
                ErrorCode.INVALID_INPUT, "prompt_audio requires an upload id and slot from 0 to 5"
            )
        path, display = self.uploads[identifier]
        destination = self.directory / (uuid4().hex + ".wav")
        loop = asyncio.get_running_loop()

        def progress(name: str, done: int, total: int) -> None:
            pct = 0 if total == 0 else round(done * 100 / total)
            event: dict[str, object] = {
                "event": "import_progress",
                "stage": f"audio input {Path(name).name}",
                "pct": pct,
            }
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self.broadcast(event)))

        # The three encoder files are downloaded on first use, not by the first run.
        if not self.session.fake_engine:
            await asyncio.to_thread(
                setup_models.fetch_audio_input,
                setup_models.model_directory(),
                progress=progress,
            )
        await asyncio.sleep(0)
        await import_prompt(path, destination)
        self.session.connect()
        self.session.send({"cmd": "prompt_audio", "slot": slot, "path": str(destination)})
        self.session.slots[slot] = display

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(protocols=[WS_PREFIX + self.token], max_msg_size=65_536)
        await socket.prepare(request)
        self.sockets.add(socket)
        try:
            await socket.send_json(self.session.state())
            await socket.send_json(self.setup_status().event())
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    data = _JSON_OBJECT.validate_json(message.data)
                    if data.get("type") == "import":
                        await self.import_upload(data)
                    elif data.get("type") == "prompt_audio":
                        await self.prompt_audio(data)
                        await self.broadcast(self.session.state())
                    else:
                        self.control(data)
                        await self.broadcast(self.session.state())
                except GhostbandError as error:
                    await socket.send_json(error.event())
                except (ValueError, OSError) as error:
                    await socket.send_json(
                        GhostbandError(ErrorCode.INVALID_INPUT, str(error)).event()
                    )
        finally:
            self.sockets.discard(socket)
        return socket

    async def broadcast(self, event: dict[str, object]) -> None:
        for socket in tuple(self.sockets):
            if not socket.closed:
                await socket.send_json(event)

    async def run_clock(self) -> None:
        while True:
            try:
                await self.publish_setup()
                before = self.session.state()
                await self.broadcast(self.session.tick())
                for update in self.session.drain_heard():
                    await self.broadcast(update)
                after = self.session.state()
                if (before["transport"], before["recording"]) != (
                    after["transport"],
                    after["recording"],
                ):
                    await self.broadcast(after)
            except GhostbandError as error:
                await self.broadcast(error.event())
                try:
                    self.session.close()
                except GhostbandError as cleanup_error:
                    await self.broadcast(cleanup_error.event())
            await asyncio.sleep(0.05)


async def index(_request: web.Request) -> web.StreamResponse:
    path = Path(__file__).with_name("ui_dist") / "index.html"
    if path.is_file():
        return web.FileResponse(path)
    return web.json_response(
        GhostbandError(
            ErrorCode.UNAVAILABLE, "the web interface is not built; run npm --prefix ui run build"
        ).event(),
        status=503,
    )


@web.middleware
async def registry_errors(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except GhostbandError as error:
        status = 413 if error.code == ErrorCode.TOO_LARGE else 400
        return web.json_response(error.event(), status=status)
    except (ValueError, OSError) as error:
        return web.json_response(
            GhostbandError(ErrorCode.INVALID_INPUT, str(error)).event(), status=400
        )


def create_app(
    session: Session,
    *,
    token: str | None = None,
    dev_origin: str | None = None,
    auto_setup: bool = False,
) -> web.Application:
    """Create a loopback application; its caller binds exclusively to 127.0.0.1."""
    secret = token if token is not None else secrets.token_urlsafe(32)
    application = web.Application(
        middlewares=[security_policy(secret, dev_origin=dev_origin), registry_errors],
        client_max_size=MAX_UPLOAD_BYTES,
    )
    directory = setup_models.app_directory() / "workspace"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = LocalServer(session, directory, secret)
    application.router.add_get("/", index)
    application.router.add_post("/api/upload", server.upload)
    application.router.add_post("/api/import", server.import_request)
    application.router.add_get("/api/export/{kind}", server.export)
    application.router.add_get("/api/setup", server.get_setup)
    application.router.add_post("/api/setup/retry", server.retry_setup)
    application.router.add_get("/api/recordings", server.list_recordings)
    application.router.add_get("/api/recordings/{recording_id}", server.download_recording)
    application.router.add_get("/ws", server.websocket)
    assets = Path(__file__).with_name("ui_dist") / "assets"
    if assets.is_dir():
        application.router.add_static("/assets/", assets, follow_symlinks=False)

    async def shutdown(_app: web.Application) -> None:
        for socket in tuple(server.sockets):
            await socket.close(code=WSCloseCode.GOING_AWAY)

    async def lifetime(_app: web.Application) -> AsyncIterator[None]:
        if auto_setup and first_run.first_run_required(server.setup_paths):
            server.start_setup()
        clock = asyncio.create_task(server.run_clock())
        try:
            yield
        finally:
            clock.cancel()
            await asyncio.gather(clock, return_exceptions=True)
            session.close()

    application.on_shutdown.append(shutdown)
    application.cleanup_ctx.append(lifetime)
    return application
