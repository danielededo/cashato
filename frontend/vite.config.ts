import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The SPA is served at the gateway root (`/`) in-cluster; the APIs live under
// `/api/v1` on the SAME origin (Envoy Gateway path-split). So the app always calls
// RELATIVE `/api/v1/...`. In local dev only, proxy that to the cluster gateway LB
// (reachable from WSL) — override with VITE_API_TARGET (e.g. a port-forward).
const apiTarget = process.env.VITE_API_TARGET ?? "http://172.19.255.1";

export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    proxy: {
      "/api/v1": { target: apiTarget, changeOrigin: true },
    },
  },
});
