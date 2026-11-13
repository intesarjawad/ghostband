import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  input: {
    index: fileURLToPath(new URL("index.html", import.meta.url)),
    demo: fileURLToPath(new URL("demo.html", import.meta.url)),
  },
  build: {
    outDir: "../conductor/ghostband/ui_dist",
    emptyOutDir: true,
  },
  test: {
    include: ["tests/**/*.test.ts"],
  },
});
