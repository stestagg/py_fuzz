import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig(() => {
  const backend = process.env.PFUI_BACKEND_URL ?? "http://127.0.0.1:8767";
  return {
    plugins: [react()],
    build: {
      outDir: "../static",
      emptyOutDir: true,
    },
    server: {
      proxy: {
        "/health": backend,
        "/ws": { target: backend, ws: true },
      },
    },
    test: {
      environment: "jsdom",
      environmentOptions: { jsdom: { url: "http://localhost/" } },
      globals: true,
      setupFiles: "./src/test/setup.ts",
    },
  };
});
