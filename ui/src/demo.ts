import "./demo.css";
import {
  BASS,
  HIGHEST,
  LOWEST,
  ROWS,
  STEP,
  clock,
  loudness,
  measured,
  punch,
  timeline,
  type Roll,
  type ScoredTake,
} from "./roll";
import type { Frame, Layout, Renderer } from "./rollgl";

const TAKE = "oh-susanna-t1-o1-r2";
const GLIDE_MS = 650;
const LAMP_MS = 900;
/** How fast the room lights go down when the take plays, and come back up on pause. */
const HOUSE_MS = 700;
const SVG = "http://www.w3.org/2000/svg";
const INK = "#26190e";
const GRAPHITE = "#4e4a45";
const RED = "#a8231b";

type Heard = ScoredTake["heard"][number];
type Add = (
  name: string,
  attributes: Record<string, string | number>,
  text?: string,
) => SVGElement;

function byId(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (!element) throw new Error(`#${id} is missing from the page`);
  return element;
}

function adder(parent: SVGElement): Add {
  return (name, attributes, text) => {
    const node = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attributes))
      node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text;
    parent.append(node);
    return node;
  };
}

/** The chord heard on one beat, ringed in red where it is not the chart's chord. */
function heardMark(
  add: Add,
  beat: Heard,
  x: number,
  y: number,
  size: number,
): void {
  const differs = beat.heard !== null && beat.heard !== beat.expected;
  add(
    "text",
    {
      x,
      y,
      class: differs ? "heard off" : "heard",
      "font-size": Math.max(12, 14 * size),
      "text-anchor": "middle",
      "dominant-baseline": "central",
      fill: differs ? RED : GRAPHITE,
    },
    beat.heard ?? "?",
  );
  if (differs) {
    add("ellipse", {
      cx: x,
      cy: y,
      rx: 17 * size,
      ry: 11 * size,
      fill: "none",
      stroke: RED,
      "stroke-width": 1.2,
    });
  }
}

interface Placed extends Layout {
  readonly rest: number;
  readonly footer: number;
}

/** Fit the roll to the viewport: across the screen on a desk, upright like the piano on a phone. */
function place(machine: HTMLElement, leader: HTMLElement, roll: Roll): Placed {
  const width = machine.clientWidth;
  const height = machine.clientHeight;
  const vertical = width < 760;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (!vertical) {
    const top = 88;
    const footer = 64;
    const bar = Math.round(Math.min(400, Math.max(300, width * 0.24)));
    const perBeat = 120;
    const rest = -(leader.offsetWidth + 50 + 70) / perBeat;
    const tip = rest - (bar - 150) / perBeat;
    return {
      vertical,
      width,
      height,
      dpr,
      bar,
      paper: [top, height - footer],
      zone: [top + 64, height - footer - 70],
      perBeat,
      spool: [92, 24, 50],
      thickness: 0.2,
      leader: [tip, tip + 1.6, 70],
      holeRadius: [2.9, 4.1],
      pilot: [rest, 6, 0],
      rest,
      footer,
    };
  }
  // The case panel under the readout and transport; the paper stops at its top edge.
  const footer = 88;
  const tangent = 120;
  const perBeat = 64;
  // The tab hangs 68 px below the bar and stops just short of the readout.
  const tab = 68;
  const bar = Math.round(
    Math.max(height - footer - 8 - tab, tangent + leader.offsetHeight + 60),
  );
  const rest = -(leader.offsetHeight + 24 + 44) / perBeat;
  const tip = rest - tab / perBeat;
  return {
    vertical,
    width,
    height,
    dpr,
    bar,
    paper: [16, width - 16],
    zone: [16 + 46, width - 16 - 40],
    perBeat,
    spool: [tangent, 12, 66],
    thickness: ((56 ** 2 - 12 ** 2) * Math.PI) / (roll.end * perBeat),
    leader: [tip, tip + 1.2, 40],
    holeRadius: [1.9, 2.2],
    pilot: [rest, 4.4, 0],
    rest,
    footer,
  };
}

interface Inked {
  /** Printed and pencilled before the roll runs: bar lines, chords, the tune, what was heard. */
  readonly ink: SVGSVGElement;
  /** What the margin prints as each change crosses the bar. */
  readonly print: SVGSVGElement;
  readonly marks: readonly { beat: number; node: SVGGElement }[];
}

function ink(take: ScoredTake, at: Placed, roll: Roll): Inked {
  const { vertical, perBeat, rest, bar, paper, zone } = at;
  const along = (beat: number): number =>
    vertical ? bar - (beat - rest) * perBeat : bar + (beat - rest) * perBeat;
  const across = (pitch: number): number => {
    const share = (pitch - LOWEST + 0.5) / ROWS;
    return vertical
      ? zone[0] + share * (zone[1] - zone[0])
      : zone[1] - share * (zone[1] - zone[0]);
  };
  const xy = (beat: number, crossing: number): [number, number] =>
    vertical ? [crossing, along(beat)] : [along(beat), crossing];

  const ends = [along(at.leader[0]), along(roll.end + 3)];
  const low = Math.min(...ends) - 40;
  const high = Math.max(...ends) + 40;
  const sheet = (): [SVGSVGElement, SVGGElement] => {
    const svg = document.createElementNS(SVG, "svg");
    const group = document.createElementNS(SVG, "g");
    if (vertical) {
      svg.setAttribute("width", String(at.width));
      svg.setAttribute("height", String(high - low));
      svg.style.top = `${String(low)}px`;
      group.setAttribute("transform", `translate(0 ${String(-low)})`);
    } else {
      svg.setAttribute("width", String(high - low));
      svg.setAttribute("height", String(at.height));
      svg.style.left = `${String(low)}px`;
      group.setAttribute("transform", `translate(${String(-low)} 0)`);
    }
    svg.append(group);
    return [svg, group];
  };
  const [inkSvg, inkGroup] = sheet();
  const [printSvg, printGroup] = sheet();
  const add = adder(inkGroup);
  const ruleBetween = (
    beat: number,
    style: Record<string, string | number>,
  ): void => {
    const [x1, y1] = xy(beat, paper[0]);
    const [x2, y2] = xy(beat, paper[1]);
    add("line", { x1, y1, x2, y2, ...style });
  };
  const small = vertical ? 0.72 : 1;

  // Without WebGL the paper is a still, so the holes are drawn here to travel with the take.
  const punched = document.createElementNS(SVG, "g");
  punched.classList.add("punched");
  inkGroup.append(punched);
  const punch = adder(punched);
  // Every other step on a phone, as the shader punches it.
  const skip = vertical ? 2 : 1;
  for (let r = 0; r < ROWS; r++) {
    for (let k = 0; k < roll.steps; k++) {
      const kind = roll.holes[r * roll.steps + k] ?? 0;
      if (!kind) continue;
      let end = k;
      while (roll.holes[r * roll.steps + end + 1] === kind) end++;
      const [x1, y1] = xy(
        Math.ceil(k / skip) * skip * STEP,
        across(LOWEST + r),
      );
      const [x2, y2] = xy(end * STEP, across(LOWEST + r));
      punch("line", {
        x1,
        y1,
        x2,
        y2,
        stroke: INK,
        "stroke-width": 2 * at.holeRadius[kind === BASS ? 1 : 0],
        "stroke-linecap": "round",
        "stroke-dasharray": `0 ${String(skip * STEP * perBeat)}`,
      });
      k = end;
    }
  }

  const bars = take.chart.beats_per_bar;
  for (let beat = 0.5; beat <= roll.end; beat += bars) {
    ruleBetween(beat, { stroke: "rgb(38 25 14 / 0.14)", "stroke-width": 1 });
  }
  ruleBetween(-30 / perBeat, {
    stroke: INK,
    "stroke-width": 1.5,
    "stroke-dasharray": "2 4",
  });

  for (const section of take.chart.sections.filter(
    (s) => s.name !== "Pickup",
  )) {
    const [x, y] = vertical
      ? [paper[1] - 8, along(section.start_beat) - 6]
      : [along(section.start_beat) + 8, paper[0] + 20];
    add(
      "text",
      {
        x,
        y,
        "font-size": 15 * small,
        "font-style": "italic",
        "text-anchor": vertical ? "end" : "start",
      },
      section.name,
    );
  }

  // Chord names and what was heard go on the unblended layer so they can be lit in the dark room.
  const printed = adder(printGroup);
  const changes = measured(take);
  const chordAt = (beat: number): [number, number] =>
    vertical
      ? [paper[0] + 6, along(beat) - 5]
      : [along(beat) + 5, paper[0] + 46];
  let previous = "";
  for (const chord of take.chart.chords) {
    if (chord.symbol === previous) continue;
    previous = chord.symbol;
    if (changes.some((change) => change.beat === chord.start_beat)) continue;
    const [x, y] = chordAt(chord.start_beat);
    printed(
      "text",
      {
        x,
        y,
        class: "chord",
        "font-size": vertical ? 15 : 22,
        "font-weight": 500,
      },
      chord.symbol,
    );
  }

  const marks = changes.map((change) => {
    const node = document.createElementNS(SVG, "g");
    node.setAttribute("class", "change");
    const put = adder(node);
    const [x, y] = chordAt(change.beat);
    put(
      "text",
      {
        x,
        y,
        class: "chord",
        "font-size": vertical ? 15 : 22,
        "font-weight": 500,
      },
      change.after,
    );
    const [ox, oy] = vertical
      ? [paper[0] + 6, along(change.beat) + 11]
      : [x + 9 + change.after.length * 13, y];
    put(
      "text",
      {
        x: ox,
        y: oy,
        class: change.offset === null ? "offset missed" : "offset",
        "font-size": 14 * small,
        "font-style": "italic",
      },
      change.offset ?? "missed",
    );
    printGroup.append(node);
    return { beat: change.beat, node };
  });

  for (const beat of take.heard) {
    const [x, y] = vertical
      ? [paper[1] - 20, along(beat.beat + 0.5)]
      : [along(beat.beat + 0.5), paper[1] - 30];
    heardMark(printed, beat, x, y, small);
  }

  const thick = vertical ? 4.5 : 7;
  for (const note of take.chart.melody) {
    if (note.pitch < LOWEST || note.pitch > HIGHEST) continue;
    const [x0, y0] = xy(note.start_beat, across(note.pitch));
    const [x1, y1] = xy(note.end_beat - 0.04, across(note.pitch));
    const box = vertical
      ? {
          x: x0 - thick / 2,
          y: Math.min(y0, y1),
          width: thick,
          height: Math.abs(y1 - y0),
        }
      : { x: x0, y: y0 - thick / 2, width: x1 - x0, height: thick };
    // On the print layer, so the outlines can be lit in the dark room like the margin.
    printed("rect", {
      ...box,
      rx: 3,
      class: "pencil",
      "stroke-width": 1.1,
    });
  }

  if (!vertical) {
    const end = along(0) - 44;
    const note = (y: number, words: string): void => {
      add(
        "text",
        {
          x: end,
          y,
          "font-size": 13,
          "font-style": "italic",
          "text-anchor": "end",
        },
        words,
      );
    };
    note(across(70), "pencil: you");
    note(across(58), "holes: the band");
    note(across(41), "bass, every beat");
    note(paper[1] - 26, "heard");
  }

  return { ink: inkSvg, print: printSvg, marks };
}

interface Cut {
  readonly from: number;
  readonly to: number;
  readonly perBeat: number;
  /** Height of one pitch row; 0 leaves out the holes and pencil, keeping the margin alone. */
  readonly row: number;
  /** The excerpt that explains the roll: heard row, a lit bar, and the beat whose bass slot the Holes note points at. */
  readonly key?: { readonly bar: number; readonly holes: number };
  /** A line for the top right of the margin. */
  readonly aside?: string | undefined;
}

interface Drawn {
  readonly svg: SVGSVGElement;
  readonly width: number;
  /** Where each note beside the cut points, in drawing units, and on which side of it the note sits. */
  readonly marks: readonly Mark[];
}

interface Mark {
  readonly n: number;
  readonly x: number;
  readonly y: number;
  readonly below: boolean;
}

/** Offsets in ms plot at this many drawing units per ms; the guides sit at the 120 ms tolerance. */
const PLOT = 0.2;
/** No tick is drawn shorter than this, so a 2 ms offset still reads as early or late. */
const SHORTEST_TICK = 6;
const TOLERANCE_MS = 120;

/** A length of the roll laid flat: the same holes, pencil and margin as the machine, at any scale. */
function cut(take: ScoredTake, roll: Roll, spec: Cut): Drawn {
  const { from, to, perBeat, row, key } = spec;
  const first = Math.ceil(from / STEP - 1e-6);
  const last = Math.min(roll.steps - 1, Math.ceil(to / STEP - 1e-6) - 1);
  const pitches: number[] = [];
  for (let r = 0; r < ROWS; r++) {
    for (let k = first; k <= last; k++) {
      if (roll.holes[r * roll.steps + k]) {
        pitches.push(LOWEST + r);
        break;
      }
    }
  }
  const notes = take.chart.melody.filter(
    (n) =>
      n.end_beat > from &&
      n.start_beat < to &&
      n.pitch >= LOWEST &&
      n.pitch <= HIGHEST,
  );
  pitches.push(...notes.map((n) => n.pitch));
  const top = Math.max(...pitches) + 1;
  const bottom = Math.min(...pitches) - 1;
  // A key carries chord names and offsets in its margin; a measured length plots them.
  const all = measured(take);
  const changes = all.filter((c) => c.beat >= from && c.beat < to);
  const reach = TOLERANCE_MS * PLOT;
  const tick = (ms: number): number =>
    (Math.sign(ms) || 1) *
    Math.min(reach + 4, Math.max(SHORTEST_TICK, Math.abs(ms) * PLOT));
  const width = Math.round((to - from) * perBeat) + 24;
  const x = (beat: number): number => 12 + (beat - from) * perBeat;
  // Each label is centred beyond its tick's tip, a row further out where that is taken,
  // else beside the tick, whichever is clear of the ticks and labels before it. Placed relative to the zero line, they set
  // how tall the plot is, so no paper is left empty between the margin and the roll.
  type Box = [number, number, number, number];
  const taken: Box[] = [];
  // Clear of the torn ends too, which bite up to 6 units into each length.
  const clear = ([a, b, c, d]: Box): boolean =>
    a >= 10 &&
    c <= width - 10 &&
    taken.every(([e, f, g, h]) => c + 4 <= e || a >= g + 4 || d <= f || b >= h);
  for (const change of key ? [] : changes) {
    const cx = x(change.beat);
    if (change.ms === null) taken.push([cx - 5, -13, cx + 52, 5]);
    else
      taken.push([
        cx - 2,
        Math.min(0, tick(change.ms)),
        cx + 2,
        Math.max(0, tick(change.ms)),
      ]);
  }
  const labels = (key ? [] : changes).flatMap((change) => {
    if (change.ms === null) return [];
    const cx = x(change.beat);
    const sign = Math.sign(change.ms) || 1;
    const text = change.offset ?? "";
    const wide = text.length * 7.3;
    // Labels sit on rows 14 units apart, so ticks of different lengths never leave two half a row askew.
    const tip = Math.ceil((Math.abs(tick(change.ms)) + 8) / 14) * 14;
    const at = (out: number, a: number): Box => {
      const y = sign * (tip + out);
      return [a, y - 6, a + wide, y + 6];
    };
    const spots = [
      ...[0, 14, 28].map((out) => at(out, cx - wide / 2)),
      ...[0, 14, 28].flatMap((out) => [
        at(out, cx + 5),
        at(out, cx - 5 - wide),
      ]),
    ];
    const spot = spots.find(clear) ?? spots[0];
    if (!spot) return [];
    taken.push(spot);
    return [{ text, spot }];
  });
  const up = Math.max(reach, ...taken.map((b) => -b[1]));
  const down = Math.max(reach, ...taken.map((b) => b[3]));
  // A phone's names sit on two baselines, so the plot starts lower.
  const names = perBeat < 30 ? 2 : 1;
  const zero = key ? 0 : 26 + 14 * names + up;
  const margin = key ? 58 : zero + down + 4;
  const foot = key ? 40 : 10;
  const height = Math.round(margin + (top - bottom + 1) * row + foot);
  const y = (pitch: number): number => margin + (top - pitch + 0.5) * row;

  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${String(width)} ${String(height)}`);
  svg.setAttribute("aria-hidden", "true");
  const add = adder(svg);

  const bars = take.chart.beats_per_bar;
  for (let beat = 0.5; beat <= to; beat += bars) {
    if (beat < from) continue;
    add("line", {
      x1: x(beat),
      x2: x(beat),
      y1: 0,
      y2: height,
      stroke: "rgb(38 25 14 / 0.14)",
    });
  }
  if (spec.aside)
    add(
      "text",
      {
        x: width - 12,
        y: 15,
        "font-size": 13,
        "font-style": "italic",
        "text-anchor": "end",
      },
      spec.aside,
    );
  for (const section of take.chart.sections) {
    if (section.name === "Pickup") continue;
    if (section.start_beat < from || section.start_beat >= to) continue;
    add(
      "text",
      {
        x: x(section.start_beat) + 5,
        y: 15,
        "font-size": 13,
        "font-style": "italic",
      },
      section.name,
    );
  }

  // In a measured length the roll is only context for the ticks, so it is drawn faint.
  const rollGroup = document.createElementNS(SVG, "g");
  if (!key) rollGroup.setAttribute("opacity", "0.35");
  svg.append(rollGroup);
  const punch = adder(rollGroup);
  // Where the holes of a chain would touch, only every other step is drawn, as on a phone's roll.
  const skip = STEP * perBeat < 6 ? 2 : 1;
  for (let r = 0; row > 0 && r < ROWS; r++) {
    const pitch = LOWEST + r;
    let k = first;
    while (k <= last) {
      const kind = roll.holes[r * roll.steps + k] ?? 0;
      if (!kind) {
        k++;
        continue;
      }
      let end = k;
      while (end + 1 <= last && roll.holes[r * roll.steps + end + 1] === kind)
        end++;
      const radius = (kind === BASS ? 0.42 : 0.3) * row;
      punch("line", {
        x1: x(Math.ceil(k / skip) * skip * STEP),
        x2: x(end * STEP),
        y1: y(pitch),
        y2: y(pitch),
        stroke: INK,
        "stroke-width": radius * 2,
        "stroke-linecap": "round",
        "stroke-dasharray": `0 ${String(skip * STEP * perBeat)}`,
      });
      k = end + 1;
    }
  }

  const pencil = row > 6 ? 0.55 : 0.4;
  for (const note of row > 0 ? notes : []) {
    const x0 = x(Math.max(from, note.start_beat));
    const x1 = x(Math.min(to, note.end_beat - 0.04));
    punch("rect", {
      x: x0,
      y: y(note.pitch) - row * pencil,
      width: Math.max(1, x1 - x0),
      height: row * pencil * 2,
      rx: row * pencil,
      fill: "rgb(78 74 69 / 0.1)",
      stroke: "rgb(78 74 69 / 0.85)",
      "stroke-width": row > 6 ? 1.1 : 0.8,
    });
  }

  const marks: Mark[] = [];
  if (key) {
    // The case shows dark behind the bar, between the brass lips; only the holes on it are lit.
    const bx = x(key.bar);
    const top = margin - 10;
    const bottom = height - foot + 4;
    add("rect", {
      x: bx - 14,
      y: top,
      width: 28,
      height: bottom - top,
      fill: "#1a120a",
    });
    for (const side of [-1, 1]) {
      add("line", {
        x1: bx + side * 10,
        x2: bx + side * 10,
        y1: top,
        y2: bottom,
        stroke: "#c9a25a",
        "stroke-width": 2,
      });
    }
    const k = Math.round(key.bar / STEP);
    let lowest = 0;
    for (let r = 0; r < ROWS; r++) {
      const kind = roll.holes[r * roll.steps + k] ?? 0;
      if (!kind) continue;
      const radius = (kind === BASS ? 0.42 : 0.3) * row;
      const cy = y(LOWEST + r);
      // The halo spreads about two hole pitches, as on the roll above.
      add("circle", {
        cx: bx,
        cy,
        r: radius + 2 * STEP * perBeat,
        fill: "url(#halo)",
      });
      add("circle", { cx: bx, cy, r: radius + 0.4, fill: "#fffaf0" });
      lowest = Math.max(lowest, cy + radius + 2 * STEP * perBeat);
    }
    marks.push({ n: 3, x: bx, y: lowest + 2, below: true });
    const defs = document.createElementNS(SVG, "defs");
    defs.innerHTML =
      '<radialGradient id="halo"><stop offset="0.3" stop-color="#ffb347"/><stop offset="1" stop-color="#ffb347" stop-opacity="0"/></radialGradient>';
    svg.prepend(defs);
  }

  let previous = "";
  // Where a name would run into the one before, it drops to the second baseline.
  const ends: [number, number] = [-Infinity, -Infinity];
  for (const chord of take.chart.chords) {
    if (chord.symbol === previous) continue;
    const unscored =
      previous !== "" && !all.some((c) => c.beat === chord.start_beat);
    previous = chord.symbol;
    if (chord.start_beat < from || chord.start_beat >= to) continue;
    const name = unscored ? `${chord.symbol}†` : chord.symbol;
    const nx = x(chord.start_beat) + (perBeat < 30 ? 2 : 4);
    const line = names > 1 && nx < ends[0] + 14 ? 1 : 0;
    ends[line] = nx + name.length * (perBeat < 30 ? 7.5 : 10);
    add(
      "text",
      {
        x: nx,
        y: 34 + 14 * line,
        // A beat of a phone's measured length is narrower than "D7" at full size.
        "font-size": perBeat < 30 ? 12 : 16,
        "font-weight": 500,
        fill: INK,
      },
      name,
    );
  }

  if (key) {
    for (const change of changes) {
      add(
        "text",
        {
          x: x(change.beat) + 4,
          y: 50,
          "font-size": 13,
          "font-style": "italic",
          fill: change.offset === null ? RED : GRAPHITE,
        },
        change.offset ?? "missed",
      );
    }
    for (const beat of take.heard) {
      if (beat.beat < from || beat.beat + 1 > to) continue;
      heardMark(add, beat, x(beat.beat + 0.5), height - 18, 0.9);
    }
  } else {
    for (const ms of [-TOLERANCE_MS, 0, TOLERANCE_MS]) {
      add("line", {
        x1: x(from),
        x2: x(to),
        y1: zero + ms * PLOT,
        y2: zero + ms * PLOT,
        stroke: GRAPHITE,
        "stroke-width": ms === 0 ? 1 : 0.5,
        opacity: ms === 0 ? 0.75 : 0.4,
      });
    }
    for (const change of changes) {
      const cx = x(change.beat);
      if (change.ms === null) {
        for (const d of [-4, 4]) {
          add("line", {
            x1: cx - 4,
            x2: cx + 4,
            y1: zero - d,
            y2: zero + d,
            stroke: RED,
            "stroke-width": 1.4,
            "stroke-linecap": "round",
          });
        }
        add(
          "text",
          {
            x: cx + 7,
            y: zero - 6,
            "font-size": 13,
            "font-style": "italic",
            fill: RED,
          },
          "missed",
        );
        continue;
      }
      add("line", {
        x1: cx,
        x2: cx,
        y1: zero,
        y2: zero + tick(change.ms),
        stroke: INK,
        "stroke-width": 2,
        "stroke-linecap": "round",
      });
    }
    for (const { text, spot } of labels) {
      add(
        "text",
        {
          x: spot[0],
          y: zero + (spot[1] + spot[3]) / 2,
          class: "figure",
          "font-size": 12,
          "dominant-baseline": "central",
          fill: INK,
        },
        text,
      );
    }
  }

  if (key) {
    // The Holes note points from below at the bass slot struck on that beat.
    const at = Math.round(key.holes / STEP);
    for (let r = 0; r < ROWS; r++) {
      if (roll.holes[r * roll.steps + at] !== BASS) continue;
      marks.push({
        n: 1,
        x: x(key.holes + 0.1),
        y: y(LOWEST + r) + row * 0.42 + 2,
        below: true,
      });
      break;
    }
    const half = (from + to) / 2;
    const [tune] = notes
      .filter((n) => n.start_beat >= from && n.end_beat <= half)
      .sort((a, b) => b.end_beat - b.start_beat - (a.end_beat - a.start_beat));
    if (tune)
      marks.push({
        n: 2,
        x: x((tune.start_beat + tune.end_beat) / 2),
        y: y(tune.pitch) - row * pencil - 1,
        below: false,
      });
    // The found change nearest three quarters along the cut, pointed at above its chord name.
    const late = from + (to - from) * 0.75;
    const [timed] = [...changes].sort(
      (a, b) =>
        Number(a.offset === null) - Number(b.offset === null) ||
        Math.abs(a.beat - late) - Math.abs(b.beat - late),
    );
    if (timed) marks.push({ n: 4, x: x(timed.beat) + 2, y: 18, below: false });
    const differs = take.heard.find(
      (h) =>
        h.beat >= from &&
        h.beat + 1 <= to &&
        h.heard !== null &&
        h.heard !== h.expected,
    );
    if (differs)
      marks.push({
        n: 5,
        x: x(differs.beat + 0.5),
        y: height - 6,
        below: true,
      });
  }
  return { svg, width, marks };
}

function fill(take: ScoredTake): void {
  const { score, pool, chart } = take;
  const values: Record<string, string> = {
    title: chart.title,
    preset: take.take.preset,
    tempo: String(chart.tempo_bpm),
    metre: `${String(chart.beats_per_bar)}/4`,
    key: chart.key,
    length: clock(take.take.seconds),
    within: String(score.within_120),
    eligible: String(score.eligible),
    median: score.median_ms === null ? "n/a" : score.median_ms.toFixed(1),
    misses: score.misses === 1 ? "one" : String(score.misses),
    agreed: String(score.heard_agreed),
    compared: String(score.heard_compared),
    "heard-off": take.heard
      .filter((h) => h.heard !== null && h.heard !== h.expected)
      .map((h) => String(h.beat))
      .join(", "),
    "pool-takes": String(pool?.takes ?? ""),
    "pool-within": String(pool?.within_120 ?? ""),
    "pool-eligible": String(pool?.eligible ?? ""),
    take: take.take.id,
  };
  for (const element of document.querySelectorAll<HTMLElement>("[data-fill]")) {
    element.textContent = values[element.dataset.fill ?? ""] ?? "";
  }
  const rows = byId("changes") as HTMLTableSectionElement;
  for (const change of measured(take)) {
    const row = rows.insertRow();
    row.insertCell().textContent = String(change.beat);
    row.insertCell().textContent = `${change.before} to ${change.after}`;
    row.insertCell().textContent = change.offset ?? "missed";
  }
}

/**
 * Ragged ends for a piece torn off the roll, never the same twice. The tail is torn at its
 * start only and ends in a pointed tab mirroring the leader's: rightward on a desk, down on a phone.
 */
function tear(
  element: HTMLElement,
  seed: number,
  tab?: "right" | "down",
): void {
  const bite = (i: number): number => {
    const v = Math.sin(seed * 91.7 + i * 12.9898) * 43758.5453;
    return v - Math.floor(v);
  };
  // Teeth at uneven spacing and depth, with now and then a deeper bite, so no run of them repeats.
  const edge = (side: number): [number, number][] => {
    const points: [number, number][] = [];
    for (let at = 0, i = 1; at < 100; i++) {
      points.push([
        at,
        1 +
          5 * bite(side + 50 + i) * bite(side + 80 + i) +
          (bite(side + 300 + i) > 0.85 ? 4 : 0),
      ]);
      at = Math.min(100, at + 1.5 + 8 * bite(side + i) ** 2);
      if (at === 100) points.push([at, 1 + 5 * bite(side + 50)]);
    }
    return points;
  };
  const px = (n: number): string => `${n.toFixed(1)}px`;
  const pc = (n: number): string => `${n.toFixed(1)}%`;
  const start = edge(0);
  let outline: string[];
  if (tab === "down")
    outline = [
      ...start.map(([at, depth]) => `${pc(at)} ${px(depth)}`),
      "100% calc(100% - 77px)",
      "calc(50% + 40px) 100%",
      "calc(50% - 40px) 100%",
      "0% calc(100% - 77px)",
    ];
  else
    outline = [
      ...(tab === "right"
        ? [
            "calc(100% - 192px) 0%",
            "100% calc(50% - 70px)",
            "100% calc(50% + 70px)",
            "calc(100% - 192px) 100%",
          ]
        : edge(200).map(
            ([at, depth]) => `calc(100% - ${px(depth)}) ${pc(at)}`,
          )),
      ...start.reverse().map(([at, depth]) => `${px(depth)} ${pc(at)}`),
    ];
  element.style.clipPath = `polygon(${outline.join(", ")})`;
}

/** The roll's last bars at the scale of the roll above, running out to blank paper, for the tail. */
function runout(roll: Roll, vertical: boolean): SVGSVGElement {
  const perBeat = vertical ? 64 : 120;
  const row = vertical ? 6.2 : 12;
  const skip = vertical ? 2 : 1;
  const radius: readonly [number, number] = vertical ? [1.9, 2.2] : [2.9, 4.1];
  const hasHole = (k: number): boolean =>
    Array.from({ length: ROWS }, (_, r) => roll.holes[r * roll.steps + k]).some(
      Boolean,
    );
  let end = roll.steps - 1;
  while (end > 0 && !hasHole(end)) end--;
  // The tear falls where no chain is cut to a stub, so no stray holes sit off the rows that run out.
  const stub = (k: number): boolean =>
    Array.from({ length: ROWS }, (_, r) => r * roll.steps).some(
      (at) => roll.holes[at + k] && !roll.holes[at + k + 3],
    );
  let first = end - Math.round((vertical ? 2.5 : 2) / STEP);
  while (first < end && stub(first)) first++;
  const used = Array.from({ length: ROWS }, (_, r) => r).filter((r) =>
    roll.holes
      .slice(r * roll.steps + first, r * roll.steps + end + 1)
      .some(Boolean),
  );
  const low = Math.min(...used);
  const high = Math.max(...used);
  const length = Math.round((end - first) * STEP * perBeat) + 24;
  const breadth = Math.round((high - low + 2) * row);
  const xy = (k: number, r: number): [number, number] => {
    const along = 12 + (k - first) * STEP * perBeat;
    const across = ((vertical ? r - low : high - r) + 1) * row;
    return vertical ? [across, along] : [along, across];
  };
  const svg = document.createElementNS(SVG, "svg");
  const [w, h] = vertical ? [breadth, length] : [length, breadth];
  svg.setAttribute("viewBox", `0 0 ${String(w)} ${String(h)}`);
  svg.setAttribute("width", String(w));
  svg.setAttribute("height", String(h));
  const add = adder(svg);
  for (const r of used) {
    for (let k = first; k <= end; k++) {
      const kind = roll.holes[r * roll.steps + k] ?? 0;
      if (!kind) continue;
      let last = k;
      while (last < end && roll.holes[r * roll.steps + last + 1] === kind)
        last++;
      const [x1, y1] = xy(Math.ceil(k / skip) * skip, r);
      const [x2, y2] = xy(last, r);
      add("line", {
        x1,
        y1,
        x2,
        y2,
        stroke: INK,
        "stroke-width": 2 * radius[kind === BASS ? 1 : 0],
        "stroke-linecap": "round",
        "stroke-dasharray": `0 ${String(skip * STEP * perBeat)}`,
      });
      k = last;
    }
  }
  return svg;
}

/** The roll's key and its full length, cut to fit the width of the page. */
function strips(take: ScoredTake, roll: Roll): void {
  const key = byId("key-cut");
  const lengths = byId("lengths");
  const narrow = key.clientWidth < 700;
  const [from, to, bar] = narrow ? [22.5, 26.5, 25.5] : [20.5, 32.5, 26.5];
  const bars = take.chart.beats_per_bar;
  byId("key-where").textContent =
    `Bars ${String((from - 0.5) / bars + 1)} to ${String((to - 0.5) / bars)} of the roll, laid flat`;
  const drawn = cut(take, roll, {
    from,
    to,
    perBeat: (key.clientWidth - 24) / (to - from),
    row: narrow ? 7 : 9,
    // The Holes note points at a bass slot clear of the bar and the other notes' marks.
    key: { bar, holes: narrow ? 24.5 : 29.5 },
  });
  const upright = matchMedia("(max-width: 759px)").matches;
  byId("runout").replaceChildren(runout(roll, upright));
  tear(byId("tail"), 9, upright ? "down" : "right");
  const drawing = byId("key-drawing");
  drawing.replaceChildren(drawn.svg);
  marginalia(key, drawing, drawn);

  const span = 16;
  const pieces: SVGSVGElement[] = [];
  // A length punched exactly like one before it shows only its margin, and says which it repeats.
  const seen = new Map<string, number>();
  for (let start = 0.5; start < roll.end; start += span) {
    const first = Math.round(start / STEP);
    const steps = Math.round(span / STEP);
    const punched = Array.from({ length: ROWS }, (_, r) =>
      roll.holes
        .slice(r * roll.steps + first, r * roll.steps + first + steps)
        .join(""),
    ).join("|");
    const repeats = seen.get(punched);
    if (repeats === undefined) seen.set(punched, start);
    const bar = (beat: number): string => String((beat - 0.5) / bars + 1);
    pieces.push(
      cut(take, roll, {
        from: start === 0.5 ? 0 : start,
        to: Math.min(roll.end, start + span),
        perBeat: (lengths.clientWidth - 24) / (span + 0.5),
        // A phone keeps only the margin: the ticks are the measurement, and the roll is just above.
        row: narrow || repeats !== undefined ? 0 : 4,
        aside:
          repeats === undefined || narrow
            ? undefined
            : `Punched as bars ${bar(repeats)} to ${String((repeats + span - 0.5) / bars)}; only the timing is new`,
      }).svg,
    );
  }
  lengths.replaceChildren(
    ...pieces.map((piece, index) => {
      const paper = document.createElement("div");
      paper.className = "length";
      tear(paper, index + 3);
      paper.append(piece);
      return paper;
    }),
  );
}

/**
 * Set each note in the margin straight above or below its mark, joined to it by a pencil line.
 * Where the page is too narrow for that, each mark gets its note's number and the notes follow as a key.
 */
function marginalia(
  key: HTMLElement,
  drawing: HTMLElement,
  drawn: Drawn,
): void {
  const notes = [...key.querySelectorAll<HTMLElement>(".notes li")];
  const width = key.clientWidth;
  const scale = width / drawn.width;
  const gap = 16;
  const noteWidth = Math.min(200, width * 0.2);
  const tall = drawing.offsetHeight;
  key.querySelector(".leaders")?.remove();
  const leaders = document.createElementNS(SVG, "svg");
  leaders.classList.add("leaders");
  leaders.setAttribute("aria-hidden", "true");
  const add = adder(leaders);
  const keyed = width < 700;
  key.classList.toggle("keyed", keyed);
  if (keyed) {
    key.style.padding = "";
    for (const note of notes) note.removeAttribute("style");
    for (const mark of drawn.marks) {
      const cx = mark.x * scale;
      const cy = mark.y * scale + (mark.below ? 11 : -11);
      add("circle", { cx, cy, r: 9, class: "number" });
      add("text", { x: cx, y: cy, class: "number" }, String(mark.n));
    }
    key.append(leaders);
    return;
  }
  const placed = drawn.marks.flatMap((mark) => {
    const note = notes[mark.n - 1];
    return note
      ? [
          {
            note,
            x: mark.x * scale,
            y: mark.y * scale,
            below: mark.below,
            tier: 0,
          },
        ]
      : [];
  });
  for (const below of [false, true]) {
    const band = placed
      .filter((p) => p.below === below)
      .sort((a, b) => a.x - b.x);
    // Side by side when the notes fit, each starting just left of its own mark.
    let right = -gap;
    const lefts = band.map((p) => {
      const left = Math.max(p.x - 14, right + gap);
      right = left + noteWidth;
      return left;
    });
    const fits =
      width >= 700 &&
      right <= width &&
      band.every((p, i) => (lefts[i] ?? 0) <= p.x - 4);
    const set = (
      p: (typeof band)[number],
      left: number,
      wide: number,
      tier: number,
    ): void => {
      p.note.style.left = `${String(left)}px`;
      p.note.style.width = `${String(wide)}px`;
      p.tier = tier;
    };
    if (fits) {
      band.forEach((p, i) => {
        set(p, lefts[i] ?? 0, noteWidth, 0);
      });
      continue;
    }
    // Otherwise the notes stack outward, those whose marks sit nearest the edge closest in.
    // Each spans the widest stretch that leaves clear the marks of the notes beyond it, so
    // no pencil line crosses a note.
    const depth = (p: (typeof band)[number]): number =>
      below ? tall - p.y : p.y;
    // Marks within 40 px of the same depth count as level; then left to right.
    const order = [...band].sort(
      (a, b) =>
        Math.round(depth(a) / 40) - Math.round(depth(b) / 40) || a.x - b.x,
    );
    order.forEach((p, tier) => {
      const beyond = order.slice(tier + 1).map((q) => q.x);
      const left = Math.max(
        0,
        ...beyond.filter((x) => x < p.x).map((x) => x + gap),
      );
      const right = Math.min(
        width,
        ...beyond.filter((x) => x > p.x).map((x) => x - gap),
      );
      set(p, left, right - left, tier);
    });
  }
  // Each tier is as tall as its tallest note; tiers stack outward from the drawing.
  const reach = (below: boolean): number[] => {
    const tiers: number[] = [];
    for (const p of placed.filter((q) => q.below === below))
      tiers[p.tier] = Math.max(tiers[p.tier] ?? 0, p.note.offsetHeight);
    return tiers;
  };
  const offset = (tiers: number[], tier: number): number =>
    tiers.slice(0, tier).reduce((sum, h) => sum + h + gap, 0);
  const above = reach(false);
  const under = reach(true);
  const top = offset(above, above.length) + gap;
  const bottom = offset(under, under.length) + gap;
  key.style.padding = `${String(top)}px 0 ${String(bottom)}px`;

  for (const p of placed) {
    const height = p.note.offsetHeight;
    const y = p.below
      ? top + tall + gap + offset(under, p.tier)
      : top - gap - offset(above, p.tier) - height;
    p.note.style.top = `${String(y)}px`;
    const from = p.below ? y - 4 : y + height + 4;
    const to = top + p.y + (p.below ? 3 : -3);
    add("line", { x1: p.x, x2: p.x + (p.below ? -1 : 1), y1: from, y2: to });
    add("circle", { cx: p.x, cy: to, r: 1.8 });
  }
  key.append(leaders);
}

async function start(): Promise<void> {
  const machine = byId("machine");
  const canvas = byId("glass") as HTMLCanvasElement;
  const inkLayer = byId("ink");
  const printLayer = byId("print");
  const leader = byId("leader");
  const readout = byId("readout");
  const clockText = byId("clock");
  const scrub = byId("scrub") as HTMLInputElement;
  const status = byId("status");
  const playError = byId("play-error");
  const copy = byId("copy");
  copy.addEventListener("click", () => {
    const command = byId("command");
    const said = (word: string): void => {
      copy.textContent = word;
      status.textContent =
        word === "Copied" ? "Command copied" : "Command selected";
      setTimeout(() => (copy.textContent = "Copy"), 2000);
    };
    navigator.clipboard.writeText(command.textContent).then(
      () => {
        said("Copied");
      },
      // Where the clipboard is refused, the command is left selected to copy by hand.
      () => {
        getSelection()?.selectAllChildren(command);
        said("Selected");
      },
    );
  });
  const playButtons = [
    ...document.querySelectorAll<HTMLButtonElement>("[data-play]"),
  ];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");

  const source = (file: string): string =>
    new URL(`takes/${file}`, document.baseURI).href;
  const response = await fetch(source(`${TAKE}.json`));
  if (!response.ok)
    throw new Error(`Take request failed (${String(response.status)})`);
  const take = (await response.json()) as ScoredTake;
  fill(take);
  for (const [index, paper] of [
    ...document.querySelectorAll<HTMLElement>(".paper"),
  ].entries())
    if (!paper.classList.contains("tail")) tear(paper, index + 1);
  const roll = punch(take.chart.chords);
  const time = timeline(take.beats);
  const audio = new Audio(source(`${TAKE}.mp3`));
  audio.preload = "auto";
  scrub.max = String(take.take.seconds);
  const bars = take.chart.beats_per_bar;

  await document.fonts.ready;
  let at = place(machine, leader, roll);
  let inked: Inked | null = null;
  let renderer: Renderer | null = null;
  let started = false;
  let glideFrom: number | null = null;
  let glideStart = 0;
  let lampStart = 0;
  let shown = at.rest;
  let heardAt = -1;
  let printed = -1;
  let lastTime = -1;
  let lastStamp = 0;
  let visible = true;
  let frameRequest = 0;
  let house = 1;
  let drawn = "";
  let houseStamp = 0;

  const layout = (): void => {
    at = place(machine, leader, roll);
    const style = machine.style;
    style.setProperty("--bar", `${String(at.bar)}px`);
    if (at.vertical) {
      leader.style.bottom = `${String(at.height - at.bar + 24)}px`;
      style.setProperty(
        "--paper-clip",
        `inset(${String(at.spool[0])}px 16px ${String(at.footer)}px 16px)`,
      );
      // The print fades as it nears the case panel, so no figure is cut in half at its edge.
      const fade = `#000 calc(100% - ${String(at.footer + 24)}px), transparent calc(100% - ${String(at.footer)}px)`;
      style.setProperty(
        "--spool-mask",
        `linear-gradient(to bottom, transparent ${String(at.spool[0])}px, #000 ${String(at.spool[0] + 36)}px, ${fade})`,
      );
      style.setProperty("--case-mask", `linear-gradient(to bottom, ${fade})`);
    } else {
      leader.style.bottom = "";
      style.setProperty(
        "--paper-clip",
        `inset(${String(at.paper[0])}px 0 ${String(at.footer)}px ${String(at.spool[0])}px)`,
      );
      style.setProperty(
        "--spool-mask",
        `linear-gradient(to right, transparent ${String(at.spool[0])}px, #000 ${String(at.spool[0] + 40)}px)`,
      );
    }
    inked?.ink.remove();
    inked?.print.remove();
    inked = ink(take, at, roll);
    inkLayer.append(inked.ink);
    printLayer.append(inked.print);
    printed = -1;
    request();
  };

  const seconds = (): number => {
    let t = audio.currentTime;
    if (!audio.paused) {
      const stamp = performance.now();
      if (t !== lastTime) {
        lastTime = t;
        lastStamp = stamp;
      } else {
        t += ((stamp - lastStamp) / 1000) * audio.playbackRate;
      }
    }
    return t;
  };

  const ease = (p: number): number =>
    p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2;

  /** The margin prints a change's offset once the change has crossed the bar. */
  const print = (beat: number): void => {
    if (!inked) return;
    const count = started
      ? inked.marks.filter((mark) => mark.beat <= beat).length
      : 0;
    if (count === printed) return;
    const crossing = !audio.paused && count === printed + 1;
    inked.marks.forEach((mark, index) => {
      const on = index < count;
      mark.node.classList.toggle("shown", on);
      if (!on) mark.node.classList.remove("fresh");
    });
    if (crossing) inked.marks[count - 1]?.node.classList.add("fresh");
    printed = count;
  };

  const draw = (stamp: number): void => {
    frameRequest = 0;
    const t = started ? seconds() : 0;
    let beat = started ? time.beatAt(t) : at.rest;
    let moving = !audio.paused;
    if (glideFrom !== null) {
      const p = Math.min(1, (stamp - glideStart) / GLIDE_MS);
      beat = glideFrom + (beat - glideFrom) * ease(p);
      if (p < 1) moving = true;
      else glideFrom = null;
    }
    if (reduced.matches && moving)
      beat = Math.floor((beat - 0.5) / bars) * bars + 0.5;
    shown = beat;
    const shift = (at.rest - beat) * at.perBeat * (at.vertical ? -1 : 1);
    const transform = at.vertical
      ? `translate3d(0, ${String(shift)}px, 0)`
      : `translate3d(${String(shift)}px, 0, 0)`;
    if (inked) {
      inked.ink.style.transform = transform;
      inked.print.style.transform = transform;
    }
    leader.style.transform = transform;
    print(beat);

    if (started) {
      const heard = take.heard.find((h) => beat >= h.beat && beat < h.beat + 1);
      const index = Math.round(beat * 10);
      if (index !== heardAt) {
        heardAt = index;
        const chord = take.chart.chords.find(
          (c) => beat >= c.start_beat && beat < c.end_beat,
        );
        const differs = heard?.heard != null && heard.heard !== heard.expected;
        readout.hidden = !chord;
        readout.replaceChildren(
          `beat ${beat.toFixed(1)} · chart ${chord?.symbol ?? ""} · heard `,
        );
        const word = document.createElement("span");
        word.textContent = heard?.heard ?? "?";
        if (differs) word.className = "off";
        readout.append(word);
      }
    }
    clockText.textContent = `${clock(t)} / ${clock(take.take.seconds)}`;
    scrub.value = String(t);
    scrub.style.setProperty(
      "--played",
      `${String((t / take.take.seconds) * 100)}%`,
    );
    scrub.setAttribute(
      "aria-valuetext",
      `${clock(t)} of ${clock(take.take.seconds)}, beat ${beat.toFixed(1)}`,
    );

    const lamp = reduced.matches
      ? 1
      : Math.min(1, (stamp - lampStart) / LAMP_MS);
    // Paused mid-take the room stays a little dim, so the lit holes still read as light.
    // Without the lamp (no WebGL) the room stays lit: there is no light to dim it for.
    const lights =
      !renderer || audio.paused ? (started && renderer ? 0.75 : 1) : 0;
    house = reduced.matches
      ? lights
      : lights +
        (house - lights) * Math.exp(-(stamp - houseStamp) / (HOUSE_MS / 4));
    houseStamp = stamp;
    const settling = Math.abs(house - lights) > 0.002;
    leader.style.filter =
      house < 0.999 ? `brightness(${(0.07 + 0.93 * house).toFixed(3)})` : "";
    machine.classList.toggle("dark", house < 0.5);
    const frame: Frame = {
      now: beat,
      energy: started ? loudness(take, t) : [0, 0, 0],
      lamp,
      house,
      afterglow: reduced.matches ? 0 : 1,
    };
    // Under reduced motion the roll is redrawn once per bar, not once per frame.
    const key = reduced.matches
      ? `${beat.toFixed(2)} ${house.toFixed(2)} ${String(at.width)}x${String(at.height)}`
      : "";
    if (renderer && visible && (key === "" || key !== drawn)) {
      renderer.draw(at, frame);
      drawn = key;
    }
    if ((moving || lamp < 1 || settling) && visible) request();
  };

  function request(): void {
    if (!frameRequest) frameRequest = requestAnimationFrame(draw);
  }

  const label = (): void => {
    const playing = !audio.paused;
    for (const button of playButtons) {
      const word = button.querySelector("[data-play-word]");
      const text = playing
        ? "Pause"
        : audio.ended
          ? "Play again"
          : "Play the take";
      if (word) word.textContent = text;
      else button.setAttribute("aria-label", text);
      button
        .querySelector("[data-icon]")
        ?.setAttribute(
          "d",
          playing ? "M2 1 H5 V11 H2 Z M7 1 H10 V11 H7 Z" : "M2 1 L11 6 L2 11 Z",
        );
    }
  };

  const describe = (): string => {
    const chord = take.chart.chords.find(
      (c) => shown >= c.start_beat && shown < c.end_beat,
    );
    const heard = take.heard.find((h) => shown >= h.beat && shown < h.beat + 1);
    return `Beat ${shown.toFixed(1)}, chart ${chord?.symbol ?? "none"}, heard ${heard?.heard ?? "nothing"}.`;
  };

  // Once the roll moves, the leader and its button are on the spool; the transport takes over.
  const begin = (): void => {
    started = true;
    leader.inert = true;
    machine.classList.add("started");
  };

  const toggle = (): void => {
    if (audio.paused) {
      if ((!started || audio.ended) && !reduced.matches) {
        glideFrom = shown;
        glideStart = performance.now();
        if (audio.ended) audio.currentTime = 0;
      }
      const moveFocus = leader.contains(document.activeElement);
      begin();
      if (moveFocus) playButtons.at(-1)?.focus();
      void audio.play().catch(() => {
        playError.hidden = false;
        label();
      });
    } else {
      audio.pause();
      status.textContent = `Paused. ${describe()}`;
    }
  };

  const seek = (t: number): void => {
    begin();
    glideFrom = null;
    audio.currentTime = Math.max(0, Math.min(take.take.seconds, t));
    lastTime = -1;
    request();
  };

  const step = (direction: number): void => {
    const beat = started ? time.beatAt(audio.currentTime) : 0.5;
    const bar = Math.floor((beat - 0.5) / bars + 1e-6) * bars + 0.5;
    const target =
      direction > 0 ? bar + bars : beat - bar > 0.05 ? bar : bar - bars;
    seek(time.secondsAt(Math.max(0, target)));
    requestAnimationFrame(() => {
      status.textContent = describe();
    });
  };

  byId("retry-play").addEventListener("click", toggle);
  for (const button of playButtons) button.addEventListener("click", toggle);
  audio.addEventListener("play", () => {
    playError.hidden = true;
    houseStamp = performance.now();
    label();
    request();
  });
  audio.addEventListener("pause", () => {
    houseStamp = performance.now();
    label();
    request();
  });
  audio.addEventListener("ended", () => {
    label();
    request();
  });
  scrub.addEventListener("input", () => {
    seek(Number(scrub.value));
  });
  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target as HTMLElement;
    const native = target.closest("button, a, textarea, select");
    if (event.key === " " && !native) {
      event.preventDefault();
      toggle();
    } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      if (!target.closest("#machine") && target !== document.body) return;
      event.preventDefault();
      step(event.key === "ArrowRight" ? 1 : -1);
    }
  });
  new ResizeObserver(layout).observe(machine);
  let stripWidth = 0;
  new ResizeObserver(([entry]) => {
    const width = entry?.contentRect.width ?? 0;
    if (width === stripWidth) return;
    stripWidth = width;
    strips(take, roll);
  }).observe(byId("how"));
  new IntersectionObserver(([entry]) => {
    visible = entry?.isIntersecting ?? true;
    if (visible) request();
  }).observe(machine);
  reduced.addEventListener("change", request);
  layout();
  document.body.classList.remove("loading");
  byId("load-state").textContent = "";
  // ?t=16.2 opens the roll at that second of the take, so a moment can be linked.
  const linked = Number(new URLSearchParams(location.search).get("t"));
  if (linked > 0) seek(linked);

  // The text is on screen before the GPU starts; the lamp comes on once the shader is ready.
  requestAnimationFrame(() => {
    setTimeout(() => {
      void import("./rollgl")
        .then(({ createRenderer }) => createRenderer(canvas, roll))
        .catch(() => null)
        .then((made) => {
          renderer = made;
          if (!made) {
            machine.classList.add("still");
            machine.style.setProperty(
              "--still",
              `url(${source(at.vertical ? "roll-upright.webp" : "roll.webp")})`,
            );
            return;
          }
          lampStart = performance.now();
          canvas.classList.add("on");
          request();
        });
    }, 0);
  });
}

void start().catch(() => {
  const state = byId("load-state");
  state.textContent = "The recorded take could not be loaded. ";
  const retry = document.createElement("button");
  retry.textContent = "Retry";
  retry.addEventListener("click", () => {
    location.reload();
  });
  state.append(retry);
});
