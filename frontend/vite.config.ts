import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      // ws: true - the native Remote Management agent's control channel and
      // the operator's session channel are both WebSocket routes under
      // /api/remote/* (see remote-agent/PROTOCOL.md); the plain string form
      // doesn't reliably proxy WS upgrades.
      "/api": { target: "http://api:8000", ws: true, changeOrigin: true },
    },
  },
});
