/** The lane: heard row, bar geometry, playhead, keyboard map and transport controls. */

export interface HeardUpdate {
  beat: number;
  symbol: string;
  confidence: number;
  expected: string | null;
  matches: boolean | null;
}

export interface HeardRow {
  readonly element: HTMLOListElement;
  update(next: HeardUpdate): void;
  clear(): void;
}

export const HEARD_ROWS = 8;

/** Say the verdict in words so colour alone never carries it. */
export function heardLabel(update: HeardUpdate): string {
  const confidence = String(Math.round(update.confidence * 100));
  const beat = String(update.beat);
  const heard = `Heard ${update.symbol} at beat ${beat} (${confidence}%)`;
  if (update.matches === null) {
    return heard;
  }
  if (update.matches) {
    return `${heard}: match`;
  }
  return `${heard}: mismatch, chart says ${update.expected ?? "no chord"}`;
}

export function createHeardRow(document: Document): HeardRow {
  const element = document.createElement("ol");
  element.className = "lane-heard";
  element.setAttribute("aria-label", "Heard chord");
  const update = (next: HeardUpdate): void => {
    const item = document.createElement("li");
    item.className = "lane-heard-item";
    item.dataset.match =
      next.matches === null ? "unknown" : String(next.matches);
    item.textContent = heardLabel(next);
    element.append(item);
    while (element.children.length > HEARD_ROWS) {
      element.firstElementChild?.remove();
    }
  };
  return {
    element,
    update,
    clear: () => {
      element.replaceChildren();
    },
  };
}

export const BARS_PER_SCREEN = 4;
export const LANE_HEIGHT_PX = 96;
export const TEMPO_STEP = 0.05;
export const TEMPO_MIN = 0.5;
export const TEMPO_MAX = 1.5;
export const SLIDER_STEP = 0.05;

export interface LaneGeometry {
  widthPx: number;
  beatsPerBar: number;
  beatPhase: number;
  barOrigin: number;
  zoom: number;
}

export interface Section {
  readonly name: string;
  readonly startBeat: number;
  readonly endBeat: number;
}

export interface SectionBand {
  readonly name: string;
  readonly leftPx: number;
  readonly widthPx: number;
}

/** Show whole bars’ worth of counted beats on each page. */
export function barsOnScreen(zoom: number): number {
  return Math.max(1, Math.round(BARS_PER_SCREEN / zoom));
}

export function pageBeats(geometry: LaneGeometry): number {
  return barsOnScreen(geometry.zoom) * geometry.beatsPerBar;
}

export function pxPerBeat(geometry: LaneGeometry): number {
  return geometry.widthPx / pageBeats(geometry);
}

export function pageStartBeat(beat: number, geometry: LaneGeometry): number {
  const page = pageBeats(geometry);
  return Math.floor(beat / page) * page;
}

/** Horizontal offset of a beat from the left edge of the page that holds it. */
export function beatToX(beat: number, geometry: LaneGeometry): number {
  return (beat - pageStartBeat(beat, geometry)) * pxPerBeat(geometry);
}

/** One x position per beat of a page; every page has the same lines. */
export function beatLineXs(geometry: LaneGeometry): readonly number[] {
  const step = pxPerBeat(geometry);
  return Array.from(
    { length: pageBeats(geometry) },
    (_, k) => (k + geometry.beatPhase) * step,
  );
}

export function sectionBands(
  sections: readonly Section[],
  pageStart: number,
  geometry: LaneGeometry,
): readonly SectionBand[] {
  const pageEnd = pageStart + pageBeats(geometry);
  const step = pxPerBeat(geometry);
  return sections
    .filter((s) => s.endBeat > pageStart && s.startBeat < pageEnd)
    .map((s) => {
      const from = Math.max(s.startBeat, pageStart);
      const to = Math.min(s.endBeat, pageEnd);
      return {
        name: s.name,
        leftPx: (from - pageStart) * step,
        widthPx: (to - from) * step,
      };
    });
}

/** Under reduced motion the playhead holds each beat instead of gliding. */
export function displayBeat(
  beat: number,
  reducedMotion: boolean,
  phase = 0,
): number {
  return reducedMotion ? Math.max(0, Math.floor(beat - phase) + phase) : beat;
}

export interface Playhead {
  /** Take an engine position report stamped with the audio clock. */
  tick(audioTimeS: number, beat: number): void;
  /** The beat to draw at an audio-clock time; never earlier than the last one drawn. */
  beatAt(audioTimeS: number): number;
  /** Forget the history after a seek or a loop wrap, which legitimately go back. */
  reset(): void;
}

export function createPlayhead(beatsPerSecond: number): Playhead {
  let anchor: { audioTimeS: number; beat: number } | null = null;
  let drawn = Number.NEGATIVE_INFINITY;
  return {
    tick(audioTimeS, beat) {
      if (anchor === null || audioTimeS > anchor.audioTimeS) {
        anchor = { audioTimeS, beat };
      }
    },
    beatAt(audioTimeS) {
      if (anchor === null) {
        return 0;
      }
      const raw =
        anchor.beat + (audioTimeS - anchor.audioTimeS) * beatsPerSecond;
      drawn = Math.max(drawn, raw);
      return drawn;
    },
    reset() {
      anchor = null;
      drawn = Number.NEGATIVE_INFINITY;
    },
  };
}

export function mirrorText(bar: number, chord: string | null): string {
  const label = bar === 0 ? "Pickup" : `Bar ${String(bar)}`;
  return chord === null ? label : `${label}, ${chord}`;
}

export interface Mirror {
  update(
    beat: number,
    beatsPerBar: number,
    chord: string | null,
    barOrigin?: number,
  ): void;
  reset(): void;
}

/** Writes once per bar, so a screen reader is not interrupted every beat. */
export function createMirror(write: (text: string) => void): Mirror {
  let lastBar = -1;
  return {
    update(beat, beatsPerBar, chord, barOrigin = 0) {
      const bar = Math.max(0, Math.floor((beat - barOrigin) / beatsPerBar) + 1);
      if (bar === lastBar) {
        return;
      }
      lastBar = bar;
      write(mirrorText(bar, chord));
    },
    reset() {
      lastBar = -1;
    },
  };
}

export interface LoopRegion {
  readonly startBeat: number | null;
  readonly endBeat: number | null;
}

export const NO_LOOP: LoopRegion = { startBeat: null, endBeat: null };

export function loopActive(loop: LoopRegion): boolean {
  return (
    loop.startBeat !== null &&
    loop.endBeat !== null &&
    loop.startBeat < loop.endBeat
  );
}

/** The loop covers whole bars; a start at or past the old end drops that end. */
export function loopStartAt(
  loop: LoopRegion,
  beat: number,
  beatsPerBar: number,
  barOrigin = 0,
): LoopRegion {
  const startBeat = Math.max(
    0,
    barOrigin + Math.floor((beat - barOrigin) / beatsPerBar) * beatsPerBar,
  );
  const keep = loop.endBeat !== null && loop.endBeat > startBeat;
  return { startBeat, endBeat: keep ? loop.endBeat : null };
}

export function loopEndAt(
  loop: LoopRegion,
  beat: number,
  beatsPerBar: number,
  barOrigin = 0,
): LoopRegion {
  const endBeat =
    barOrigin +
    (Math.floor((beat - barOrigin) / beatsPerBar) + 1) * beatsPerBar;
  const keep = loop.startBeat !== null && loop.startBeat < endBeat;
  return { startBeat: keep ? loop.startBeat : null, endBeat };
}

function stepped(value: number, direction: 1 | -1, step: number): number {
  return Math.round((value + direction * step) * 100) / 100;
}

export function stepTempo(
  factor: number,
  direction: 1 | -1,
  range: readonly [number, number] = [TEMPO_MIN, TEMPO_MAX],
): number {
  const next = stepped(factor, direction, TEMPO_STEP);
  return Math.min(range[1], Math.max(range[0], next));
}

/** Take one clock frame's position through the playhead, so a late frame cannot rewind it. */
export function clockBeat(
  playhead: Playhead,
  nowS: number,
  beat: number,
): number {
  playhead.tick(nowS, beat);
  return playhead.beatAt(nowS);
}

export function stepSlider(value: number, direction: 1 | -1): number {
  return Math.min(1, Math.max(0, stepped(value, direction, SLIDER_STEP)));
}

export type LaneAction =
  | "play-toggle"
  | "loop-start"
  | "loop-end"
  | "loop-clear"
  | "tempo-down"
  | "tempo-up"
  | "slider-back"
  | "slider-forward"
  | "melody"
  | "drums"
  | "click"
  | "reference"
  | "record"
  | "undo"
  | "redo"
  | "shortcuts";

export type KeyTarget = "text" | "button" | "range" | "other";

export interface KeyInput {
  readonly key: string;
  readonly metaKey: boolean;
  readonly ctrlKey: boolean;
  readonly altKey: boolean;
  readonly shiftKey: boolean;
  readonly target: KeyTarget;
}

/** Sort a focused element by which keys it already handles natively. */
export function keyTarget(
  element: {
    readonly tagName: string;
    getAttribute(name: string): string | null;
  } | null,
): KeyTarget {
  if (element === null) {
    return "other";
  }
  const tag = element.tagName.toLowerCase();
  const type = element.getAttribute("type");
  if (
    tag === "textarea" ||
    tag === "select" ||
    element.getAttribute("contenteditable") !== null ||
    (tag === "input" &&
      type !== "range" &&
      type !== "checkbox" &&
      type !== "radio")
  ) {
    return "text";
  }
  if (tag === "input" && type === "range") {
    return "range";
  }
  return tag === "button" || tag === "input" ? "button" : "other";
}

const LETTER_ACTIONS: Readonly<Record<string, LaneAction>> = {
  "[": "loop-start",
  "]": "loop-end",
  l: "loop-clear",
  "-": "tempo-down",
  "=": "tempo-up",
  m: "melody",
  d: "drums",
  k: "click",
  o: "reference",
  r: "record",
  "?": "shortcuts",
};

/** What a key press means for the lane, or null to leave it to the browser. */
export function keyAction(input: KeyInput): LaneAction | null {
  if (input.target === "text" || input.altKey) {
    return null;
  }
  const key = input.key.length === 1 ? input.key.toLowerCase() : input.key;
  if (input.metaKey || input.ctrlKey) {
    if (key !== "z") {
      return null;
    }
    return input.shiftKey ? "redo" : "undo";
  }
  if (key === " ") {
    return input.target === "other" ? "play-toggle" : null;
  }
  if (key === "ArrowLeft" || key === "ArrowRight") {
    if (input.target === "range") {
      return null;
    }
    return key === "ArrowLeft" ? "slider-back" : "slider-forward";
  }
  return LETTER_ACTIONS[key] ?? null;
}

export interface Shortcut {
  readonly keys: string;
  readonly description: string;
}

export const SHORTCUTS: readonly Shortcut[] = [
  { keys: "Space", description: "Play or stop" },
  { keys: "[", description: "Loop starts at the current bar" },
  { keys: "]", description: "Loop ends at the current bar" },
  { keys: "L", description: "Clear the loop" },
  { keys: "- / =", description: "Tempo down or up 5 percent" },
  { keys: "Left / Right", description: "Slider back or forward 5 percent" },
  { keys: "M", description: "Melody on or off" },
  { keys: "D", description: "Drums on or off" },
  { keys: "K", description: "Click on or off" },
  { keys: "O", description: "Reference on or off" },
  { keys: "R", description: "Record" },
  { keys: "Cmd+Z / Shift+Cmd+Z", description: "Undo or redo" },
  { keys: "?", description: "Show these shortcuts" },
];

export function createShortcutList(document: Document): HTMLDListElement {
  const list = document.createElement("dl");
  list.className = "lane-shortcuts";
  for (const shortcut of SHORTCUTS) {
    const term = document.createElement("dt");
    term.textContent = shortcut.keys;
    const detail = document.createElement("dd");
    detail.textContent = shortcut.description;
    list.append(term, detail);
  }
  return list;
}

interface TransportButton {
  readonly action: LaneAction;
  readonly label: string;
  readonly keys: string;
  readonly toggle: boolean;
}

const TRANSPORT_BUTTONS: readonly TransportButton[] = [
  { action: "play-toggle", label: "Play", keys: "Space", toggle: false },
  { action: "loop-start", label: "Loop start", keys: "[", toggle: false },
  { action: "loop-end", label: "Loop end", keys: "]", toggle: false },
  { action: "loop-clear", label: "Clear loop", keys: "L", toggle: false },
  { action: "melody", label: "Melody", keys: "M", toggle: true },
  { action: "drums", label: "Drums", keys: "D", toggle: true },
  { action: "record", label: "Record", keys: "R", toggle: true },
  { action: "undo", label: "Undo", keys: "Meta+Z", toggle: false },
  { action: "redo", label: "Redo", keys: "Meta+Shift+Z", toggle: false },
  { action: "shortcuts", label: "Shortcuts", keys: "?", toggle: false },
];

export interface TransportHandlers {
  act(action: LaneAction): void;
  tempo(factor: number): void;
  seek(fraction: number): void;
}

export interface Transport {
  readonly element: HTMLElement;
  setPlaying(playing: boolean): void;
  setPressed(action: LaneAction, pressed: boolean): void;
  setTempo(factor: number): void;
  setTempoBounds(range: readonly [number, number]): void;
  setPosition(fraction: number): void;
}

function createSlider(
  document: Document,
  name: string,
  text: string,
  range: { min: number; max: number },
  onInput: (value: number) => void,
): { label: HTMLLabelElement; input: HTMLInputElement } {
  const label = document.createElement("label");
  label.className = `lane-${name}`;
  label.append(text);
  const input = document.createElement("input");
  input.type = "range";
  input.name = name;
  input.min = String(range.min);
  input.max = String(range.max);
  input.step = "5";
  input.addEventListener("input", () => {
    onInput(input.valueAsNumber);
  });
  label.append(input);
  return { label, input };
}

/** Transport controls below the lane: real buttons and range inputs, each named. */
export function createTransport(
  document: Document,
  handlers: TransportHandlers,
): Transport {
  const element = document.createElement("div");
  element.className = "lane-transport";
  element.setAttribute("role", "group");
  element.setAttribute("aria-label", "Transport");
  const buttons = new Map<LaneAction, HTMLButtonElement>();
  for (const spec of TRANSPORT_BUTTONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = spec.label;
    button.setAttribute("aria-keyshortcuts", spec.keys);
    if (spec.toggle) {
      button.setAttribute("aria-pressed", "false");
    }
    button.addEventListener("click", () => {
      handlers.act(spec.action);
    });
    buttons.set(spec.action, button);
    element.append(button);
  }
  const tempo = createSlider(
    document,
    "tempo",
    "Tempo percent",
    { min: TEMPO_MIN * 100, max: TEMPO_MAX * 100 },
    (value) => {
      handlers.tempo(value / 100);
    },
  );
  const position = createSlider(
    document,
    "position",
    "Position percent",
    { min: 0, max: 100 },
    (value) => {
      handlers.seek(value / 100);
    },
  );
  element.append(tempo.label, position.label);
  tempo.input.value = "100";
  position.input.value = "0";
  return {
    element,
    setPlaying(playing) {
      const play = buttons.get("play-toggle");
      if (play) {
        play.textContent = playing ? "Stop" : "Play";
      }
    },
    setPressed(action, pressed) {
      buttons.get(action)?.setAttribute("aria-pressed", String(pressed));
    },
    setTempo(factor) {
      tempo.input.value = String(Math.round(factor * 100));
    },
    setTempoBounds(range) {
      const low = Math.round(range[0] * 100);
      const high = Math.round(range[1] * 100);
      tempo.input.min = String(low);
      tempo.input.max = String(high);
      const value = Number(tempo.input.value);
      tempo.input.value = String(Math.min(high, Math.max(low, value)));
    },
    setPosition(fraction) {
      position.input.value = String(Math.round(fraction * 100));
    },
  };
}

export interface LaneView {
  readonly beat: number;
  readonly geometry: LaneGeometry;
  readonly sections: readonly Section[];
  readonly chord: string | null;
  readonly reducedMotion: boolean;
}

export interface Lane {
  readonly element: HTMLElement;
  update(view: LaneView): void;
}

function paintBeatLines(
  context: CanvasRenderingContext2D,
  geometry: LaneGeometry,
  pageStart: number,
): void {
  context.clearRect(0, 0, geometry.widthPx, LANE_HEIGHT_PX);
  context.strokeStyle = "currentColor";
  beatLineXs(geometry).forEach((x, beat) => {
    const whole = pageStart + beat;
    context.lineWidth =
      (whole - Math.floor(geometry.barOrigin)) % geometry.beatsPerBar === 0
        ? 2
        : 1;
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, LANE_HEIGHT_PX);
    context.stroke();
  });
}

/** Full-width lane: section bands above, drawn beats, a playhead moved by transform alone. */
export function createLane(document: Document): Lane {
  const element = document.createElement("section");
  element.className = "lane";
  element.setAttribute("aria-label", "Lane");
  const bands = document.createElement("div");
  bands.className = "lane-bands";
  const stage = document.createElement("div");
  stage.className = "lane-stage";
  const canvas = document.createElement("canvas");
  canvas.className = "lane-canvas";
  canvas.height = LANE_HEIGHT_PX;
  const playhead = document.createElement("div");
  playhead.className = "lane-playhead";
  stage.append(canvas, playhead);
  const text = document.createElement("p");
  text.className = "lane-mirror";
  text.setAttribute("aria-live", "polite");
  element.append(bands, stage, text);
  const mirror = createMirror((next) => {
    text.textContent = next;
  });
  let drawnPage: { start: number; key: string } | null = null;
  const update = (view: LaneView): void => {
    const { geometry } = view;
    const shown = displayBeat(
      view.beat,
      view.reducedMotion,
      geometry.beatPhase,
    );
    const start = pageStartBeat(shown, geometry);
    const key = JSON.stringify([geometry, view.sections]);
    if (drawnPage?.start !== start || drawnPage.key !== key) {
      drawnPage = { start, key };
      canvas.width = geometry.widthPx;
      const context = canvas.getContext("2d");
      if (context) {
        paintBeatLines(context, geometry, start);
      }
      bands.replaceChildren(
        ...sectionBands(view.sections, start, geometry).map((band) => {
          const item = document.createElement("div");
          item.className = "lane-band";
          item.textContent = band.name;
          item.title = band.name;
          item.style.left = `${String(band.leftPx)}px`;
          item.style.width = `${String(band.widthPx)}px`;
          return item;
        }),
      );
    }
    playhead.style.transform = `translateX(${String(beatToX(shown, geometry))}px)`;
    mirror.update(
      view.beat,
      geometry.beatsPerBar,
      view.chord,
      geometry.barOrigin,
    );
  };
  return { element, update };
}
