import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// A local page that renders the real scorecard with fixture data, so layout can be
// checked at a fixed width without a login. Not part of the production build.
export default defineConfig({
  plugins: [react()],
  root: "harness",
  server: { port: 5174, strictPort: true, fs: { allow: [".."] } },
});
