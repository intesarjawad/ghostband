import json
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from ghostband import engine_process
from ghostband.bench import LOAD_TIMEOUT_S
from ghostband.chart import Chart
from ghostband.engine_client import Command, EngineClient
from ghostband.errors import GhostbandError
from ghostband.evaluation import live as live_report
from ghostband.fake_engine import FakeEngine
from ghostband.importing import read_chart
from ghostband.presets import Guidance
from ghostband.session import Session, measured_tempo_range

ROOT = Path(__file__).resolve().parents[2]


def test_connect_applies_active_guidance_without_a_preset_control() -> None:
    session = Session(fake_engine=True)
    initial = session.presets[0].model_copy(update={"guidance": Guidance(cfg_notes=6.0)})
    session.presets = (initial, *session.presets[1:])
    session.connect()
    session.connect()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    params = [row.command for row in engine.commands if row.command["cmd"] == "params"]
    assert params == [{"cmd": "params", **initial.guidance.model_dump()}]
    session.close()


def test_session_uses_scheduler_releases_notes_and_saves_recording(tmp_path: Path) -> None:
    now = [10.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    session.control({"type": "preset", "value": 0}, record_path=tmp_path / "take.wav")
    session.record("start", tmp_path / "take.wav")
    session.play()
    session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    assert engine.active_notes
    assert {row.command["cmd"] for row in engine.commands} >= {"prompt", "record", "play", "noteon"}
    now[0] = 10.5
    assert session.tick()["audible_beat"] == pytest.approx(0.36)
    session.control({"type": "slider", "value": 1}, record_path=tmp_path / "take.wav")
    for index in range(1, 31):
        now[0] = 10.5 + index * 0.05
        session.tick()
    assert engine.weights == (0, 1, 0, 0, 0, 0)
    session.close()
    assert engine.active_notes == set()
    assert engine.playing is False
    assert (tmp_path / "take.wav").stat().st_size == 44 + 96000 * 4


def test_session_validates_controls_and_history(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "record.wav"
    original = session.chart
    session.control({"type": "rename_section", "index": 0, "name": "Verse"}, record_path=path)
    assert session.chart is not None
    assert session.chart.sections[0].name == "Verse"
    session.control({"type": "undo"}, record_path=path)
    assert session.chart == original
    session.control({"type": "redo"}, record_path=path)
    assert session.chart is not None
    assert session.chart.sections[0].name == "Verse"
    invalid_controls: list[dict[str, object]] = [
        {"type": "slider", "value": 2},
        {"type": "tempo_factor", "value": -1},
        {"type": "loop", "start": 5, "end": 2},
        {"type": "preset", "value": 0.5},
        {"type": "toggle", "name": "other"},
        {"type": "edit_chord", "index": 0, "symbol": "invalid"},
    ]
    for invalid in invalid_controls:
        with pytest.raises(GhostbandError):
            session.control(invalid, record_path=path)
    session.close()


def test_tempo_range_is_reported_and_enforced(tmp_path: Path) -> None:
    session = Session(fake_engine=True, tempo_range=(0.7, 1.3))
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    assert session.state()["tempo_range"] == [0.7, 1.3]
    with pytest.raises(GhostbandError, match=r"between 0.7 and 1.3"):
        session.control({"type": "tempo_factor", "value": 1.4}, record_path=tmp_path)
    session.control({"type": "tempo_factor", "value": 1.3}, record_path=tmp_path)
    assert session.tempo_factor == 1.3
    session.close()


def test_tempo_range_comes_from_the_claims_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "claims.json").write_text(json.dumps({"tempo_range": [0.7, 1.3]}))
    monkeypatch.setattr(live_report, "default_directory", lambda: tmp_path)
    assert measured_tempo_range() == (0.7, 1.3)
    (tmp_path / "claims.json").write_text(json.dumps({"tempo_range": [0.3, 1.9]}))
    assert measured_tempo_range() == (0.5, 1.5)
    (tmp_path / "claims.json").write_text(json.dumps({"heard_agreement": {}}))
    assert measured_tempo_range() == (1.0, 1.0)
    failed: dict[str, object] = {"tempo_factors_pass": [], "tempo_range": None}
    (tmp_path / "claims.json").write_text(json.dumps(failed))
    assert measured_tempo_range() == (1.0, 1.0)


def test_chord_edit_keeps_timing_and_undo_redo_walk_the_history(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "record.wav"
    original = session.chart
    assert original is not None
    session.control({"type": "edit_chord", "index": 2, "symbol": " Bm7 "}, record_path=path)
    edited = session.chart
    assert edited is not None
    assert edited.chords[2].symbol == "Bm7"
    assert edited.chords[2].start_beat == original.chords[2].start_beat
    assert edited.chords[2].end_beat == original.chords[2].end_beat
    assert edited.chords[:2] + edited.chords[3:] == original.chords[:2] + original.chords[3:]
    session.control({"type": "rename_section", "index": 2, "name": "Chorus"}, record_path=path)
    session.control({"type": "undo"}, record_path=path)
    assert session.chart == edited
    session.control({"type": "undo"}, record_path=path)
    assert session.chart == original
    session.control({"type": "redo"}, record_path=path)
    assert session.chart == edited
    session.control({"type": "edit_chord", "index": 0, "symbol": "G"}, record_path=path)
    with pytest.raises(GhostbandError, match="no chart edit"):
        session.control({"type": "redo"}, record_path=path)
    state_chart = session.state()["chart"]
    assert isinstance(state_chart, dict)
    assert state_chart["chords"][0]["symbol"] == "G"
    assert state_chart["sections"][2]["name"] == "Refrain"
    session.close()


def test_rejected_edits_leave_chart_and_history_alone(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    path = tmp_path / "record.wav"
    with pytest.raises(GhostbandError, match="import a chart"):
        session.control({"type": "edit_chord", "index": 0, "symbol": "G"}, record_path=path)
    with pytest.raises(GhostbandError, match="no chart edit"):
        session.control({"type": "undo"}, record_path=path)
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    original = session.chart
    rejected: list[dict[str, object]] = [
        {"type": "edit_chord", "index": 18, "symbol": "G"},
        {"type": "edit_chord", "index": 0, "symbol": "   "},
        {"type": "edit_chord", "index": -1, "symbol": "G"},
        {"type": "rename_section", "index": 3, "name": "Bridge"},
        {"type": "rename_section", "index": 0, "name": "  "},
    ]
    for control in rejected:
        with pytest.raises(GhostbandError) as error:
            session.control(control, record_path=path)
        assert error.value.code.name == "INVALID_INPUT"
    assert session.chart == original
    with pytest.raises(GhostbandError, match="no chart edit"):
        session.control({"type": "undo"}, record_path=path)
    session.close()


def test_edit_that_changes_nothing_adds_no_history_and_loading_clears_it(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    chart = read_chart(ROOT / "charts/greensleeves.chart.json")
    session.load_chart(chart)
    path = tmp_path / "record.wav"
    session.control({"type": "edit_chord", "index": 0, "symbol": "Em"}, record_path=path)
    session.control({"type": "rename_section", "index": 1, "name": "Verse"}, record_path=path)
    with pytest.raises(GhostbandError, match="no chart edit"):
        session.control({"type": "undo"}, record_path=path)
    session.control({"type": "rename_section", "index": 1, "name": "Stanza"}, record_path=path)
    session.load_chart(chart)
    with pytest.raises(GhostbandError, match="no chart edit"):
        session.control({"type": "undo"}, record_path=path)
    session.close()


def test_edits_and_undo_while_playing_keep_the_position(tmp_path: Path) -> None:
    now = [10.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "record.wav"
    session.play()
    now[0] = 10.5
    session.tick()
    session.control({"type": "edit_chord", "index": 1, "symbol": "G"}, record_path=path)
    assert session.state()["transport"] == "playing"
    now[0] = 11.0
    # Resumes from beat 0.36 after the 0.2 s lookahead: 0.36 + (0.5 - 0.2) s * 1.2 beats/s.
    assert session.tick()["audible_beat"] == pytest.approx(0.72)
    session.control({"type": "undo"}, record_path=path)
    assert session.state()["transport"] == "playing"
    assert session.chart is not None
    assert session.chart.chords[1].symbol == "Em"
    session.close()


def test_session_selects_and_plays_a_user_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "Library/Application Support/ghostband/presets"
    folder.mkdir(parents=True)
    (folder / "late.toml").write_text(
        'name = "Late night"\na = "ambient piano"\nb = "drum and bass"\n'
        "[guidance]\ncfg_notes = 10.0\n",
        encoding="utf-8",
    )
    session = Session(fake_engine=True)
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    session.control({"type": "preset", "value": 4}, record_path=tmp_path / "take.wav")
    assert session.preset == 4
    assert session.slots[:2] == ["ambient piano", "drum and bass"]
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    prompts = [
        (row.command["slot"], row.command["text"])
        for row in engine.commands
        if row.command["cmd"] == "prompt"
    ]
    assert prompts == [(0, "ambient piano"), (1, "drum and bass")]
    assert any(
        row.command == {"cmd": "params", **session.presets[4].guidance.model_dump()}
        for row in engine.commands
    )
    session.play()
    session.tick()
    assert engine.playing is True
    session.close()


def test_play_without_chart_rejects_without_engine() -> None:
    with pytest.raises(GhostbandError, match="import a chart"):
        Session(fake_engine=True).play()


def test_restarting_controls_keep_clock_continuous(tmp_path: Path) -> None:
    now = [10.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "take.wav"
    session.play()
    now[0] = 11.0
    assert session.tick()["audible_beat"] == pytest.approx(0.96)
    session.control({"type": "transport", "action": "seek", "value": 4}, record_path=path)
    now[0] = 11.5
    assert session.tick()["audible_beat"] == pytest.approx(4.36)
    session.stop()
    now[0] = 12.0
    assert session.tick()["audible_beat"] == pytest.approx(4.36)
    session.close()


def test_melody_toggle_retains_exported_notes_and_rights(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "record.wav"
    session.control({"type": "toggle", "name": "melody", "enabled": False}, record_path=path)
    assert session.scheduler is not None
    assert session.scheduler.chart.melody == []
    session.control({"type": "set_rights", "rights": "own-work"}, record_path=path)
    assert session.chart is not None
    assert len(session.chart.melody) > 0
    assert session.chart.rights == "own-work"
    session.control({"type": "toggle", "name": "melody", "enabled": True}, record_path=path)
    assert len(session.scheduler.chart.melody) > 0
    session.close()


def test_session_socket_protocol_loads_records_and_surfaces_disconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [10.0]
    engine = FakeEngine(lambda: now[0])
    shutdown = Event()
    client_socket, server_socket = socket.socketpair()
    # The sockets close before the server thread is joined, so a send it blocks in ends.
    with ThreadPoolExecutor() as executor, client_socket, server_socket:
        serving = executor.submit(engine.serve, server_socket, shutdown)
        client = EngineClient(client_socket)

        def reuse(_engine: Path, _socket: Path) -> tuple[EngineClient, None]:
            return client, None

        monkeypatch.setattr(engine_process, "connect", reuse)
        monkeypatch.setenv("GHOSTBAND_RESOURCES", "/unused-resources")
        session = Session(clock=lambda: now[0])
        session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
        path = tmp_path / "take.wav"
        session.record("start", path)
        session.play()
        session.tick()
        now[0] = 10.5
        session.record("stop")
        session.stop()
        assert path.stat().st_size == 44 + 24000 * 4
        names = [row.command["cmd"] for row in engine.commands]
        assert "load" in names
        assert "record" in names
        assert "play" in names
        session.send({"cmd": "quit"})
        serving.result(timeout=2)
        server_socket.close()
        with pytest.raises(GhostbandError, match="Engine failed"):
            session.tick()
        with pytest.raises(GhostbandError, match="Engine failed"):
            session.close()
        assert session.engine is None
        assert session.state()["transport"] == "stopped"


def test_session_waits_for_a_cold_model_load(monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts: list[tuple[object, float]] = []

    class Engine(EngineClient):
        def request(self, command: Command, *, timeout_s: float = 2.0) -> Command:
            timeouts.append((command["cmd"], timeout_s))
            return {"ok": True}

    client_socket, server_socket = socket.socketpair()
    with client_socket, server_socket:
        engine = Engine(client_socket)

        def reuse(_engine: Path, _socket: Path) -> tuple[EngineClient, None]:
            return engine, None

        monkeypatch.setattr(engine_process, "connect", reuse)
        monkeypatch.setenv("GHOSTBAND_RESOURCES", "/unused-resources")
        session = Session()
        session.connect()
        session.close()

    # A first load after setup outlasts the default request timeout.
    assert timeouts == [("load", LOAD_TIMEOUT_S)]


def test_failed_close_while_recording_leaves_nothing_for_a_second_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [10.0]
    engine = FakeEngine(lambda: now[0])
    shutdown = Event()
    client_socket, server_socket = socket.socketpair()
    with ThreadPoolExecutor() as executor, client_socket, server_socket:
        serving = executor.submit(engine.serve, server_socket, shutdown)
        client = EngineClient(client_socket)

        def reuse(_engine: Path, _socket: Path) -> tuple[EngineClient, None]:
            return client, None

        monkeypatch.setattr(engine_process, "connect", reuse)
        monkeypatch.setenv("GHOSTBAND_RESOURCES", "/unused-resources")
        session = Session(clock=lambda: now[0])
        session.record("start", tmp_path / "take.wav")
        session.send({"cmd": "quit"})
        serving.result(timeout=2)
        server_socket.close()
        with pytest.raises(GhostbandError, match="Engine failed"):
            session.close()
        # A caller's own cleanup closes again; it must not replace the first error.
        session.close()

    assert not session.recording


def _clicks(engine: FakeEngine) -> list[tuple[int, bool]]:
    clicks: list[tuple[int, bool]] = []
    for row in engine.commands:
        if row.command["cmd"] != "click_at":
            continue
        sample, accent = row.command["sample"], row.command["accent"]
        assert type(sample) is int
        assert type(accent) is bool
        clicks.append((sample, accent))
    return clicks


def test_metronome_clicks_land_on_the_chart_beats(tmp_path: Path) -> None:
    now = [10.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "take.wav"
    session.control({"type": "toggle", "name": "click", "enabled": True}, record_path=path)
    session.play()
    for step in range(1, 46):
        now[0] = 10 + step * 0.05
        session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    # 72 BPM, 0.2 s pre-roll and a 1/3-beat pickup put beat n+1/3 at 22933+n*40000.
    assert _clicks(engine) == [(22933, True), (62933, False), (102933, True)]
    session.close()


def test_metronome_clicks_follow_a_tempo_change_from_the_next_beat(tmp_path: Path) -> None:
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0], tempo_range=(0.5, 1.5))
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "take.wav"
    session.control({"type": "toggle", "name": "click", "enabled": True}, record_path=path)
    session.play()
    now[0] = 0.05
    session.tick()
    now[0] = 0.1
    session.control({"type": "tempo_factor", "value": 1.5}, record_path=path)
    for step in range(2, 49):
        now[0] = step * 0.05
        session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    # The 1/3-beat pickup uses the old tempo; the faster factor starts at beat 1.
    assert _clicks(engine) == [
        (22933, True),
        (58489, False),
        (85156, True),
        (111822, False),
    ]
    session.close()


def test_metronome_toggle_stops_clicks_and_resumes_at_the_next_beat(tmp_path: Path) -> None:
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "take.wav"
    session.control({"type": "toggle", "name": "click", "enabled": True}, record_path=path)
    session.play()
    for step in range(1, 5):
        now[0] = step * 0.05
        session.tick()
    session.control({"type": "toggle", "name": "click", "enabled": False}, record_path=path)
    for step in range(5, 21):
        now[0] = step * 0.05
        session.tick()
    session.control({"type": "toggle", "name": "click", "enabled": True}, record_path=path)
    for step in range(21, 45):
        now[0] = step * 0.05
        session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    # Enabling again mid-beat skips the beat already sounding.
    assert _clicks(engine) == [(62933, False), (102933, True)]
    session.close()


def test_metronome_state_follows_the_toggle_and_stays_silent_while_stopped(
    tmp_path: Path,
) -> None:
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "take.wav"
    assert session.state()["click"] is False
    session.control({"type": "toggle", "name": "click", "enabled": True}, record_path=path)
    assert session.state()["click"] is True
    for step in range(1, 20):
        now[0] = step * 0.05
        session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    assert _clicks(engine) == []
    session.control({"type": "toggle", "name": "click", "enabled": False}, record_path=path)
    assert session.state()["click"] is False
    session.close()


def test_metronome_clicks_repeat_their_accents_when_a_loop_wraps(tmp_path: Path) -> None:
    now = [10.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    path = tmp_path / "take.wav"
    session.control({"type": "toggle", "name": "click", "enabled": True}, record_path=path)
    session.control({"type": "loop", "start": 2, "end": 4}, record_path=path)
    session.play()
    for step in range(1, 76):
        now[0] = 10 + step * 0.05
        session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    # The loop counts 2+1/3, 3+1/3, 2+1/3 with 40000 samples between beats at 72 BPM.
    assert _clicks(engine) == [
        (22933, True),
        (62933, False),
        (102933, True),
        (142933, False),
        (182933, True),
    ]
    session.close()


def test_session_sets_the_engine_onset_mode_its_scheduler_cues() -> None:
    for mode in (0, 1):
        session = Session(fake_engine=True, onset_mode=mode)
        session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
        session.play()
        engine = session.engine
        assert isinstance(engine, FakeEngine)
        assert engine.onset_mode == mode
        assert session.scheduler is not None
        assert session.scheduler.pulse is (mode == 1)
        session.close()
        assert session.scheduler.pulse is (mode == 1)


def test_dropped_frames_count_from_the_last_play(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(fake_engine=True)
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    session.play()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    statuses = iter([7, 10])

    def status() -> Command:
        return {
            "event": "status",
            "played_samples": 0,
            "latency_samples": 0,
            "underruns": next(statuses),
        }

    monkeypatch.setattr(engine, "status", status)
    session.tick()
    assert session.dropped_frames == 7
    session.stop()
    session.play()
    session.tick()
    assert session.dropped_frames == 3


def test_first_play_sends_both_prompts_and_preset_switch_keeps_blend(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    session.play()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    prompts = [row.command for row in engine.commands if row.command["cmd"] == "prompt"]
    assert [(row["slot"], row["text"]) for row in prompts] == [
        (0, session.presets[0].a),
        (1, session.presets[0].b),
    ]
    assert engine.weights == (1, 0, 0, 0, 0, 0)
    session.slew.jump(1.0)
    session.control({"type": "preset", "value": 1}, record_path=tmp_path)
    assert engine.weights == (0, 1, 0, 0, 0, 0)
    assert session.state()["slider"] == 1.0
    session.close()
    session.connect()
    assert isinstance(session.engine, FakeEngine)
    assert session.engine.weights == (0, 1, 0, 0, 0, 0)
    session.close()


def test_pickup_geometry_clicks_and_fractional_chords(tmp_path: Path) -> None:
    chart = Chart.model_validate(
        {
            "schema_version": 1,
            "title": "Pickup",
            "source": "test",
            "rights": "own-work",
            "extractor": "manual",
            "tempo_bpm": 60,
            "beat_times_s": [],
            "time_signature": [6, 8],
            "key": "C",
            "duration_beats": 4.5,
            "melody": [],
            "sections": [
                {"start_beat": 0, "end_beat": 0.5, "name": "Pickup"},
                {"start_beat": 0.5, "end_beat": 4.5, "name": "Verse"},
            ],
            "chords": [
                {"start_beat": 0, "end_beat": 0.5, "symbol": "G"},
                {"start_beat": 0.5, "end_beat": 4.5, "symbol": "C"},
            ],
        }
    )
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(chart)
    assert session.state()["chart_geometry"] == {
        "beats_per_bar": 2,
        "pulses_per_beat": 3,
        "beat_phase": 0.5,
        "bar_origin": 0.5,
    }
    assert chart.symbol_at(0.49) == "G"
    assert chart.symbol_at(0.5) == "C"
    assert chart.symbol_at(4.5) is None
    session.control({"type": "toggle", "name": "click", "enabled": True}, record_path=tmp_path)
    session.play()
    for step in range(61):
        now[0] = step * 0.05
        session.tick()
    assert isinstance(session.engine, FakeEngine)
    assert _clicks(session.engine) == [(33600, True), (81600, False), (129600, True)]
    session.close()


def test_missing_or_invalid_tempo_claims_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_report, "default_directory", lambda: tmp_path)
    assert measured_tempo_range() == (1.0, 1.0)
    (tmp_path / "claims.json").write_text("broken")
    assert measured_tempo_range() == (1.0, 1.0)
    (tmp_path / "claims.json").write_text('{"tempo_range": [1.1, 1.3]}')
    assert measured_tempo_range() == (1.0, 1.0)
