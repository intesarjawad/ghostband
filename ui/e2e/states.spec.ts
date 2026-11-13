import { expect, type Locator, type Page } from "@playwright/test";
import {
  test,
  Frames,
  ROOT,
  SONG,
  PROMPT_AUDIO,
  state,
  chart,
  type Frame,
} from "./live-server";

const press = (page: Page, target: Locator, key: string) => async () => {
  await target.focus();
  await page.keyboard.press(key);
};

test("every control message round-trips through the fake engine", async ({
  page,
  address,
}) => {
  test.setTimeout(120_000);
  const frames = new Frames();
  page.on("websocket", (socket) => {
    frames.watch(socket);
  });
  await page.goto(address);
  await expect.poll(() => frames.received.length).toBeGreaterThan(0);
  expect(frames.received[0]).toMatchObject({ event: "state", chart: null });

  await frames.roundTrip(
    async () => {
      await page.getByLabel("Song file").setInputFiles(SONG);
      await page.getByRole("button", { name: "Import" }).click();
    },
    { type: "import", upload_id: expect.any(String), extractor: "permissive" },
    state((frame) => chart(frame).source === "greensleeves.mid"),
  );
  await expect(page.getByText("(greensleeves.mid)")).toBeVisible();

  await frames.roundTrip(
    async () => {
      await page.getByLabel("Preset", { exact: true }).selectOption("1");
    },
    { type: "preset", value: 1 },
    state((frame) => frame.preset === 1),
  );

  await frames.roundTrip(
    press(page, page.getByLabel("Style percent"), "ArrowRight"),
    { type: "slider", value: 0.05 },
    state((frame) => frame.slider === 0.05),
  );

  await frames.roundTrip(
    async () => {
      await page.getByLabel("Slot", { exact: true }).selectOption("2");
      await page.getByLabel("Prompt text").fill("solo cello");
      await page.getByRole("button", { name: "Set prompt" }).click();
    },
    { type: "prompt", slot: 2, text: "solo cello" },
    state((frame) => (frame.slots as string[])[2] === "solo cello"),
  );
  await expect(page.getByText("Slot 3: solo cello")).toBeVisible();

  await frames.roundTrip(
    async () => {
      await page.getByLabel("Slot", { exact: true }).selectOption("3");
      await page.getByLabel("Prompt audio").setInputFiles(PROMPT_AUDIO);
      await page.getByRole("button", { name: "Use prompt audio" }).click();
    },
    { type: "prompt_audio", upload_id: expect.any(String), slot: 3 },
    state((frame) => (frame.slots as string[])[3] === "greensleeves.wav"),
  );

  const reference = page.getByRole("checkbox", { name: /Reference/ });
  const error = await frames.roundTrip(
    () => reference.click(),
    { type: "toggle", name: "reference", enabled: true },
    (frame) => frame.event === "error",
  );
  expect(error.code).toBe("invalid_input");
  await expect(page.locator('main > [role="alert"]')).toContainText(
    "import audio before enabling reference playback",
  );
  await expect(reference).not.toBeChecked();

  await frames.roundTrip(
    () => page.getByRole("button", { name: "Play" }).click(),
    { type: "transport", action: "play" },
    state((frame) => frame.transport === "playing"),
  );
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();

  await frames.roundTrip(
    press(page, page.getByLabel("Position percent"), "ArrowRight"),
    { type: "transport", action: "seek", value: expect.any(Number) },
    state((frame) => frame.transport === "playing"),
  );

  await frames.roundTrip(
    async () => {
      await page.getByRole("button", { name: "Loop start" }).click();
      await page.getByRole("button", { name: "Loop end" }).click();
    },
    { type: "loop", start: expect.any(Number), end: expect.any(Number) },
    state((frame) => Array.isArray(frame.loop)),
  );
  await frames.roundTrip(
    () => page.getByRole("button", { name: "Clear loop" }).click(),
    { type: "loop" },
    state((frame) => frame.loop === null),
  );

  await expect(page.getByLabel("Tempo percent")).toHaveAttribute("min", "100");
  await expect(page.getByLabel("Tempo percent")).toHaveAttribute("max", "100");

  for (const name of ["Melody", "Drums"]) {
    const toggle = page.getByRole("button", { name, exact: true });
    await frames.roundTrip(
      () => toggle.click(),
      { type: "toggle", name: name.toLowerCase(), enabled: false },
      state((frame) => frame[name.toLowerCase()] === false),
    );
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
  }

  await frames.roundTrip(
    () => page.getByRole("checkbox", { name: /Metronome/ }).click(),
    { type: "toggle", name: "click", enabled: true },
    state((frame) => frame.click === true),
  );
  await expect(page.getByText("Metronome on (K)")).toBeVisible();
  await frames.roundTrip(
    press(page, page.getByRole("checkbox", { name: /Metronome/ }), "k"),
    { type: "toggle", name: "click", enabled: false },
    state((frame) => frame.click === false),
  );
  await expect(page.getByText("Metronome off (K)")).toBeVisible();

  const record = page.getByRole("button", { name: "Record", exact: true });
  await frames.roundTrip(
    () => record.click(),
    { type: "record", action: "start" },
    state((frame) => frame.recording === true),
  );
  await frames.roundTrip(
    () => record.click(),
    { type: "record", action: "stop" },
    state((frame) => frame.recording === false),
  );

  await frames.roundTrip(
    () => page.getByLabel("Rights").selectOption("1"),
    { type: "set_rights", rights: "own-work" },
    state((frame) => chart(frame).rights === "own-work"),
  );

  await frames.roundTrip(
    async () => {
      await page
        .getByLabel("Chord", { exact: true })
        .selectOption({ index: 0 });
      await page.getByLabel("New symbol").fill("G7");
      await page.getByRole("button", { name: "Change chord" }).click();
    },
    { type: "edit_chord", index: 0, symbol: "G7" },
    state((frame) => (chart(frame).chords as Frame[])[0]?.symbol === "G7"),
  );

  const sectionName = (frame: Frame): unknown =>
    (chart(frame).sections as Frame[])[0]?.name;
  await frames.roundTrip(
    async () => {
      await page.getByLabel("Section", { exact: true }).selectOption({
        index: 0,
      });
      await page.getByLabel("New name").fill("Verse");
      await page.getByRole("button", { name: "Rename section" }).click();
    },
    { type: "rename_section", index: 0, name: "Verse" },
    state((frame) => sectionName(frame) === "Verse"),
  );

  await expect(page.locator(".lane-band").first()).toHaveText("Verse");
  await frames.roundTrip(
    () => page.getByRole("button", { name: "Undo" }).click(),
    { type: "undo" },
    state((frame) => sectionName(frame) !== "Verse"),
  );
  await frames.roundTrip(
    () => page.getByRole("button", { name: "Redo" }).click(),
    { type: "redo" },
    state((frame) => sectionName(frame) === "Verse"),
  );

  await frames.roundTrip(
    () => page.getByRole("button", { name: "Stop" }).click(),
    { type: "transport", action: "stop" },
    state((frame) => frame.transport === "stopped"),
  );
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
});

test("import controls disclose the licence and contain long titles", async ({
  page,
  address,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(address);
  const note = page.getByText(/weights licensed CC-BY-NC-4.0/);
  await expect(note).toBeHidden();
  await page.getByLabel("Extractor").selectOption("1");
  await expect(note).toBeVisible();
  await expect(page.locator(".lane-shortcuts")).toBeHidden();
  await page.getByRole("button", { name: "Shortcuts" }).click();
  await expect(page.locator(".lane-shortcuts")).toBeVisible();
  const chartFile = JSON.parse(
    await (
      await import("node:fs/promises")
    ).readFile(`${ROOT}charts/oh-susanna.chart.json`, "utf8"),
  ) as Record<string, unknown>;
  chartFile.title = "LongUnbrokenTitle".repeat(30);
  await page.getByLabel("Song file").setInputFiles({
    name: "long.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(chartFile)),
  });
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.getByText("Chart ready:", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
});
