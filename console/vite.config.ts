import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// The same policy the Python server sends as a header. Added only to the production build: Vite's dev server
// needs an inline script for hot reload, which this policy (rightly) forbids.
const CSP = "default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; " +
  "font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'";

const csp = (): Plugin => ({
  name: "onboardiq-csp",
  apply: "build",
  transformIndexHtml: (html) => html.replace("<!--csp-->", `<meta http-equiv="Content-Security-Policy" content="${CSP}">`),
});

export default defineConfig({
  base: "./",                                   // works at /console/ on the server and under the Pages project path
  plugins: [react(), csp()],
  build: { outDir: "../web/console", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/sessions": "http://127.0.0.1:8765" } },
});
