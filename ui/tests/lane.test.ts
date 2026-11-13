import { expect, test } from "vitest";
import {
  barsOnScreen,
  beatLineXs,
  beatToX,
  clockBeat,
  createMirror,
  createPlayhead,
  displayBeat,
  heardLabel,
  keyAction,
  keyTarget,
  loopActive,
  loopEndAt,
  loopStartAt,
  NO_LOOP,
  pageBeats,
  pageStartBeat,
  pxPerBeat,
  sectionBands,
  SHORTCUTS,
  stepSlider,
  stepTempo,
  type HeardUpdate,
  type KeyInput,
  type LaneAction,
  type LaneGeometry,
  type Section,
} from "../src/lane";

const match: HeardUpdate = {
  beat: 4,
  symbol: "Am",
  confidence: 0.82,
  expected: "Am7",
  matches: true,
};

test("the heard row names a match in words", () => {
  expect(heardLabel(match)).toBe("Heard Am at beat 4 (82%): match");
});

test("the heard row names a mismatch in words, not only colour", () => {
  expect(heardLabel({ ...match, symbol: "C", matches: false })).toBe(
    "Heard C at beat 4 (82%): mismatch, chart says Am7",
  );
});

test("a beat without a chart chord has no verdict", () => {
  expect(heardLabel({ ...match, expected: null, matches: null })).toBe(
    "Heard Am at beat 4 (82%)",
  );
});

const wide: LaneGeometry = {
  widthPx: 1200,
  beatsPerBar: 4,
  beatPhase: 0,
  barOrigin: 0,
  zoom: 1,
};

test("four bars fill the lane at full zoom", () => {
  expect(barsOnScreen(1)).toBe(4);
  expect(pageBeats(wide)).toBe(16);
  expect(pxPerBeat(wide)).toBe(75);
});

test("zooming in shows fewer whole bars, never fewer than one", () => {
  expect(barsOnScreen(2)).toBe(2);
  expect(barsOnScreen(100)).toBe(1);
  expect(barsOnScreen(0.5)).toBe(8);
});

test("the playhead sits within a pixel of the beat line the lane draws", () => {
  const lines = beatLineXs(wide);
  expect(lines).toHaveLength(16);
  expect(lines[6]).toBe(450);
  for (const beat of [0, 1, 6, 15]) {
    expect(Math.abs(beatToX(beat, wide) - (lines[beat] ?? NaN))).toBeLessThan(
      1,
    );
  }
  expect(beatToX(6.5, wide)).toBe(487.5);
});

test("the playhead returns to the left edge on the next page", () => {
  expect(pageStartBeat(17, wide)).toBe(16);
  expect(beatToX(17, wide)).toBe(75);
});

test("section bands clip to the page and carry their names", () => {
  const sections: Section[] = [
    { name: "Verse", startBeat: 0, endBeat: 12 },
    { name: "Chorus", startBeat: 12, endBeat: 24 },
    { name: "Outro", startBeat: 24, endBeat: 32 },
  ];
  expect(sectionBands(sections, 0, wide)).toEqual([
    { name: "Verse", leftPx: 0, widthPx: 900 },
    { name: "Chorus", leftPx: 900, widthPx: 300 },
  ]);
  expect(sectionBands(sections, 16, wide)).toEqual([
    { name: "Chorus", leftPx: 0, widthPx: 600 },
    { name: "Outro", leftPx: 600, widthPx: 600 },
  ]);
});

test("reduced motion holds the playhead on the beat", () => {
  expect(displayBeat(6.9, true)).toBe(6);
  expect(displayBeat(6.9, false)).toBe(6.9);
});

test("the playhead follows the audio clock within 25 ms", () => {
  const playhead = createPlayhead(2);
  playhead.tick(10, 8);
  const toleranceBeats = 0.025 * 2;
  expect(Math.abs(playhead.beatAt(10.3) - 8.6)).toBeLessThanOrEqual(
    toleranceBeats,
  );
  expect(Math.abs(playhead.beatAt(11) - 10)).toBeLessThanOrEqual(
    toleranceBeats,
  );
});

test("a late tick never moves the playhead backward", () => {
  const playhead = createPlayhead(2);
  playhead.tick(10, 8);
  const drawn = [playhead.beatAt(10.2), playhead.beatAt(10.4)];
  playhead.tick(10.25, 8.1);
  drawn.push(playhead.beatAt(10.45), playhead.beatAt(10.5));
  expect(drawn).toHaveLength(4);
  [8.4, 8.8, 8.8, 8.8].forEach((beat, index) => {
    expect(drawn[index]).toBeCloseTo(beat, 9);
  });
  expect(drawn).toEqual([...drawn].sort((a, b) => a - b));
});

test("an out-of-order tick is ignored", () => {
  const playhead = createPlayhead(2);
  playhead.tick(10, 8);
  playhead.tick(9, 6);
  expect(playhead.beatAt(10.5)).toBe(9);
});

test("a reset lets the playhead go back after a seek", () => {
  const playhead = createPlayhead(2);
  playhead.tick(10, 8);
  playhead.beatAt(11);
  playhead.reset();
  playhead.tick(12, 0);
  expect(playhead.beatAt(12.5)).toBe(1);
});

test("the text mirror updates once per bar", () => {
  const written: string[] = [];
  const mirror = createMirror((text) => written.push(text));
  mirror.update(0, 4, "Am");
  mirror.update(1, 4, "Am");
  mirror.update(3.9, 4, "F");
  mirror.update(4, 4, "F");
  expect(written).toEqual(["Bar 1, Am", "Bar 2, F"]);
  mirror.reset();
  mirror.update(4.5, 4, null);
  expect(written).toEqual(["Bar 1, Am", "Bar 2, F", "Bar 2"]);
});

test("loop points snap to whole bars", () => {
  const start = loopStartAt(NO_LOOP, 5.5, 4);
  expect(start).toEqual({ startBeat: 4, endBeat: null });
  expect(loopActive(start)).toBe(false);
  const both = loopEndAt(start, 9, 4);
  expect(both).toEqual({ startBeat: 4, endBeat: 12 });
  expect(loopActive(both)).toBe(true);
});

test("a loop point that would invert the loop drops the other point", () => {
  const loop = { startBeat: 4, endBeat: 12 };
  expect(loopStartAt(loop, 12, 4)).toEqual({ startBeat: 12, endBeat: null });
  expect(loopEndAt(loop, 1, 4)).toEqual({ startBeat: null, endBeat: 4 });
});

test("tempo moves five points and stays inside its bounds", () => {
  expect(stepTempo(1, 1)).toBe(1.05);
  expect(stepTempo(1.05, -1)).toBe(1);
  expect(stepTempo(0.5, -1)).toBe(0.5);
  expect(stepTempo(1.5, 1)).toBe(1.5);
  expect(stepTempo(1.3, 1, [0.7, 1.3])).toBe(1.3);
  expect(stepTempo(0.7, -1, [0.7, 1.3])).toBe(0.7);
});

test("a clock frame moves the playhead on the audio clock without rewinding", () => {
  const playhead = createPlayhead(2);
  expect(clockBeat(playhead, 10, 8)).toBe(8);
  expect(clockBeat(playhead, 10.5, 8.6)).toBe(8.6);
  // A late frame reports an earlier beat, but the draw never goes back.
  expect(clockBeat(playhead, 10.6, 8.5)).toBe(8.6);
});

test("a slider moves five points and stays between zero and one", () => {
  expect(stepSlider(0.5, 1)).toBe(0.55);
  expect(stepSlider(0, -1)).toBe(0);
  expect(stepSlider(1, 1)).toBe(1);
});

function press(key: string, extra: Partial<KeyInput> = {}): LaneAction | null {
  return keyAction({
    key,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    target: "other",
    ...extra,
  });
}

test("each documented key maps to its action", () => {
  expect(press(" ")).toBe("play-toggle");
  expect(press("[")).toBe("loop-start");
  expect(press("]")).toBe("loop-end");
  expect(press("l")).toBe("loop-clear");
  expect(press("L")).toBe("loop-clear");
  expect(press("-")).toBe("tempo-down");
  expect(press("=")).toBe("tempo-up");
  expect(press("ArrowLeft")).toBe("slider-back");
  expect(press("ArrowRight")).toBe("slider-forward");
  expect(press("m")).toBe("melody");
  expect(press("d")).toBe("drums");
  expect(press("k")).toBe("click");
  expect(press("o")).toBe("reference");
  expect(press("r")).toBe("record");
  expect(press("?", { shiftKey: true })).toBe("shortcuts");
  expect(press("x")).toBeNull();
});

test("undo and redo take the command key, and other command keys pass through", () => {
  expect(press("z", { metaKey: true })).toBe("undo");
  expect(press("Z", { metaKey: true, shiftKey: true })).toBe("redo");
  expect(press("r", { metaKey: true })).toBeNull();
  expect(press("m", { altKey: true })).toBeNull();
});

test("keys typed into a text field are left alone", () => {
  expect(press("m", { target: "text" })).toBeNull();
  expect(press("z", { metaKey: true, target: "text" })).toBeNull();
});

test("a focused button or slider keeps the keys it already handles", () => {
  expect(press(" ", { target: "button" })).toBeNull();
  expect(press(" ", { target: "range" })).toBeNull();
  expect(press("ArrowLeft", { target: "range" })).toBeNull();
  expect(press("ArrowLeft", { target: "button" })).toBe("slider-back");
  expect(press("m", { target: "button" })).toBe("melody");
});

function fake(tagName: string, attributes: Record<string, string> = {}) {
  return {
    tagName,
    getAttribute: (name: string) => attributes[name] ?? null,
  };
}

test("focused elements are sorted by the keys they handle", () => {
  expect(keyTarget(null)).toBe("other");
  expect(keyTarget(fake("DIV"))).toBe("other");
  expect(keyTarget(fake("BUTTON"))).toBe("button");
  expect(keyTarget(fake("INPUT", { type: "checkbox" }))).toBe("button");
  expect(keyTarget(fake("INPUT", { type: "range" }))).toBe("range");
  expect(keyTarget(fake("INPUT", { type: "text" }))).toBe("text");
  expect(keyTarget(fake("INPUT"))).toBe("text");
  expect(keyTarget(fake("SELECT"))).toBe("text");
  expect(keyTarget(fake("TEXTAREA"))).toBe("text");
  expect(keyTarget(fake("DIV", { contenteditable: "true" }))).toBe("text");
});

test("every shortcut in the list names a key and what it does", () => {
  expect(SHORTCUTS).toHaveLength(13);
  for (const shortcut of SHORTCUTS) {
    expect(shortcut.keys).not.toBe("");
    expect(shortcut.description).not.toBe("");
  }
});

test("compound beats and bar accents follow the server pickup grid", () => {
  const geometry = { ...wide, beatsPerBar: 2, beatPhase: 0.5, barOrigin: 0.5 };
  expect(pageBeats(geometry)).toBe(8);
  expect(beatLineXs(geometry)).toEqual([
    75, 225, 375, 525, 675, 825, 975, 1125,
  ]);
  expect(displayBeat(1.8, true, 0.5)).toBe(1.5);
  expect(displayBeat(0.2, true, 0.5)).toBe(0);
  const written: string[] = [];
  const mirror = createMirror((text) => written.push(text));
  mirror.update(0.2, 2, "G", 0.5);
  mirror.update(0.5, 2, "G", 0.5);
  mirror.update(2.5, 2, "D", 0.5);
  expect(written).toEqual(["Pickup, G", "Bar 1, G", "Bar 2, D"]);
});
