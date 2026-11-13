import { homedir } from "node:os";
import { expect } from "@playwright/test";
import { test, Frames, ROOT, state, chart, type Frame } from "./live-server";

for (const size of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`compound pickup chart and authenticated downloads at ${String(size.width)}`, async ({
    page,
    address,
  }) => {
    await page.setViewportSize(size);
    const frames = new Frames();
    page.on("websocket", (socket) => {
      frames.watch(socket);
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const chartFile = JSON.parse(
      await (
        await import("node:fs/promises")
      ).readFile(`${ROOT}charts/oh-susanna.chart.json`, "utf8"),
    ) as Frame;
    chartFile.title = "Compound pickup study";
    chartFile.time_signature = [6, 8];
    await page.goto(address);
    await frames.roundTrip(
      async () => {
        await page.getByLabel("Song file").setInputFiles({
          name: "compound.json",
          mimeType: "application/json",
          buffer: Buffer.from(JSON.stringify(chartFile)),
        });
        await page.getByRole("button", { name: "Import", exact: true }).click();
      },
      {
        type: "import",
        upload_id: expect.any(String),
        extractor: "permissive",
      },
      state(
        (frame) =>
          frame.chart !== null &&
          chart(frame).title === "Compound pickup study",
      ),
    );
    const imported = frames.received.find(
      state(
        (frame) =>
          frame.chart !== null &&
          chart(frame).title === "Compound pickup study",
      ),
    );
    expect(imported?.chart_geometry).toEqual({
      beats_per_bar: 2,
      pulses_per_beat: 3,
      beat_phase: 0.5,
      bar_origin: 0.5,
    });
    await expect(page.locator(".lane-mirror")).toHaveText("Pickup, G");
    await page
      .getByRole("region", { name: "Lane", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `${homedir()}/.cache/ghostband/e2e-shots/chart-6-8-${String(size.width)}.png`,
    });

    const downloads = page.getByRole("region", { name: "Downloads" });
    const token = new URL(address).searchParams.get("t");
    const paths: string[] = [];
    page.on("request", (request) => {
      if (/\/api\/(export|recordings)/.test(request.url())) {
        expect(request.headers()["x-ghostband-token"]).toBe(token);
        expect(new URL(request.url()).search).toBe("");
        paths.push(new URL(request.url()).pathname);
      }
    });
    await page.route("**/api/export/chart", (route) =>
      route.fulfill({
        status: 503,
        json: {
          event: "error",
          code: "unavailable",
          message: "Chart download unavailable",
          fix: "Try again.",
        },
      }),
    );
    await downloads
      .getByRole("button", { name: "Download current chart" })
      .click();
    await expect(downloads.getByRole("alert")).toContainText(
      "Chart download unavailable",
    );
    await expect(
      downloads.getByRole("button", { name: "Download current chart" }),
    ).toBeEnabled();
    await page.unroute("**/api/export/chart");
    const chartDownload = page.waitForEvent("download");
    await downloads
      .getByRole("button", { name: "Download current chart" })
      .click();
    const exported = await chartDownload;
    expect(exported.suggestedFilename()).toBe("chart.json");
    const exportedPath = await exported.path();
    const exportedChart = JSON.parse(
      await (await import("node:fs/promises")).readFile(exportedPath, "utf8"),
    ) as Frame;
    expect(exportedChart).toEqual({
      ...chartFile,
      source: "compound.json",
      rights: "unknown",
    });

    const record = page.getByRole("button", { name: "Record", exact: true });
    await frames.roundTrip(
      () => record.click(),
      { type: "record", action: "start" },
      state((frame) => frame.recording === true),
    );
    await expect(
      downloads.getByRole("button", { name: "Download latest recording" }),
    ).toBeDisabled();
    const warning = page.waitForEvent("dialog");
    await page.close({ runBeforeUnload: true });
    const dialog = await warning;
    expect(dialog.type()).toBe("beforeunload");
    await dialog.dismiss();
    expect(page.isClosed()).toBe(false);
    await frames.roundTrip(
      () => record.click(),
      { type: "record", action: "stop" },
      state((frame) => frame.recording === false),
    );
    await expect(
      downloads.getByRole("button", { name: /^Download recording/ }).first(),
    ).toBeVisible();
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
    await expect(
      downloads.getByRole("button", { name: /^Download recording/ }),
    ).toHaveCount(2);
    await downloads.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `${homedir()}/.cache/ghostband/e2e-shots/downloads-${String(size.width)}.png`,
    });
    const recordingButtons = downloads.getByRole("button", {
      name: /^Download recording/,
    });
    const count = await recordingButtons.count();
    for (let index = 0; index < count; index += 1) {
      const downloaded = page.waitForEvent("download");
      await recordingButtons.nth(index).click();
      const take = await downloaded;
      expect(take.suggestedFilename()).toMatch(/^[0-9a-f]{32}\.wav$/);
      const path = await take.path();
      const data = await (await import("node:fs/promises")).readFile(path);
      expect(data.subarray(0, 4).toString()).toBe("RIFF");
      expect(data.subarray(8, 12).toString()).toBe("WAVE");
    }
    await page.route("**/api/recordings", (route) =>
      route.fulfill({ status: 503 }),
    );
    await downloads.getByRole("button", { name: "Refresh recordings" }).click();
    await expect(downloads.getByRole("alert")).toContainText(
      "Use Refresh recordings to retry.",
    );
    await expect(recordingButtons).toHaveCount(count);
    await page.unroute("**/api/recordings");
    await downloads.getByRole("button", { name: "Refresh recordings" }).click();
    await expect(downloads.getByRole("alert")).toBeEmpty();
    const latestDownload = page.waitForEvent("download");
    await downloads
      .getByRole("button", { name: "Download latest recording" })
      .click();
    expect((await latestDownload).suggestedFilename()).toBe("take.wav");
    await page.reload();
    await expect(
      page
        .getByRole("region", { name: "Downloads" })
        .getByRole("button", { name: /^Download recording/ }),
    ).toHaveCount(count);
    expect(paths).toContain("/api/export/chart");
    expect(paths).toContain("/api/export/wav");
    expect(paths).toContain("/api/recordings");
    expect(errors).toEqual([]);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(size.width);
  });
}
