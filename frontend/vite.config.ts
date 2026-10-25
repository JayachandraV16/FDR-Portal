import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      // Browser calls /api/... and Vite forwards it to the FastAPI server.
      proxy: {
        "/api": env.VITE_API_PROXY_TARGET || "http://localhost:8000",
      },
    },
  };
});
