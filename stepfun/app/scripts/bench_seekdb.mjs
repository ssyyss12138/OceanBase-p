import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ensurePreload } from "./preload.mjs";

ensurePreload();
const { SeekdbClient } = await import("seekdb");

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const stepfunRoot = path.resolve(appRoot, "..");
const resultsDir = path.join(stepfunRoot, "results");
const benchDir = path.join(appRoot, ".data-bench");
const DIM = 512;
const DOC_COUNT = 200;
const QUERY_COUNT = 100;

function readJsonl(name) {
  return fs
    .readFileSync(path.join(stepfunRoot, "data", name), "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function rng(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function vector(random) {
  const values = Array.from({ length: DIM }, () => random() * 2 - 1);
  const norm = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0)) || 1;
  return values.map((value) => value / norm);
}

function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return Number(sorted[index].toFixed(2));
}

async function main() {
  fs.rmSync(benchDir, { recursive: true, force: true });
  fs.mkdirSync(benchDir, { recursive: true });
  const dbPath = path.join(benchDir, "bench.db");
  const client = new SeekdbClient({ path: dbPath, database: "test" });
  const collection = await client.getOrCreateCollection({
    name: "bench",
    configuration: {
      hnsw: { dimension: DIM, distance: "cosine" },
      fulltextConfig: { analyzer: "ngram", properties: { ngram_token_size: 2 } },
    },
    embeddingFunction: null,
  });

  const corpus = readJsonl("corpus.jsonl");
  const queries = readJsonl("queries.jsonl");
  const random = rng(20261108);

  const docs = Array.from({ length: DOC_COUNT }, (_, index) => {
    const base = corpus[index % corpus.length];
    return {
      id: `bench-${index}`,
      text: `${base.text}（样本 ${index}）`,
      metadata: { source: base.id, group: index % 8 },
      embedding: vector(random),
    };
  });

  const insertStarted = performance.now();
  for (let offset = 0; offset < docs.length; offset += 50) {
    const batch = docs.slice(offset, offset + 50);
    await collection.upsert({
      ids: batch.map((doc) => doc.id),
      documents: batch.map((doc) => doc.text),
      embeddings: batch.map((doc) => doc.embedding),
      metadatas: batch.map((doc) => doc.metadata),
    });
  }
  const insertMs = Math.round(performance.now() - insertStarted);
  const count = await collection.count();

  const latencies = [];
  const searchTexts = Array.from({ length: QUERY_COUNT }, (_, index) => queries[index % queries.length].query);
  // warmup
  for (let index = 0; index < 5; index += 1) {
    await collection.hybridSearch({
      query: { whereDocument: { $contains: searchTexts[index] }, nResults: 15 },
      knn: { queryEmbeddings: vector(random), nResults: 15 },
      rank: { rrf: {} },
      nResults: 5,
      include: ["documents", "distances"],
    });
  }
  for (const text of searchTexts) {
    const started = performance.now();
    await collection.hybridSearch({
      query: { whereDocument: { $contains: text }, nResults: 15 },
      knn: { queryEmbeddings: vector(random), nResults: 15 },
      rank: { rrf: {} },
      nResults: 5,
      include: ["documents", "distances"],
    });
    latencies.push(performance.now() - started);
  }

  const report = {
    generatedAt: new Date().toISOString(),
    mode: "embedded",
    runtime: `Node ${process.version} / ${process.platform}-${process.arch}`,
    dimension: DIM,
    docs: count,
    insert: { ms: insertMs, docsPerSecond: Number((count / (insertMs / 1000)).toFixed(1)) },
    search: {
      queries: QUERY_COUNT,
      p50Ms: percentile(latencies, 50),
      p90Ms: percentile(latencies, 90),
      meanMs: Number((latencies.reduce((sum, value) => sum + value, 0) / latencies.length).toFixed(2)),
    },
  };
  fs.mkdirSync(resultsDir, { recursive: true });
  fs.writeFileSync(path.join(resultsDir, "seekdb_bench.json"), JSON.stringify(report, null, 2));
  console.log(`docs=${count} insert=${insertMs}ms (${report.insert.docsPerSecond} docs/s)`);
  console.log(`hybrid search: p50=${report.search.p50Ms}ms p90=${report.search.p90Ms}ms mean=${report.search.meanMs}ms`);
  console.log(`saved ${path.join(resultsDir, "seekdb_bench.json")}`);
}

main().then(() => process.exit(0)).catch((error) => {
  console.error(error);
  process.exit(1);
});
