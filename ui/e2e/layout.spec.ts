import { mkdir, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { expect } from "@playwright/test";
import { test, ROOT, type Frame } from "./live-server";

const SHOTS = `${homedir()}/.cache/ghostband/e2e-shots`;

for (const size of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`chart controls contain long sections at ${String(size.width)}`, async ({
    page,
    address,
  }) => {
    await page.setViewportSize(size);
    await page.goto(address);
    await page
      .getByLabel("Song file")
      .setInputFiles(`${ROOT}charts/oh-susanna.chart.json`);
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByLabel("Section", { exact: true })).toContainText(
      "Pickup",
    );
    const field = page.getByLabel("New name", { exact: true });
    const action = page.getByRole("button", { name: "Rename section" });
    const fieldBox = await field.boundingBox();
    const actionBox = await action.boundingBox();
    expect(fieldBox).not.toBeNull();
    expect(actionBox).not.toBeNull();
    expect((fieldBox?.y ?? 0) + (fieldBox?.height ?? 0)).toBe(
      (actionBox?.y ?? 0) + (actionBox?.height ?? 0),
    );
    await mkdir(SHOTS, { recursive: true });
    await page
      .getByRole("region", { name: "Chart", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `${SHOTS}/app-chart-${String(size.width)}.png`,
    });

    const chartFile = JSON.parse(
      await readFile(`${ROOT}charts/oh-susanna.chart.json`, "utf8"),
    ) as { sections: { name: string }[] };
    const first = chartFile.sections[0];
    if (first === undefined) throw new Error("Chart needs a section");
    first.name = "LongUnbrokenSection".repeat(25);
    await page.getByLabel("Song file").setInputFiles({
      name: "long-section.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(chartFile)),
    });
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByLabel("Section", { exact: true })).toContainText(
      first.name,
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(size.width);
    await page
      .getByRole("region", { name: "Chart", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `${SHOTS}/app-long-section-${String(size.width)}.png`,
    });
  });
}

test("heard evidence clears after import and when playback restarts", async ({
  page,
  address,
}) => {
  let emit: ((event: Frame) => void) | undefined;
  await page.routeWebSocket("**/ws", (socket) => {
    socket.connectToServer();
    emit = (event) => {
      socket.send(JSON.stringify(event));
    };
  });
  await page.goto(address);
  const heard = page.getByRole("list", { name: "Heard chord" });
  const evidence = {
    event: "heard",
    beat: 0,
    symbol: "Em",
    confidence: 1,
    expected: "G",
    matches: false,
  };
  await page
    .getByLabel("Song file")
    .setInputFiles(`${ROOT}charts/oh-susanna.chart.json`);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.getByText("Chart ready:", { exact: false })).toBeVisible();
  emit?.(evidence);
  await expect(heard).toContainText("chart says G");
  await page
    .getByLabel("Song file")
    .setInputFiles(`${ROOT}charts/greensleeves.chart.json`);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Song", exact: true }),
  ).toContainText("Greensleeves");
  await expect(heard.getByRole("listitem")).toHaveCount(0);
  emit?.(evidence);
  await expect(heard).toContainText("chart says G");
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop", exact: true }),
  ).toBeVisible();
  await expect(heard).not.toContainText("chart says G");
});
