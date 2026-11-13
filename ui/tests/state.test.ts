import { expect, test } from "vitest";
import { NO_LOOP } from "../src/lane";
import {
  errorText,
  laneMessage,
  loopMessage,
  mergeLoop,
  parseServerEvent,
  type SessionState,
} from "../src/state";

const stopped: SessionState = {
  event: "state",
  chart: {
    title: "Greensleeves",
    source: "song.mid",
    rights: "unknown",
    tempo_bpm: 90,
    time_signature: [3, 4],
    duration_beats: 48,
    sections: [{ start_beat: 0, end_beat: 48, name: "A" }],
    chords: [{ start_beat: 0, end_beat: 3, symbol: "Am" }],
  },
  chart_geometry: {
    beats_per_bar: 3,
    pulses_per_beat: 1,
    beat_phase: 0,
    bar_origin: 0,
  },
  transport: "stopped",
  tempo_factor: 1,
  tempo_range: [0.5, 1.5],
  loop: null,
  slider: 0.5,
  preset: 0,
  slots: ["warm jazz piano trio", "8-bit chiptune", "", "", "", ""],
  melody: true,
  drums: true,
  click: false,
  reference: false,
  recording: false,
  engine_health: { online: false },
  base_model: { live: false, message: "Run ghostband bench first." },
};

const playing: SessionState = {
  ...stopped,
  transport: "playing",
  tempo_factor: 1.5,
  slider: 1,
  loop: [3, 9],
  recording: true,
  click: true,
};

test("a complete state frame is accepted as sent", () => {
  const frame: unknown = JSON.parse(JSON.stringify(stopped));
  expect(parseServerEvent(frame)).toEqual(stopped);
});

test("a state frame missing a field is dropped", () => {
  expect(parseServerEvent({ ...stopped, recording: undefined })).toBeNull();
  expect(parseServerEvent({ ...stopped, slots: ["one"] })).toBeNull();
  expect(parseServerEvent({ ...stopped, loop: [1] })).toBeNull();
  expect(parseServerEvent({ ...stopped, tempo_range: [0.5] })).toBeNull();
  expect(parseServerEvent({ ...stopped, tempo_range: [0.5, 2] })).toBeNull();
  expect(parseServerEvent({ ...stopped, tempo_range: [1.2, 1.3] })).toBeNull();
});

test("clock, heard, progress and error frames are accepted", () => {
  const frames = [
    { event: "clock", audible_beat: 4.5, now_s: 12 },
    {
      event: "heard",
      beat: 3,
      symbol: "C:maj",
      confidence: 0.75,
      expected: null,
      matches: null,
    },
    { event: "import_progress", stage: "decode", pct: 40 },
    {
      event: "error",
      code: "invalid_input",
      message: "Invalid input: bad",
      fix: "Run ghostband --help.",
    },
  ];
  for (const frame of frames) {
    expect(parseServerEvent(frame)).toEqual(frame);
  }
});

test("unknown events and wrong field types are dropped", () => {
  expect(parseServerEvent({ event: "hello" })).toBeNull();
  expect(parseServerEvent({ event: "clock", audible_beat: "4" })).toBeNull();
  expect(
    parseServerEvent({ event: "clock", audible_beat: Infinity, now_s: 1 }),
  ).toBeNull();
  expect(parseServerEvent([])).toBeNull();
  expect(parseServerEvent(null)).toBeNull();
});

test("an error reads as what happened, then what to do", () => {
  expect(
    errorText({
      event: "error",
      code: "invalid_input",
      message: "Invalid input: import a chart first",
      fix: "Run ghostband --help.",
    }),
  ).toBe("Invalid input: import a chart first Next: Run ghostband --help.");
});

test("a loop is sent only when both ends are set and in order", () => {
  expect(loopMessage({ startBeat: 3, endBeat: 9 })).toEqual({
    type: "loop",
    start: 3,
    end: 9,
  });
  expect(loopMessage({ startBeat: 3, endBeat: null })).toBeNull();
  expect(loopMessage({ startBeat: 9, endBeat: 3 })).toBeNull();
});

test("the conductor's loop wins, and a cleared loop drops the local one", () => {
  expect(mergeLoop({ startBeat: 0, endBeat: null }, playing)).toEqual({
    startBeat: 3,
    endBeat: 9,
  });
  expect(mergeLoop({ startBeat: 3, endBeat: 9 }, stopped)).toEqual(NO_LOOP);
  expect(mergeLoop({ startBeat: 6, endBeat: null }, stopped)).toEqual({
    startBeat: 6,
    endBeat: null,
  });
});

test("play toggles against the transport the conductor reported", () => {
  const context = { beat: 0, loop: NO_LOOP };
  expect(laneMessage("play-toggle", { ...context, state: stopped })).toEqual({
    message: { type: "transport", action: "play" },
    loop: NO_LOOP,
  });
  expect(
    laneMessage("play-toggle", { ...context, state: playing }).message,
  ).toEqual({ type: "transport", action: "stop" });
});

test("loop keys hold half a loop locally and send the whole one", () => {
  const start = laneMessage("loop-start", {
    state: stopped,
    beat: 4,
    loop: NO_LOOP,
  });
  expect(start).toEqual({
    message: null,
    loop: { startBeat: 3, endBeat: null },
  });
  expect(
    laneMessage("loop-end", { state: stopped, beat: 7, loop: start.loop }),
  ).toEqual({
    message: { type: "loop", start: 3, end: 9 },
    loop: { startBeat: 3, endBeat: 9 },
  });
  expect(
    laneMessage("loop-clear", {
      state: playing,
      beat: 0,
      loop: { startBeat: 3, endBeat: 9 },
    }),
  ).toEqual({ message: { type: "loop" }, loop: NO_LOOP });
});

test("tempo and slider steps stop at their limits", () => {
  const at = (state: SessionState) => ({ state, beat: 0, loop: NO_LOOP });
  expect(laneMessage("tempo-down", at(stopped)).message).toEqual({
    type: "tempo_factor",
    value: 0.95,
  });
  expect(laneMessage("tempo-up", at(playing)).message).toEqual({
    type: "tempo_factor",
    value: 1.5,
  });
  expect(laneMessage("slider-back", at(stopped)).message).toEqual({
    type: "slider",
    value: 0.45,
  });
  expect(laneMessage("slider-forward", at(playing)).message).toEqual({
    type: "slider",
    value: 1,
  });
  const narrowed: SessionState = {
    ...stopped,
    tempo_factor: 1.3,
    tempo_range: [0.7, 1.3],
  };
  expect(laneMessage("tempo-up", at(narrowed)).message).toEqual({
    type: "tempo_factor",
    value: 1.3,
  });
});

test("toggles and record flip the state the conductor reported", () => {
  const at = (state: SessionState) => ({ state, beat: 0, loop: NO_LOOP });
  expect(laneMessage("melody", at(stopped)).message).toEqual({
    type: "toggle",
    name: "melody",
    enabled: false,
  });
  expect(laneMessage("click", at(playing)).message).toEqual({
    type: "toggle",
    name: "click",
    enabled: false,
  });
  expect(laneMessage("reference", at(stopped)).message).toEqual({
    type: "toggle",
    name: "reference",
    enabled: true,
  });
  expect(laneMessage("record", at(stopped)).message).toEqual({
    type: "record",
    action: "start",
  });
  expect(laneMessage("record", at(playing)).message).toEqual({
    type: "record",
    action: "stop",
  });
  expect(laneMessage("undo", at(stopped)).message).toEqual({ type: "undo" });
  expect(laneMessage("redo", at(stopped)).message).toEqual({ type: "redo" });
  expect(laneMessage("shortcuts", at(stopped)).message).toBeNull();
});

test("server geometry governs compound meter and pickup loop boundaries", () => {
  const compound = {
    ...stopped,
    chart_geometry: {
      beats_per_bar: 2,
      pulses_per_beat: 3,
      beat_phase: 0.5,
      bar_origin: 0.5,
    },
  };
  expect(parseServerEvent(compound)).toEqual(compound);
  expect(
    parseServerEvent({ ...compound, chart_geometry: undefined }),
  ).toBeNull();
  expect(
    parseServerEvent({
      ...compound,
      chart_geometry: { ...compound.chart_geometry, beats_per_bar: 0 },
    }),
  ).toBeNull();
  expect(
    laneMessage("loop-start", { state: compound, beat: 2.7, loop: NO_LOOP })
      .loop.startBeat,
  ).toBe(2.5);
  expect(
    laneMessage("loop-end", { state: compound, beat: 0.2, loop: NO_LOOP }).loop
      .endBeat,
  ).toBe(0.5);
});

test("setup events validate byte counts, states and recovery fields", () => {
  const event = {
    event: "setup_status",
    state: "installing",
    stage: "models",
    done_bytes: 1024,
    total_bytes: 4096,
    detail: "",
    retryable: false,
  };
  expect(parseServerEvent(event)).toEqual(event);
  for (const patch of [
    { state: "mystery" },
    { done_bytes: -1 },
    { total_bytes: "4096" },
    { retryable: undefined },
  ]) {
    expect(parseServerEvent({ ...event, ...patch })).toBeNull();
  }
});
