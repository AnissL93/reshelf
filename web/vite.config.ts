import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // The FastAPI app mounts this directory as the SPA (src/reshelf/web/app.py).
  build: { outDir: "../src/reshelf/web/static", emptyOutDir: true },
  // `npm run dev` talks to a `reshelf serve` instance on 8080.
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8080",
    },
  },
});
