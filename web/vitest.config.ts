import { defineConfig } from "vitest/config";

// Kept out of vite.config.ts so the SPA build config stays a build config.
// No DOM environment: the only tested module is pure arithmetic, and
// pulling in jsdom to test it would be testing jsdom.
export default defineConfig({
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
