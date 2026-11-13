/** The messages exchanged with the conductor over /ws, and what the page keeps of them. */

import {
  loopActive,
  loopEndAt,
  loopStartAt,
  NO_LOOP,
  stepSlider,
  stepTempo,
  TEMPO_MAX,
  TEMPO_MIN,
  type LaneAction,
  type LoopRegion,
} from "./lane";

export type Rights = "public-domain" | "own-work" | "unknown";
export type ToggleName = "melody" | "drums" | "click" | "reference";
export type Extractor = "sheetsage2" | "permissive";

export const PROMPT_SLOTS = 6;
export const RIGHTS: readonly Rights[] = [
  "unknown",
  "own-work",
  "public-domain",
];
export const EXTRACTORS: readonly Extractor[] = ["permissive", "sheetsage2"];

export type ClientMessage =
  | { type: "transport"; action: "play" | "stop" }
  | { type: "transport"; action: "seek"; value: number }
  | { type: "slider"; value: number }
  | { type: "preset"; value: number }
  | { type: "prompt"; slot: number; text: string }
  | { type: "loop"; start: number; end: number }
  | { type: "loop" }
  | { type: "tempo_factor"; value: number }
  | { type: "record"; action: "start" | "stop" }
  | { type: "set_rights"; rights: Rights }
  | { type: "toggle"; name: ToggleName; enabled: boolean }
  | { type: "edit_chord"; index: number; symbol: string }
  | { type: "rename_section"; index: number; name: string }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "import"; upload_id: string; extractor: Extractor }
  | { type: "prompt_audio"; upload_id: string; slot: number };

export interface Interval {
  readonly start_beat: number;
  readonly end_beat: number;
}

export interface Chart {
  readonly title: string;
  readonly source: string;
  readonly rights: Rights;
  readonly extractor?: string;
  readonly tempo_bpm: number;
  readonly time_signature: readonly [number, number];
  readonly duration_beats: number;
  readonly sections: readonly (Interval & { readonly name: string })[];
  readonly chords: readonly (Interval & { readonly symbol: string })[];
}

export interface ChartGeometry {
  readonly beats_per_bar: number;
  readonly pulses_per_beat: number;
  readonly beat_phase: number;
  readonly bar_origin: number;
}

export interface SetupEvent {
  readonly event: "setup_status";
  readonly state: "not_started" | "installing" | "ready" | "failed";
  readonly stage: string;
  readonly done_bytes: number;
  readonly total_bytes: number;
  readonly detail: string;
  readonly retryable: boolean;
}

export interface SessionState {
  readonly event: "state";
  readonly chart: Chart | null;
  readonly chart_geometry: ChartGeometry | null;
  readonly transport: "playing" | "stopped";
  readonly tempo_factor: number;
  readonly tempo_range: readonly [number, number];
  readonly loop: readonly [number, number] | null;
  readonly slider: number;
  readonly preset: number;
  readonly slots: readonly string[];
  readonly melody: boolean;
  readonly drums: boolean;
  readonly click: boolean;
  readonly reference: boolean;
  readonly recording: boolean;
  readonly engine_health: { readonly online: boolean };
  readonly base_model: { readonly live: boolean; readonly message: string };
}

export interface ClockEvent {
  readonly event: "clock";
  readonly audible_beat: number;
  readonly now_s: number;
}

export interface HeardEvent {
  readonly event: "heard";
  readonly beat: number;
  readonly symbol: string;
  readonly confidence: number;
  readonly expected: string | null;
  readonly matches: boolean | null;
}

export interface ProgressEvent {
  readonly event: "import_progress";
  readonly stage: string;
  readonly pct: number;
}

export interface ErrorEvent {
  readonly event: "error";
  readonly code: string;
  readonly message: string;
  readonly fix: string;
}

export type ServerEvent =
  | SessionState
  | ClockEvent
  | HeardEvent
  | ProgressEvent
  | ErrorEvent
  | SetupEvent;

type Fields = Readonly<Record<string, unknown>>;

function isObject(value: unknown): value is Fields {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isLoop(value: unknown): boolean {
  return (
    value === null ||
    (Array.isArray(value) && value.length === 2 && value.every(isNumber))
  );
}

function isRange(value: unknown): boolean {
  if (!Array.isArray(value) || value.length !== 2) {
    return false;
  }
  const low: unknown = value[0];
  const high: unknown = value[1];
  return (
    isNumber(low) &&
    isNumber(high) &&
    TEMPO_MIN <= low &&
    low <= 1 &&
    1 <= high &&
    high <= TEMPO_MAX
  );
}

function isChart(value: unknown): boolean {
  if (value === null) {
    return true;
  }
  if (!isObject(value)) {
    return false;
  }
  const { sections, chords, time_signature: signature } = value;
  return (
    isString(value.title) &&
    isString(value.source) &&
    isString(value.rights) &&
    RIGHTS.includes(value.rights as Rights) &&
    isNumber(value.tempo_bpm) &&
    isNumber(value.duration_beats) &&
    Array.isArray(signature) &&
    signature.length === 2 &&
    signature.every(isNumber) &&
    Array.isArray(sections) &&
    sections.every((item) => isObject(item) && isString(item.name)) &&
    Array.isArray(chords) &&
    chords.every((item) => isObject(item) && isString(item.symbol))
  );
}

function isGeometry(value: unknown): boolean {
  return (
    isObject(value) &&
    Number.isInteger(value.beats_per_bar) &&
    isNumber(value.beats_per_bar) &&
    value.beats_per_bar > 0 &&
    Number.isInteger(value.pulses_per_beat) &&
    isNumber(value.pulses_per_beat) &&
    value.pulses_per_beat > 0 &&
    isNumber(value.beat_phase) &&
    value.beat_phase >= 0 &&
    value.beat_phase < 1 &&
    isNumber(value.bar_origin) &&
    value.bar_origin >= 0
  );
}

function isState(value: Fields): boolean {
  const health = value.engine_health;
  const model = value.base_model;
  return (
    isChart(value.chart) &&
    (value.chart === null
      ? value.chart_geometry === null
      : isGeometry(value.chart_geometry)) &&
    (value.transport === "playing" || value.transport === "stopped") &&
    isNumber(value.tempo_factor) &&
    isRange(value.tempo_range) &&
    isLoop(value.loop) &&
    isNumber(value.slider) &&
    isNumber(value.preset) &&
    Array.isArray(value.slots) &&
    value.slots.length === PROMPT_SLOTS &&
    value.slots.every(isString) &&
    isBoolean(value.melody) &&
    isBoolean(value.drums) &&
    isBoolean(value.click) &&
    isBoolean(value.reference) &&
    isBoolean(value.recording) &&
    isObject(health) &&
    isBoolean(health.online) &&
    isObject(model) &&
    isBoolean(model.live) &&
    isString(model.message)
  );
}

/** Check a decoded frame, so a malformed one is dropped instead of half-applied. */
export function parseServerEvent(value: unknown): ServerEvent | null {
  if (!isObject(value)) {
    return null;
  }
  switch (value.event) {
    case "state":
      return isState(value) ? (value as unknown as SessionState) : null;
    case "setup_status":
      return ["not_started", "installing", "ready", "failed"].includes(
        String(value.state),
      ) &&
        isString(value.stage) &&
        isString(value.detail) &&
        isBoolean(value.retryable) &&
        isNumber(value.done_bytes) &&
        value.done_bytes >= 0 &&
        isNumber(value.total_bytes) &&
        value.total_bytes >= 0
        ? (value as unknown as SetupEvent)
        : null;
    case "clock":
      return isNumber(value.audible_beat) && isNumber(value.now_s)
        ? (value as unknown as ClockEvent)
        : null;
    case "heard":
      return isNumber(value.beat) &&
        isString(value.symbol) &&
        isNumber(value.confidence) &&
        (value.expected === null || isString(value.expected)) &&
        (value.matches === null || isBoolean(value.matches))
        ? (value as unknown as HeardEvent)
        : null;
    case "import_progress":
      return isString(value.stage) && isNumber(value.pct)
        ? (value as unknown as ProgressEvent)
        : null;
    case "error":
      return isString(value.code) &&
        isString(value.message) &&
        isString(value.fix)
        ? (value as unknown as ErrorEvent)
        : null;
    default:
      return null;
  }
}

/** Both halves of the conductor's error: what went wrong, then what to do. */
export function errorText(error: ErrorEvent): string {
  return `${error.message} Next: ${error.fix}`;
}

export function beatsPerBar(state: SessionState): number {
  return state.chart_geometry?.beats_per_bar ?? 4;
}

/** The loop the conductor holds, in the lane's terms. */
export function loopRegion(state: SessionState): LoopRegion {
  return state.loop === null
    ? NO_LOOP
    : { startBeat: state.loop[0], endBeat: state.loop[1] };
}

/** Keep a half-set region the conductor has not seen; otherwise follow the conductor. */
export function mergeLoop(local: LoopRegion, state: SessionState): LoopRegion {
  if (state.loop !== null) {
    return loopRegion(state);
  }
  return loopActive(local) ? NO_LOOP : local;
}

/** The conductor takes a loop only whole, so a half-set region stays local. */
export function loopMessage(loop: LoopRegion): ClientMessage | null {
  if (loopActive(loop) && loop.startBeat !== null && loop.endBeat !== null) {
    return { type: "loop", start: loop.startBeat, end: loop.endBeat };
  }
  return null;
}

export function toggleMessage(
  name: ToggleName,
  state: SessionState,
): ClientMessage {
  return { type: "toggle", name, enabled: !state[name] };
}

export interface LaneContext {
  readonly state: SessionState;
  readonly beat: number;
  readonly loop: LoopRegion;
}

export interface LaneResult {
  readonly message: ClientMessage | null;
  readonly loop: LoopRegion;
}

/** Turn a lane key or button into the message it sends, given what the page last heard. */
export function laneMessage(
  action: LaneAction,
  context: LaneContext,
): LaneResult {
  const { state, beat, loop } = context;
  const send = (message: ClientMessage | null): LaneResult => ({
    message,
    loop,
  });
  switch (action) {
    case "play-toggle":
      return send({
        type: "transport",
        action: state.transport === "playing" ? "stop" : "play",
      });
    case "loop-start":
    case "loop-end": {
      const next = (action === "loop-start" ? loopStartAt : loopEndAt)(
        loop,
        beat,
        beatsPerBar(state),
        state.chart_geometry?.bar_origin ?? 0,
      );
      return { message: loopMessage(next), loop: next };
    }
    case "loop-clear":
      return { message: { type: "loop" }, loop: NO_LOOP };
    case "tempo-down":
    case "tempo-up":
      return send({
        type: "tempo_factor",
        value: stepTempo(
          state.tempo_factor,
          action === "tempo-up" ? 1 : -1,
          state.tempo_range,
        ),
      });
    case "slider-back":
    case "slider-forward":
      return send({
        type: "slider",
        value: stepSlider(state.slider, action === "slider-forward" ? 1 : -1),
      });
    case "melody":
    case "drums":
    case "click":
    case "reference":
      return send(toggleMessage(action, state));
    case "record":
      return send({
        type: "record",
        action: state.recording ? "stop" : "start",
      });
    case "undo":
    case "redo":
      return send({ type: action });
    case "shortcuts":
      return send(null);
  }
}
