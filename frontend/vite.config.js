import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api/* is proxied to FastAPI. In prod FastAPI serves dist/ and /api itself.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
