import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test as base, type WebSocket } from "@playwright/test";
import { build } from "vite";

const UI = fileURLToPath(new URL("..", import.meta.url));
export const ROOT = fileURLToPath(new URL("../..", import.meta.url));
export const SONG = `${ROOT}fixtures/greensleeves.mid`;
export const PROMPT_AUDIO = `${ROOT}fixtures/greensleeves.wav`;
const ADDRESS = /http:\/\/127\.0\.0\.1:\d+\/\?t=\S+/;

export type Frame = Record<string, unknown>;

export const test = base.extend<{ address: string }>({
  address: async ({}, use) => {
    await build({ root: UI, logLevel: "error" });
    const dataDir = mkdtempSync(join(tmpdir(), "ghostband-e2e-"));
    const child = spawn(
      "uv",
      ["run", "--frozen", "python", "-u", "ui/e2e/server.py"],
      {
        cwd: ROOT,
        env: {
          ...process.env,
          GHOSTBAND_DEV: "1",
          GHOSTBAND_DATA_DIR: dataDir,
        },
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    try {
      const address = await new Promise<string>((resolve, reject) => {
        let output = "";
        const read = (chunk: Buffer): void => {
          output += chunk.toString();
          const found = ADDRESS.exec(output);
          if (found) resolve(found[0]);
        };
        child.stdout.on("data", read);
        child.stderr.on("data", read);
        child.on("error", reject);
        child.on("exit", (code) => {
          reject(new Error(`ghostband exited with ${String(code)}: ${output}`));
        });
      });
      await use(address);
    } finally {
      if (child.pid !== undefined && child.exitCode === null) {
        process.kill(-child.pid, "SIGTERM");
        await new Promise<void>((resolve) =>
          child.once("exit", () => {
            resolve();
          }),
        );
      }
      rmSync(dataDir, { recursive: true, force: true });
    }
  },
});

export class Frames {
  readonly sent: Frame[] = [];
  readonly received: Frame[] = [];

  watch(socket: WebSocket): void {
    socket.on("framesent", ({ payload }) => {
      this.sent.push(JSON.parse(String(payload)) as Frame);
    });
    socket.on("framereceived", ({ payload }) => {
      const frame = JSON.parse(String(payload)) as Frame;
      if (frame.event !== "clock") {
        this.received.push(frame);
      }
    });
  }

  /** Do one thing in the page; check the exact message it sent and the reply that came back. */
  async roundTrip(
    act: () => Promise<unknown>,
    message: Frame,
    reply: (frame: Frame) => boolean,
  ): Promise<Frame> {
    const sentBefore = this.sent.length;
    const receivedBefore = this.received.length;
    await act();
    await expect
      .poll(() => this.sent.slice(sentBefore))
      .toContainEqual(message);
    await expect
      .poll(
        () =>
          this.received
            .slice(receivedBefore)
            .map((frame) => ({ frame, matches: reply(frame) })),
        { message: `Reply to ${JSON.stringify(message)}` },
      )
      .toEqual(
        expect.arrayContaining([expect.objectContaining({ matches: true })]),
      );
    const matched = this.received.slice(receivedBefore).find(reply);
    if (matched === undefined) throw new Error("Matched reply disappeared");
    return matched;
  }
}

export const state =
  (check: (frame: Frame) => boolean) =>
  (frame: Frame): boolean =>
    frame.event === "state" && check(frame);

export const chart = (frame: Frame): Frame => frame.chart as Frame;
