import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "console",
  base: "/console/",
  publicDir: false,
  envDir: "..",
  plugins: [react()],
  server: { port: 5175, allowedHosts: [".localhost"], fs: { allow: [".."] } },
  preview: { port: 4175, allowedHosts: [".localhost"] },
  build: {
    outDir: "../dist/console",
    emptyOutDir: true,
  },
});
