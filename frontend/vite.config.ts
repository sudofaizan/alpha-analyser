import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const apiProxy = {
  target: "http://127.0.0.1:8090",
  changeOrigin: true,
};

export default defineConfig({
  root: __dirname,
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/getChartBundle": apiProxy,
      "/health": apiProxy,
      "/api": apiProxy,
    },
  },
  preview: { port: 5174 },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        login: path.resolve(__dirname, "login.html"),
        signup: path.resolve(__dirname, "signup.html"),
        admin: path.resolve(__dirname, "admin.html"),
      },
    },
  },
  resolve: {
    dedupe: ["lightweight-charts"],
  },
  optimizeDeps: {
    include: ["lightweight-charts"],
  },
});
