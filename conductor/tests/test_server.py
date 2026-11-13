import asyncio
import json
import wave
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from threading import Event

import pytest
from aiohttp import FormData, WSCloseCode, WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from ghostband import first_run, setup_models
from ghostband.importing import read_chart
from ghostband.server import LocalServer, create_app
from ghostband.session import Session

ROOT = Path(__file__).resolve().parents[2]
HEADERS = {"Host": "127.0.0.1:7777", "X-Ghostband-Token": "secret"}


def test_shutdown_closes_a_playing_browser_before_cleanup() -> None:
    async def exercise() -> None:
        session = Session(fake_engine=True)
        session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
        app = create_app(session, token=HEADERS["X-Ghostband-Token"])
        async with TestClient(TestServer(app)) as client:
            socket = await client.ws_connect("/ws", headers=HEADERS, protocols=["ghostband.secret"])
            await socket.send_json({"type": "transport", "action": "play"})
            while True:
                event = await socket.receive_json()
                if event.get("transport") == "playing":
                    break

            async def closed() -> None:
                while True:
                    message = await socket.receive()
                    if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED):
                        return

            reader = asyncio.create_task(closed())
            async with asyncio.timeout(1):
                await app.shutdown()
                await reader
            assert socket.close_code == WSCloseCode.GOING_AWAY
        assert session.engine is None

    asyncio.run(exercise())


def test_upload_websocket_play_record_export() -> None:
    async def exercise() -> None:
        session = Session(fake_engine=True)
        async with TestClient(
            TestServer(create_app(session, token=HEADERS["X-Ghostband-Token"]))
        ) as client:
            form = FormData()
            form.add_field(
                "file", (ROOT / "fixtures/greensleeves.mid").read_bytes(), filename="../../song.mid"
            )
            response = await client.post("/api/upload", data=form, headers=HEADERS)
            assert response.status == 200
            identifier = (await response.json())["upload_id"]
            assert len(identifier) == 32
            async with client.ws_connect(
                "/ws", headers=HEADERS, protocols=["ghostband.secret"]
            ) as socket:
                state = await socket.receive_json()
                assert state["chart"] is None
                await socket.send_json({"type": "import", "upload_id": identifier})
                while True:
                    event = await socket.receive_json()
                    if event["event"] == "state":
                        break
                assert event["chart"]["extractor"] == "midi"
                assert event["chart"]["source"] == "song.mid"
                for command in [
                    {"type": "preset", "value": 0},
                    {"type": "transport", "action": "play"},
                    {"type": "record", "action": "start"},
                ]:
                    await socket.send_json(command)
                    while (await socket.receive_json())["event"] != "state":
                        pass
                async with asyncio.timeout(3):
                    while (await socket.receive_json())["event"] != "clock":
                        pass
                await socket.send_json({"type": "transport", "action": "stop"})
                while True:
                    event = await socket.receive_json()
                    if event["event"] == "state":
                        break
                assert event["transport"] == "stopped"
                assert event["recording"] is False
                await socket.send_str("[]")
                while True:
                    event = await socket.receive_json()
                    if event["event"] == "error":
                        break
                assert event["code"] == "invalid_input"
                assert event["fix"].startswith("Run ghostband")
            for kind, signature in [("midi", b"MThd"), ("wav", b"RIFF"), ("chart", b"{")]:
                response = await client.get(f"/api/export/{kind}", headers=HEADERS)
                assert response.status == 200
                body = await response.read()
                assert body.startswith(signature)
                if kind == "wav":
                    with wave.open(BytesIO(body)) as audio:
                        assert audio.getnframes() > 0
                if kind == "chart":
                    assert json.loads(body)["rights"] == "unknown"
        assert session.engine is None

    asyncio.run(exercise())


def test_real_routes_keep_security_and_upload_boundaries() -> None:
    async def exercise() -> None:
        async with TestClient(
            TestServer(create_app(Session(fake_engine=True), token=HEADERS["X-Ghostband-Token"]))
        ) as client:
            for headers, status in [
                ({**HEADERS, "X-Ghostband-Token": "wrong"}, 401),
                ({**HEADERS, "Origin": "https://example.org"}, 403),
                ({**HEADERS, "Host": "example.org:7777"}, 403),
            ]:
                response = await client.get("/api/export/chart", headers=headers)
                assert response.status == status
            form = FormData()
            form.add_field("file", b"ID3invalid", filename="wrong.wav")
            response = await client.post("/api/upload", data=form, headers=HEADERS)
            assert response.status == 400
            assert (await response.json())["code"] == "format_mismatch"
            response = await client.post(
                "/api/import", json={"upload_id": "../../private"}, headers=HEADERS
            )
            assert response.status == 400
            assert (await response.json())["code"] == "invalid_input"

    asyncio.run(exercise())


@pytest.mark.parametrize("fake_engine", [False, True])
def test_prompt_audio_fetches_the_audio_input_files_before_sending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_engine: bool
) -> None:
    session = Session(fake_engine=True)
    server = LocalServer(session, tmp_path, "token")
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")
    server.uploads["upload"] = (clip, "clip.wav")
    fetched: list[Path | None] = []

    def fetch(root: Path | None = None, *, progress: object = None) -> tuple[Path, ...]:
        fetched.append(root)
        assert callable(progress)
        progress("resources/spectrostream/encoder.safetensors", 1, 2)
        return ()

    async def import_prompt(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"wav")

    events: list[dict[str, object]] = []

    async def broadcast(event: dict[str, object]) -> None:
        events.append(event)

    monkeypatch.setattr(setup_models, "fetch_audio_input", fetch)
    monkeypatch.setattr("ghostband.server.import_prompt", import_prompt)
    monkeypatch.setattr(server, "broadcast", broadcast)

    session.connect()
    session.fake_engine = fake_engine
    asyncio.run(server.prompt_audio({"upload_id": "upload", "slot": 0}))

    assert fetched == ([] if fake_engine else [setup_models.model_directory()])
    assert events == (
        []
        if fake_engine
        else [
            {
                "event": "import_progress",
                "stage": "audio input encoder.safetensors",
                "pct": 50,
            }
        ]
    )
    assert session.slots[0] == "clip.wav"


def test_chunked_upload_enforces_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ghostband.server.MAX_UPLOAD_BYTES", 1024)

    async def body() -> AsyncIterator[bytes]:
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="large.wav"\r\n\r\n'
        yield b"RIFF" + bytes(2048)
        yield b"\r\n--test--\r\n"

    async def exercise() -> None:
        async with TestClient(
            TestServer(create_app(Session(fake_engine=True), token=HEADERS["X-Ghostband-Token"]))
        ) as client:
            response = await client.post(
                "/api/upload",
                data=body(),
                headers={**HEADERS, "Content-Type": "multipart/form-data; boundary=test"},
            )
            assert response.status == 413
            assert (await response.json())["code"] == "too_large"

    asyncio.run(exercise())


@pytest.fixture(autouse=True)
def private_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("GHOSTBAND_DATA_DIR", str(data))
    monkeypatch.setenv("GHOSTBAND_MODEL_DIR", str(data / "models"))
    monkeypatch.setenv("GHOSTBAND_ENGINE", str(data / "engine/ghostband-engine"))


def test_recordings_and_edits_survive_shutdown_and_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        session = Session(fake_engine=True)
        session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
        async with TestClient(
            TestServer(create_app(session, token=HEADERS["X-Ghostband-Token"]))
        ) as client:
            async with client.ws_connect(
                "/ws", headers=HEADERS, protocols=["ghostband.secret"]
            ) as socket:
                await socket.receive_json()
                for command in [
                    {"type": "rename_section", "index": 0, "name": "Kept edit"},
                    {"type": "record", "action": "start"},
                    {"type": "record", "action": "stop"},
                    {"type": "record", "action": "start"},
                    {"type": "record", "action": "stop"},
                ]:
                    await socket.send_json(command)
                    while (await socket.receive_json())["event"] != "state":
                        pass
            response = await client.get("/api/recordings", headers=HEADERS)
            recordings = (await response.json())["recordings"]
            assert len(recordings) == 2
            assert recordings[0]["id"] != recordings[1]["id"]
        assert len(list((tmp_path / "data/workspace/recordings").glob("*.wav"))) == 2
        async with TestClient(
            TestServer(create_app(Session(fake_engine=True), token=HEADERS["X-Ghostband-Token"]))
        ) as client:
            response = await client.get("/api/export/chart", headers=HEADERS)
            assert (await response.json())["sections"][0]["name"] == "Kept edit"
            assert "attachment" in response.headers["Content-Disposition"]
            for path in ["/api/export/wav", recordings[0]["download_url"]]:
                response = await client.get(path, headers=HEADERS)
                assert response.status == 200
                assert (await response.read()).startswith(b"RIFF")
                response = await client.get(path, headers={"Host": HEADERS["Host"]})
                assert response.status == 401
            response = await client.get("/api/recordings/not-a-recording", headers=HEADERS)
            assert response.status == 404

    asyncio.run(exercise())


def test_recording_download_does_not_follow_links_out_of_the_workspace(tmp_path: Path) -> None:
    async def exercise() -> None:
        outside = tmp_path / "outside.wav"
        outside.write_bytes(b"RIFF")
        async with TestClient(
            TestServer(create_app(Session(fake_engine=True), token=HEADERS["X-Ghostband-Token"]))
        ) as client:
            identifier = "a" * 32
            link = tmp_path / "data/workspace/recordings" / (identifier + ".wav")
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside)
            response = await client.get(f"/api/recordings/{identifier}", headers=HEADERS)
            assert response.status == 404

    asyncio.run(exercise())


def test_setup_failure_replay_retry_and_single_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, finish = Event(), Event()
    calls: list[first_run.Paths] = []

    def install(paths: first_run.Paths) -> first_run.SetupStatus:
        calls.append(paths)
        first_run.SetupStatus("installing", "models", 4, 10).write(paths.status)
        entered.set()
        assert finish.wait(timeout=5)
        status = first_run.SetupStatus("ready", "ready")
        status.write(paths.status)
        return status

    monkeypatch.setattr(first_run, "install", install)
    paths = first_run.Paths.defaults()
    first_run.SetupStatus("failed", "engine", detail="network unavailable").write(paths.status)

    async def exercise() -> None:
        async with (
            TestClient(
                TestServer(create_app(Session(), token=HEADERS["X-Ghostband-Token"]))
            ) as client,
            client.ws_connect("/ws", headers=HEADERS, protocols=["ghostband.secret"]) as socket,
        ):
            await socket.receive_json()
            event = await socket.receive_json()
            assert event["event"] == "setup_status"
            assert event["state"] == "failed"
            assert event["retryable"] is True
            assert event["detail"] == "network unavailable"
            response = await client.post("/api/setup/retry", headers={"Host": HEADERS["Host"]})
            assert response.status == 401
            response = await client.post("/api/setup/retry", headers=HEADERS)
            assert response.status == 202
            assert (await response.json())["state"] == "installing"
            assert await asyncio.to_thread(entered.wait, 2)
            response = await client.post("/api/setup/retry", headers=HEADERS)
            assert response.status == 202
            assert len(calls) == 1
            async with asyncio.timeout(3):
                while True:
                    event = await socket.receive_json()
                    if event["event"] == "setup_status" and event["stage"] == "models":
                        break
            assert (event["done_bytes"], event["total_bytes"]) == (4, 10)
            finish.set()
            async with asyncio.timeout(3):
                while True:
                    event = await socket.receive_json()
                    if event["event"] == "setup_status" and event["state"] == "ready":
                        break
            assert event["retryable"] is False
            response = await client.get("/api/setup", headers=HEADERS)
            assert (await response.json())["state"] == "ready"

    try:
        asyncio.run(exercise())
    finally:
        finish.set()


def test_setup_retry_broadcasts_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_paths: first_run.Paths) -> first_run.SetupStatus:
        raise OSError("download interrupted")

    monkeypatch.setattr(first_run, "install", broken)

    async def exercise() -> None:
        async with (
            TestClient(
                TestServer(create_app(Session(), token=HEADERS["X-Ghostband-Token"]))
            ) as client,
            client.ws_connect("/ws", headers=HEADERS, protocols=["ghostband.secret"]) as socket,
        ):
            await socket.receive_json()
            await socket.receive_json()
            response = await client.post("/api/setup/retry", headers=HEADERS)
            assert response.status == 202
            async with asyncio.timeout(3):
                while True:
                    event = await socket.receive_json()
                    if event["event"] == "setup_status" and event["state"] == "failed":
                        break
            assert event["detail"] == "download interrupted"
            assert event["retryable"] is True

    asyncio.run(exercise())


def test_shutdown_does_not_wait_for_setup_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, finish, exited = Event(), Event(), Event()

    def install(paths: first_run.Paths) -> first_run.SetupStatus:
        entered.set()
        try:
            assert finish.wait(timeout=5)
            status = first_run.SetupStatus("ready", "ready")
            status.write(paths.status)
            return status
        finally:
            exited.set()

    monkeypatch.setattr(first_run, "install", install)

    async def exercise() -> None:
        client = TestClient(
            TestServer(create_app(Session(), token=HEADERS["X-Ghostband-Token"], auto_setup=True))
        )
        await client.start_server()
        assert await asyncio.to_thread(entered.wait, 2)
        async with asyncio.timeout(1):
            await client.close()
        assert not finish.is_set()

    try:
        asyncio.run(exercise())
    finally:
        finish.set()
        assert exited.wait(timeout=2)
