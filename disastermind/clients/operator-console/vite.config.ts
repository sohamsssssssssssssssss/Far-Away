import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-server proxy: forward the DisasterMind API + WebSocket to the FastAPI
// backend (uvicorn default :8000). The browser talks to the Vite dev server on
// relative URLs (so the built app also works behind any host, like the
// reference vanilla-JS dashboard), and Vite proxies through to uvicorn.
//
// Override the proxy target with VITE_PROXY_TARGET when the backend lives
// elsewhere. The app itself honours VITE_API_BASE / VITE_WS_BASE at runtime
// (see src/api/client.ts), which take precedence over relative URLs entirely.
const PROXY_TARGET = process.env.VITE_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/health": { target: PROXY_TARGET, changeOrigin: true },
      "/topics": { target: PROXY_TARGET, changeOrigin: true },
      "/incidents": { target: PROXY_TARGET, changeOrigin: true },
      "/escalations": { target: PROXY_TARGET, changeOrigin: true },
      "/ws": { target: PROXY_TARGET, changeOrigin: true, ws: true },
    },
  },
});
