import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// allowedHosts: the app resolves its TENANT from the hostname it was served on (the SPA sends
// it as X-Tenant-Host — see src/api.js), so testing a second tenant locally means loading the
// dev server as e.g. http://acme.localhost:5173. Vite rejects unknown Host headers by default,
// which made that impossible. `.localhost` resolves to loopback in every modern browser with no
// hosts-file edit, and the entry is a subdomain suffix so only *.localhost is admitted.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, allowedHosts: [".localhost"] },
  preview: { port: 4173, allowedHosts: [".localhost"] },
});
