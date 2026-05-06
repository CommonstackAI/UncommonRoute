import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/dashboard/",
  server: {
    proxy: {
      "/v1": "http://localhost:8403",
      "/health": "http://localhost:8403",
    },
  },
  build: {
    outDir: "../../uncommon_route/static",
    emptyOutDir: true,
  },
});
