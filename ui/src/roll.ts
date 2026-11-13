/** A scored take, as written by scripts/export_pages_take.py, and the roll punched from it. */

export interface ChartChord {
  readonly start_beat: number;
  readonly end_beat: number;
  readonly symbol: string;
  readonly bass: number | null;
  readonly pcs: readonly number[];
}

export interface Note {
  readonly start_beat: number;
  readonly end_beat: number;
  readonly pitch: number;
}

export interface ScoredTake {
  readonly chart: {
    readonly title: string;
    readonly tempo_bpm: number;
    readonly beats_per_bar: number;
    readonly key: string;
    readonly sections: readonly {
      name: string;
      start_beat: number;
      end_beat: number;
    }[];
    readonly chords: readonly ChartChord[];
    readonly melody: readonly Note[];
  };
  readonly take: {
    readonly id: string;
    readonly preset: string;
    readonly seconds: number;
  };
  readonly beats: readonly (readonly [number, number])[];
  readonly changes: readonly {
    beat: number;
    chart_s: number | null;
    found_s: number | null;
  }[];
  readonly heard: readonly {
    beat: number;
    expected: string;
    heard: string | null;
  }[];
  readonly score: {
    readonly eligible: number;
    readonly misses: number;
    readonly within_120: number;
    readonly median_ms: number | null;
    readonly heard_agreed: number;
    readonly heard_compared: number;
  };
  readonly pool?: { takes: number; eligible: number; within_120: number };
  readonly envelope_rate: number;
  readonly envelope: Readonly<Record<Register, readonly number[]>>;
}

export type Register = "low" | "mid" | "high";

/** Lowest and highest pitch rows on the paper. */
export const LOWEST = 36;
export const HIGHEST = 79;
export const ROWS = HIGHEST - LOWEST + 1;
/** Chord tones are punched in the octave from G3, the bass in the octave from C2. */
const CHORD_FLOOR = 55;
const BASS_FLOOR = 36;
/** Perforations are cut on a grid of this many beats; a chain of them is one sustained note. */
export const STEP = 0.05;
const BASS_LENGTH = 0.22;
const CHORD_GAP = 0.1;

export const BASS = 2;
export const CHORD = 1;

export interface Roll {
  /** One byte per (row, step): 0 paper, CHORD or BASS. Row 0 is the lowest pitch. */
  readonly holes: Uint8Array;
  readonly steps: number;
  readonly end: number;
}

const wrap = (pc: number): number => ((pc % 12) + 12) % 12;

/** Punch the chart: each chord tone as a chain of holes, the bass as one slot on every beat. */
export function punch(chords: readonly ChartChord[]): Roll {
  const end = Math.max(0, ...chords.map((chord) => chord.end_beat));
  const steps = Math.ceil(end / STEP) + 1;
  const holes = new Uint8Array(ROWS * steps);
  const cut = (pitch: number, from: number, to: number, kind: number): void => {
    const row = pitch - LOWEST;
    for (let k = Math.round(from / STEP); k <= Math.round(to / STEP); k++) {
      holes[row * steps + k] = kind;
    }
  };
  for (const chord of chords) {
    if (chord.bass === null) continue;
    for (const pc of chord.pcs) {
      cut(
        CHORD_FLOOR + wrap(pc - 7),
        chord.start_beat,
        chord.end_beat - CHORD_GAP,
        CHORD,
      );
    }
    // Beats fall on the half: the chart's first bar line follows a half-beat pickup.
    for (
      let beat = Math.ceil(chord.start_beat - 0.5) + 0.5;
      beat < chord.end_beat;
      beat++
    ) {
      cut(BASS_FLOOR + wrap(chord.bass), beat, beat + BASS_LENGTH, BASS);
    }
  }
  return { holes, steps, end };
}

export interface Timeline {
  beatAt(seconds: number): number;
  secondsAt(beat: number): number;
}

/** Chart beats against recording seconds, linear between logged beats and beyond them. */
export function timeline(
  beats: readonly (readonly [number, number])[],
): Timeline {
  const at = (x: number, from: 0 | 1, to: 0 | 1): number => {
    let i = 1;
    while (i < beats.length - 1 && x > (beats[i]?.[from] ?? Infinity)) i++;
    const a = beats[i - 1];
    const b = beats[i];
    if (!a || !b) return x;
    return a[to] + ((x - a[from]) / (b[from] - a[from])) * (b[to] - a[to]);
  };
  return { beatAt: (s) => at(s, 1, 0), secondsAt: (b) => at(b, 0, 1) };
}

/** The take's loudness in each register at a moment, 0 to 1. */
export function loudness(
  take: ScoredTake,
  seconds: number,
): [number, number, number] {
  const frame = Math.round(seconds * take.envelope_rate);
  const read = (band: readonly number[]): number =>
    frame < 0 ? 0 : (band[Math.min(frame, band.length - 1)] ?? 0);
  return [
    read(take.envelope.low),
    read(take.envelope.mid),
    read(take.envelope.high),
  ];
}

/** "+7 ms" late, "−78 ms" early, with a true minus sign. */
export function offset(seconds: number): string {
  const ms = Math.round(seconds * 1000);
  return `${ms < 0 ? "−" : "+"}${String(Math.abs(ms))} ms`;
}

export interface Measured {
  readonly beat: number;
  readonly before: string;
  readonly after: string;
  /** Measured offset in ms and as printed, or null when the detector never found the change. */
  readonly ms: number | null;
  readonly offset: string | null;
}

/** Each chart change with the chords either side and what was measured. */
export function measured(take: ScoredTake): Measured[] {
  const symbolAt = (beat: number): string =>
    take.chart.chords.find((c) => beat >= c.start_beat && beat < c.end_beat)
      ?.symbol ?? "N.C.";
  return take.changes.map((change) => {
    const error =
      change.found_s === null || change.chart_s === null
        ? null
        : change.found_s - change.chart_s;
    return {
      beat: change.beat,
      before: symbolAt(change.beat - STEP),
      after: symbolAt(change.beat),
      ms: error === null ? null : Math.round(error * 1000),
      offset: error === null ? null : offset(error),
    };
  });
}

export const clock = (seconds: number): string => {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60))}:${String(s % 60).padStart(2, "0")}`;
};
