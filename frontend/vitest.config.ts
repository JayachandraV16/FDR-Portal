import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Kept separate from vite.config.ts: tests need neither the Tailwind plugin nor the /api proxy,
// and no test may ever reach a real backend.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    // Lets a test read src/index.css as text (the theme-token checks); styles are not otherwise processed.
    css: { include: [/index\.css\?raw/] },
  },
});
