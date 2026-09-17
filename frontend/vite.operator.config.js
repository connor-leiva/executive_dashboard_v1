import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The operator console: Acumyn staff administering workspaces, at admin.acumyn.io. Its own entry
// and its own bundle, so a customer's browser never downloads the screen that suspends customers,
// and the operator's session lives on an origin no workspace shares.
//
// base "/" because it owns the root of its host; publicDir false because frontend/public is the
// dashboard's. The favicons it needs are referenced from index.html, so Vite bundles only those.
// Not to be confused with vite.console.config.js, which is each WORKSPACE's own team-portal admin.
export default defineConfig({
  root: "operator",
  base: "/",
  publicDir: false,
  envDir: "..",
  plugins: [react()],
  build: { outDir: "../dist/operator", emptyOutDir: true },
  server: { port: 5177, allowedHosts: [".localhost"], fs: { allow: [".."] } },
  preview: { port: 4177, allowedHosts: [".localhost"] },
});
