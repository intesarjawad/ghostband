import { describe, expect, test } from "vitest";
import {
  BASS,
  CHORD,
  LOWEST,
  STEP,
  offset,
  punch,
  timeline,
  type ChartChord,
} from "../src/roll";

const chord = (
  start: number,
  end: number,
  symbol: string,
  bass: number | null,
  pcs: number[],
): ChartChord => ({
  start_beat: start,
  end_beat: end,
  symbol,
  bass,
  pcs,
});

describe("punch", () => {
  const roll = punch([
    chord(0.5, 2.5, "G", 7, [7, 11, 2]),
    chord(2.5, 4.5, "N.C.", null, []),
  ]);
  const at = (pitch: number, beat: number): number =>
    roll.holes[(pitch - LOWEST) * roll.steps + Math.round(beat / STEP)] ?? -1;

  test("cuts each chord tone as a chain in the octave from G3", () => {
    for (const pitch of [55, 59, 62]) {
      expect(at(pitch, 0.5)).toBe(CHORD);
      expect(at(pitch, 2.3)).toBe(CHORD);
      expect(at(pitch, 2.45)).toBe(0);
    }
    expect(at(57, 1)).toBe(0);
  });

  test("strikes the bass once on every beat after the pickup", () => {
    expect(at(43, 0.5)).toBe(BASS);
    expect(at(43, 1.5)).toBe(BASS);
    expect(at(43, 1)).toBe(0);
    expect(at(43, 2.5)).toBe(0);
  });

  test("leaves a chord with no bass unpunched", () => {
    expect(at(36, 3.5)).toBe(0);
    expect(at(55, 3.5)).toBe(0);
  });
});

test("timeline maps beats and seconds both ways, beyond the log too", () => {
  const time = timeline([
    [0, 0.2],
    [1, 0.8],
    [2, 1.4],
  ]);
  expect(time.secondsAt(1.5)).toBeCloseTo(1.1);
  expect(time.beatAt(1.1)).toBeCloseTo(1.5);
  expect(time.beatAt(0)).toBeCloseTo(-1 / 3);
  expect(time.secondsAt(3)).toBeCloseTo(2);
});

test("offset signs late and early with a true minus", () => {
  expect(offset(0.007)).toBe("+7 ms");
  expect(offset(-0.078)).toBe("−78 ms");
});
