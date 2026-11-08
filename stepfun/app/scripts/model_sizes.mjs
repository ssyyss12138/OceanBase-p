import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const stepfunRoot = path.resolve(appRoot, "..");
const resultsDir = path.join(stepfunRoot, "results");
const endpoint = process.env.HF_ENDPOINT || "https://huggingface.co";

const MODELS = [
  "Xenova/bge-small-zh-v1.5",
  "Xenova/all-MiniLM-L6-v2",
  "Xenova/paraphrase-multilingual-MiniLM-L12-v2",
];
const FILES = [
  { label: "FP32", file: "onnx/model.onnx" },
  { label: "INT8", file: "onnx/model_quantized.onnx" },
  { label: "FP16", file: "onnx/model_fp16.onnx" },
];

async function headSize(url) {
  const response = await fetch(url, { method: "HEAD", redirect: "follow" });
  if (!response.ok) return null;
  const length = response.headers.get("content-length");
  return length ? Number(length) : null;
}

function mib(bytes) {
  return bytes === null ? "n/a" : `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

async function main() {
  const rows = [];
  for (const model of MODELS) {
    const record = { model, sizes: {}, compression: null };
    for (const entry of FILES) {
      const url = `${endpoint}/${model}/resolve/main/${entry.file}`;
      record.sizes[entry.label] = await headSize(url);
    }
    if (record.sizes.FP32 && record.sizes.INT8) {
      record.compression = Number((record.sizes.FP32 / record.sizes.INT8).toFixed(2));
    }
    rows.push(record);
    console.log(
      `${model}: FP32 ${mib(record.sizes.FP32)} | INT8 ${mib(record.sizes.INT8)} | FP16 ${mib(record.sizes.FP16)} | compression ${record.compression ?? "n/a"}x`,
    );
  }
  fs.mkdirSync(resultsDir, { recursive: true });
  fs.writeFileSync(path.join(resultsDir, "model_sizes.json"), JSON.stringify({ endpoint, rows }, null, 2));
  console.log(`saved ${path.join(resultsDir, "model_sizes.json")}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
