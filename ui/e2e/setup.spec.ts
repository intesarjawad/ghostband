import { homedir } from "node:os";
import { expect } from "@playwright/test";
import { test, type Frame } from "./live-server";

for (const size of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`setup progress, failure and retry at ${String(size.width)}`, async ({
    page,
    address,
  }) => {
    await page.setViewportSize(size);
    const progress = {
      event: "setup_status",
      state: "installing",
      stage: "models",
      done_bytes: 1048576,
      total_bytes: 4194304,
      detail: "",
      retryable: false,
    };
    let sendSetup: ((event: Frame) => void) | undefined;
    await page.routeWebSocket("**/ws", (socket) => {
      const upstream = socket.connectToServer();
      sendSetup = (event) => {
        socket.send(JSON.stringify(event));
      };
      upstream.onMessage((data) => {
        const frame = JSON.parse(String(data)) as Frame;
        socket.send(
          JSON.stringify(frame.event === "setup_status" ? progress : frame),
        );
      });
    });
    await page.route("**/api/setup", (route) => {
      expect(route.request().headers()["x-ghostband-token"]).toBe(
        new URL(address).searchParams.get("t"),
      );
      return route.fulfill({ json: progress });
    });
    await page.goto(address);
    const setup = page.getByRole("region", { name: "Setup" });
    await expect(setup).toContainText("1048576 / 4194304 bytes");
    await expect(setup.getByRole("progressbar")).toHaveAttribute(
      "value",
      "1048576",
    );
    await page.screenshot({
      path: `${homedir()}/.cache/ghostband/e2e-shots/setup-progress-${String(size.width)}.png`,
    });
    if (sendSetup === undefined)
      throw new Error("Setup socket did not connect");
    sendSetup({
      ...progress,
      state: "failed",
      detail:
        "Download interrupted: connection closed. Resume the model download.",
      retryable: true,
    });
    await expect(setup).toContainText(
      "Download interrupted: connection closed.",
    );
    const retry = setup.getByRole("button", { name: "Retry setup" });
    await expect(retry).toBeEnabled();
    await page.screenshot({
      path: `${homedir()}/.cache/ghostband/e2e-shots/setup-failure-${String(size.width)}.png`,
    });
    await page.route("**/api/setup/retry", (route) => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers()["x-ghostband-token"]).toBe(
        new URL(address).searchParams.get("t"),
      );
      return route.fulfill({
        status: 503,
        json: {
          event: "error",
          code: "unavailable",
          message: "Retry unavailable",
          fix: "Try again.",
        },
      });
    });
    await retry.click();
    await expect(setup).toContainText("Retry unavailable");
    await expect(retry).toBeEnabled();
    await page.unroute("**/api/setup/retry");
    await page.route("**/api/setup/retry", (route) =>
      route.fulfill({ status: 202, json: progress }),
    );
    await retry.click();
    await expect(retry).toBeHidden();
    sendSetup({ ...progress, state: "ready" });
    await expect(setup).toBeHidden();
  });
}
