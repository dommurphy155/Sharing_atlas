import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the same bundle works at the domain root (direct :8080)
// and under a reverse-proxy subpath (/atlas/). Absolute "/assets/..." would
// 404 in one of those two placements.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8090",
    },
  },
});