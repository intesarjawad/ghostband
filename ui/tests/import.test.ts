import { expect, test } from "vitest";
import {
  DROP_ONE_FILE,
  DROP_PROMPT,
  IMPORT_STAGES,
  SHEETSAGE2_NOTE,
  START,
  advance,
  droppedFile,
  extractorBadge,
  fail,
  finish,
  isImportNotice,
  stageStatuses,
  stageText,
} from "../src/import";

const song = new File(["RIFF"], "greensleeves.wav");

test("a new import is uploading and nothing else has started", () => {
  expect(stageStatuses(START)).toEqual([
    "working",
    "waiting",
    "waiting",
    "waiting",
  ]);
});

test("each stage the conductor reports moves the import on", () => {
  const checking = advance(START, "validate");
  expect(stageStatuses(checking)).toEqual([
    "done",
    "working",
    "waiting",
    "waiting",
  ]);
  const extracting = advance(checking, "extract");
  expect(stageStatuses(extracting)).toEqual([
    "done",
    "done",
    "working",
    "waiting",
  ]);
  expect(stageStatuses(advance(extracting, "complete"))).toEqual([
    "done",
    "done",
    "done",
    "working",
  ]);
});

test("an unknown or earlier stage leaves the import where it was", () => {
  const extracting = advance(START, "extract");
  expect(advance(extracting, "decode")).toBe(extracting);
  expect(advance(extracting, "validate")).toBe(extracting);
});

test("the import ends with every stage done only after the last stage", () => {
  const extracting = advance(START, "extract");
  expect(finish(extracting)).toBe(extracting);
  const building = advance(extracting, "complete");
  expect(stageStatuses(finish(building))).toEqual([
    "done",
    "done",
    "done",
    "done",
  ]);
});

test("a rejection marks the stage it stopped at and nothing after it", () => {
  const failed = fail(advance(START, "validate"));
  expect(stageStatuses(failed)).toEqual([
    "done",
    "failed",
    "waiting",
    "waiting",
  ]);
  expect(advance(failed, "extract")).toBe(failed);
  expect(finish(failed)).toBe(failed);
});

test("every stage names its state in words", () => {
  expect(IMPORT_STAGES.map((stage) => stage.id)).toEqual([
    "upload",
    "validate",
    "extract",
    "complete",
  ]);
  expect(stageText("Extracting chords", "working")).toBe(
    "Extracting chords: in progress",
  );
  expect(stageText("Uploading", "failed")).toBe("Uploading: failed");
  expect(stageText("Building the chart", "waiting")).toBe(
    "Building the chart: waiting",
  );
  expect(stageText("Checking the file", "done")).toBe(
    "Checking the file: done",
  );
});

test("the SheetSage2 badge carries the non-commercial licence note", () => {
  expect(extractorBadge("sheetsage2")).toBe(
    "SheetSage2: weights licensed CC-BY-NC-4.0, for non-commercial use only",
  );
  expect(SHEETSAGE2_NOTE).toContain("non-commercial");
});

test("other extractors name themselves and carry no licence note", () => {
  expect(extractorBadge("permissive")).toBe("Permissive extractor");
  expect(extractorBadge("midi")).toBe("Read from a MIDI file");
  expect(extractorBadge("manual")).toBe("Written by hand");
  expect(extractorBadge("other")).toBe("other");
  expect(extractorBadge(null)).toBe("");
  expect(extractorBadge(undefined)).toBe("");
});

test("the long-recording and missing-model notices are not failures", () => {
  expect(isImportNotice("long")).toBe(true);
  expect(isImportNotice("fallback")).toBe(true);
  for (const code of ["silent", "too_short", "too_large", "format_mismatch"]) {
    expect(isImportNotice(code)).toBe(false);
  }
});

test("a single dropped file is taken", () => {
  expect(droppedFile([song])).toEqual({ file: song });
});

test("nothing or several dropped files say why they were not taken", () => {
  expect(droppedFile(null)).toEqual({ problem: DROP_PROMPT });
  expect(droppedFile([])).toEqual({ problem: DROP_PROMPT });
  expect(droppedFile([song, song])).toEqual({ problem: DROP_ONE_FILE });
});
