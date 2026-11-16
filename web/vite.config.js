import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves on :5173 and forwards API calls to the Python server on :8000.
const api = ["/events", "/meetings", "/new", "/weekly", "/brief", "/meeting_types", "/voice", "/voices", "/people", "/recompile", "/settings", "/questions", "/answer", "/ask", "/feedback", "/summarize", "/pause", "/segment", "/end"];

export default defineConfig({
  plugins: [react()],
  server: { proxy: Object.fromEntries(api.map((p) => [p, "http://localhost:8000"])) },
});
