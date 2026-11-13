import { spawn, type ChildProcess } from "node:child_process";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";
import { homedir } from "node:os";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

let server: ChildProcess;
test.beforeAll(async () => {
  server = spawn(
    "npm",
    [
      "run",
      "dev",
      "--",
      "--host",
      "127.0.0.1",
      "--port",
      "5178",
      "--strictPort",
    ],
    { cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "pipe" },
  );
  await new Promise<void>((resolve, reject) => {
    server.stdout?.on("data", (chunk: Buffer) => {
      // The Playwright runner forces colour, so Vite wraps "Local" in escapes.
      if (
        chunk
          .toString()
          .replace(/\x1b\[[0-9;]*m/g, "")
          .includes("Local:")
      )
        resolve();
    });
    server.on("exit", () => {
      reject(new Error("Vite exited before starting"));
    });
  });
});
test.afterAll(() => {
  server.kill();
});

test("failed take loading offers a working retry", async ({ page }) => {
  await page.route("**/takes/*.json", (route) =>
    route.fulfill({ status: 503 }),
  );
  await page.goto("http://127.0.0.1:5178/demo.html");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.locator("#machine")).toBeHidden();
  await page.unrouteAll();
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.locator("#machine")).toBeVisible();
});

test("playback rejection offers another attempt", async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () =>
      Promise.reject(new Error("blocked"));
  });
  await page.goto("http://127.0.0.1:5178/demo.html");
  await expect(page.locator("body")).not.toHaveClass(/loading/);
  await page.keyboard.press("Space");
  await expect(
    page.getByRole("button", { name: "Retry playback" }),
  ).toBeVisible();
});

for (const size of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`run instructions fit and remain keyboard reachable at ${String(size.width)}`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    await page.goto("http://127.0.0.1:5178/demo.html");
    await expect(page.locator("body")).not.toHaveClass(/loading/);
    const shots = `${homedir()}/.cache/ghostband/e2e-shots`;
    await mkdir(shots, { recursive: true });
    await page.screenshot({
      path: `${shots}/demo-hero-${String(size.width)}.png`,
    });
    const command = page.locator(".command pre");
    await command.scrollIntoViewIfNeeded();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(size.width);
    await command.focus();
    await expect(command).toBeFocused();
    expect(
      (await new AxeBuilder({ page }).include(".command").analyze()).violations,
    ).toEqual([]);
    await page.screenshot({
      path: `${shots}/demo-run-${String(size.width)}.png`,
    });
  });
}
