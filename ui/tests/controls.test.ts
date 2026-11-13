import { expect, test } from "vitest";
import {
  metronomeLabel,
  presetChoices,
  presetLabel,
  referenceLabel,
  type Preset,
} from "../src/controls";

const measured: Preset = {
  name: "Warm jazz",
  a: "warm jazz piano trio",
  b: "8-bit chiptune",
  measured: true,
};

const user: Preset = {
  name: "Late night",
  a: "ambient piano",
  b: "drum and bass",
  measured: false,
};

test("a measured preset shows its name alone", () => {
  expect(presetLabel(measured)).toBe("Warm jazz");
});

test("an unmeasured preset says so in words", () => {
  expect(presetLabel(user)).toBe("Late night (unmeasured)");
});

test("choices carry their index, label and both prompts", () => {
  expect(presetChoices([measured, user])).toEqual([
    {
      value: "0",
      label: "Warm jazz",
      title: "warm jazz piano trio / 8-bit chiptune",
    },
    {
      value: "1",
      label: "Late night (unmeasured)",
      title: "ambient piano / drum and bass",
    },
  ]);
});

test("the metronome names its state and shortcut in words", () => {
  expect(metronomeLabel(false)).toBe("Metronome off (K)");
  expect(metronomeLabel(true)).toBe("Metronome on (K)");
});

test("reference says why it is unavailable away from full tempo", () => {
  expect(referenceLabel(true, 0.5)).toBe(
    "Reference plays at 100 percent tempo",
  );
  expect(referenceLabel(false, 2)).toBe("Reference plays at 100 percent tempo");
});

test("reference names its on and off state at full tempo", () => {
  expect(referenceLabel(true, 1)).toBe("Reference on (O)");
  expect(referenceLabel(false, 1)).toBe("Reference off (O)");
});
