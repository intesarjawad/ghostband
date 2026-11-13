import { expect, test } from "vitest";
import { parseRecordings } from "../src/workspace";

test("recording URLs stay on the authenticated local route", () => {
  const id = "a".repeat(32);
  const recording = { id, download_url: `/api/recordings/${id}` };
  expect(parseRecordings({ recordings: [recording] })).toEqual([recording]);
  expect(parseRecordings({ recordings: [] })).toEqual([]);
  for (const value of [
    null,
    {},
    { recordings: [null] },
    {
      recordings: [
        { ...recording, download_url: "https://example.com/steal-token" },
      ],
    },
    { recordings: [{ ...recording, id: "../chart" }] },
  ]) {
    expect(() => parseRecordings(value)).toThrow(/Invalid/);
  }
});
