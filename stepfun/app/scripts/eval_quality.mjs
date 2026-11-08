import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { env, pipeline } from "@huggingface/transformers";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const stepfunRoot = path.resolve(appRoot, "..");
const resultsDir = path.join(stepfunRoot, "results");
const cacheDir = path.join(appRoot, ".cache");
env.cacheDir = cacheDir;
env.allowLocalModels = false;
env.allowRemoteModels = true;

const MODEL = process.env.EVAL_MODEL || "Xenova/bge-small-zh-v1.5";
const TOP_K = 3;

function readJsonl(name) {
  return fs
    .readFileSync(path.join(stepfunRoot, "data", name), "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function embedAll(extractor, texts, batchSize = 8) {
  const vectors = [];
  for (let offset = 0; offset < texts.length; offset += batchSize) {
    const batch = texts.slice(offset, offset + batchSize);
    const output = await extractor(batch, { pooling: "mean", normalize: true });
    const rows = output.tolist();
    for (const row of rows) vectors.push(row);
  }
  return vectors;
}

function dot(a, b) {
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) sum += a[i] * b[i];
  return sum;
}

function evaluate(docVectors, docs, queryVectors, queries) {
  let recallSum = 0;
  let reciprocalRankSum = 0;
  for (let q = 0; q < queries.length; q += 1) {
    const scored = docs.map((doc, index) => ({ id: doc.id, score: dot(queryVectors[q], docVectors[index]) }));
    scored.sort((left, right) => right.score - left.score);
    const top = scored.slice(0, TOP_K).map((item) => item.id);
    const relevant = new Set(queries[q].relevant);
    const hits = top.filter((id) => relevant.has(id)).length;
    recallSum += hits / relevant.size;
    const rank = scored.findIndex((item) => relevant.has(item.id));
    reciprocalRankSum += rank >= 0 ? 1 / (rank + 1) : 0;
  }
  return {
    recallAtK: Number((recallSum / queries.length).toFixed(4)),
    mrrAtK: Number((reciprocalRankSum / queries.length).toFixed(4)),
    k: TOP_K,
    queries: queries.length,
    docs: docs.length,
  };
}

async function main() {
  const docs = readJsonl("corpus.jsonl");
  const queries = readJsonl("queries.jsonl");
  const results = { model: MODEL, generatedAt: new Date().toISOString(), precisions: {} };
  for (const precision of ["fp32", "q8"]) {
    const started = Date.now();
    const extractor = await pipeline("feature-extraction", MODEL, { dtype: precision });
    const docVectors = await embedAll(extractor, docs.map((doc) => doc.text));
    const queryVectors = await embedAll(extractor, queries.map((item) => item.query));
    const metrics = evaluate(docVectors, docs, queryVectors, queries);
    metrics.elapsedMs = Date.now() - started;
    metrics.dim = docVectors[0]?.length ?? 0;
    results.precisions[precision] = metrics;
    console.log(`${precision}: Recall@${TOP_K}=${metrics.recallAtK} MRR@${TOP_K}=${metrics.mrrAtK} (dim=${metrics.dim}, ${metrics.elapsedMs} ms)`);
    if (typeof extractor.dispose === "function") {
      await extractor.dispose();
    }
  }
  const baseline = results.precisions.fp32;
  const quantized = results.precisions.q8;
  if (baseline && quantized) {
    results.qualityDelta = {
      recallAtK: Number((quantized.recallAtK - baseline.recallAtK).toFixed(4)),
      mrrAtK: Number((quantized.mrrAtK - baseline.mrrAtK).toFixed(4)),
    };
  }
  fs.mkdirSync(resultsDir, { recursive: true });
  fs.writeFileSync(path.join(resultsDir, "quality.json"), JSON.stringify(results, null, 2));
  console.log(`saved ${path.join(resultsDir, "quality.json")}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
