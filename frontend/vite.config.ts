import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: __dirname,
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/getChartBundle": { target: "http://127.0.0.1:8090", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8090", changeOrigin: true },
    },
  },
  preview: { port: 5174 },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  resolve: {
    dedupe: ["lightweight-charts"],
  },
  optimizeDeps: {
    include: ["lightweight-charts"],
  },
});
