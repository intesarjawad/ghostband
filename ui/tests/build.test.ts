import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "vite";
import { expect, test } from "vitest";

test("build packages all browser entry points", async () => {
  const output = await mkdtemp(join(tmpdir(), "ghostband-ui-"));
  try {
    await build({ build: { outDir: output }, logLevel: "error" });
    for (const page of ["index.html", "demo.html"]) {
      const html = await readFile(join(output, page), "utf8");
      expect(html).toContain('<html lang="en">');
      expect(html).not.toContain('src="/src/');
    }
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});
