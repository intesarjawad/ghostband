from collections import Counter

from ghostband.chart import Chart, ChordEvent, NoteEvent, Section
from ghostband.fake_engine import FakeEngine
from ghostband.scheduler import Scheduler
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule


class TransportMachine(RuleBasedStateMachine):
    """Exercise interleaved transport edits against the engine's held-note set."""

    def __init__(self) -> None:
        super().__init__()
        self.now_s = 0.0
        self.engine = FakeEngine(lambda: self.now_s)
        self.chart = Chart(
            schema_version=1,
            title="Overlapping voices",
            source="test",
            rights="own-work",
            extractor="manual",
            tempo_bpm=120,
            beat_times_s=[],
            time_signature=(4, 4),
            key="C",
            sections=[Section(name="A", start_beat=0, end_beat=4)],
            chords=[
                ChordEvent(start_beat=0, end_beat=1.5, symbol="C"),
                ChordEvent(start_beat=1.5, end_beat=2, symbol="N.C."),
                ChordEvent(start_beat=2, end_beat=4, symbol="F"),
            ],
            melody=[
                NoteEvent(start_beat=0, end_beat=1, pitch=60),
                NoteEvent(start_beat=0.5, end_beat=2.5, pitch=60),
                NoteEvent(start_beat=1, end_beat=3, pitch=72),
                NoteEvent(start_beat=3, end_beat=4, pitch=72),
            ],
            duration_beats=4,
        )
        self.scheduler = Scheduler(self.chart, self.engine.send, lookahead_s=0.2)
        self.loop: tuple[float, float] | None = None
        self.scheduler.play(self.now_s)
        self.scheduler.step(self.now_s)

    @rule(milliseconds=st.integers(min_value=0, max_value=1000))
    def advance(self, milliseconds: int) -> None:
        self.now_s += milliseconds / 1000
        self.scheduler.step(self.now_s)

    @rule()
    def stop(self) -> None:
        self.scheduler.stop(self.now_s)
        assert not self.engine.active_notes

    @rule()
    def play(self) -> None:
        self.scheduler.play(self.now_s)
        self.scheduler.step(self.now_s)

    @rule(quarter_beats=st.integers(min_value=0, max_value=15))
    def seek(self, quarter_beats: int) -> None:
        beat = quarter_beats / 4
        if self.loop is not None:
            start, end = self.loop
            beat = start + (beat % (end - start))
        self.scheduler.seek(beat, self.now_s)
        self.scheduler.step(self.now_s)

    @rule(factor=st.sampled_from([0.25, 0.5, 1.0, 1.5, 2.0, 4.0]))
    def tempo(self, factor: float) -> None:
        self.scheduler.set_tempo(factor, self.now_s)
        self.scheduler.step(self.now_s)

    @rule(loop=st.sampled_from([None, (0.0, 4.0), (0.5, 1.25), (1.0, 3.5), (2.25, 2.5)]))
    def select_loop(self, loop: tuple[float, float] | None) -> None:
        self.loop = loop
        self.scheduler.set_loop(loop, self.now_s)
        self.scheduler.step(self.now_s)

    @invariant()
    def notes_match_the_chart_at_the_latest_emitted_position(self) -> None:
        if not self.scheduler.running:
            assert not self.engine.active_notes
            return
        if not self.scheduler.events:
            return
        # Independently spelled expected harmony, including silence and overlaps.
        beat = self.scheduler.events[-1].beat
        harmony: set[int] = (
            {36, 55, 60, 64} if beat < 1.5 else {41, 57, 60, 65} if beat >= 2 else set()
        )
        melody = {
            note.pitch for note in self.chart.melody if note.start_beat <= beat < note.end_beat
        }
        assert self.engine.active_notes == harmony | melody

    def teardown(self) -> None:
        self.scheduler.stop(self.now_s)
        assert not self.engine.active_notes
        # A stop command must not conceal a missing release in the pitch stream.
        counts: Counter[int] = Counter()
        for event in self.scheduler.events:
            pitch = event.command["pitch"]
            assert isinstance(pitch, int)
            counts[pitch] += 1 if event.command["cmd"] == "noteon" else -1
            assert 0 <= counts[pitch] <= 1
        assert not +counts


TestTransport = TransportMachine.TestCase
TestTransport.settings = settings(
    max_examples=500, stateful_step_count=50, derandomize=True, deadline=None
)
