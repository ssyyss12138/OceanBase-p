import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(appRoot, "node_modules", "onnxruntime-web", "dist");
const target = path.join(appRoot, "public", "ort");

if (!fs.existsSync(source)) {
  console.log("[copy-ort] node_modules/onnxruntime-web not found, skip");
  process.exit(0);
}

fs.mkdirSync(target, { recursive: true });
let copied = 0;
for (const file of fs.readdirSync(source)) {
  if (/\.(wasm|mjs)$/.test(file)) {
    fs.copyFileSync(path.join(source, file), path.join(target, file));
    copied += 1;
  }
}
console.log(`[copy-ort] copied ${copied} ONNX Runtime asset(s) to public/ort`);
