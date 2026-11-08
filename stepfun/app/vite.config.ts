import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  worker: { format: "es" },
  optimizeDeps: {
    exclude: ["@huggingface/transformers", "onnxruntime-web"],
  },
  server: {
    port: 5173,
    strictPort: false,
    headers: {
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Embedder-Policy": "require-corp",
    },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 4096,
  },
});
