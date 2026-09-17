import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Acumyn's public site: the marketing pages AND the app.<domain> workspace finder, one bundle
// that Caddy serves on both hosts (see src/marketing/Site.jsx).
//
// base "/" — unlike intranet and console this entry is served at the ROOT of its own HOST, not
// under a path prefix, and Site.jsx's router reads window.location.pathname directly. A base of
// "/marketing/" would break every internal link.
//
// publicDir FALSE, like the intranet and console. frontend/public is the DASHBOARD's: it holds
// Spring's privacy policy, EULA and brand photographs, and copying it here would publish another
// company's documents on Acumyn's own domain. What this site needs — its plates, its one photo,
// the favicons — is imported or referenced from index.html, so Vite bundles exactly that.
export default defineConfig({
  root: "marketing",
  base: "/",
  publicDir: false,
  envDir: "..",
  plugins: [react()],
  build: { outDir: "../dist/marketing", emptyOutDir: true },
  server: { port: 5176, allowedHosts: [".localhost"], fs: { allow: [".."] } },
  preview: { port: 4176, allowedHosts: [".localhost"] },
});
