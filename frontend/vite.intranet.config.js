import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: "intranet",
  publicDir: false,
  base: "/intranet/",
  build: {
    outDir: "../dist/intranet",
    emptyOutDir: true,
  },
  server: { port: 5174, allowedHosts: [".localhost"], fs: { allow: [".."] } },
  preview: { port: 4174, allowedHosts: [".localhost"] },
});
