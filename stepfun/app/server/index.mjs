import express from "express";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ensurePreload } from "../scripts/preload.mjs";

// On Linux the embedded seekdb shared library must be preloaded before Node starts
// (otherwise Node may fail with "cannot allocate memory in static TLS block").
ensurePreload();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, "..");
const stepfunRoot = path.resolve(appRoot, "..");
const dataRoot = path.join(stepfunRoot, "data");
const dbRoot = process.env.KB_DATA_DIR ? path.resolve(process.env.KB_DATA_DIR) : path.join(appRoot, ".data");
fs.mkdirSync(dbRoot, { recursive: true });

const { SeekdbClient } = await import("seekdb");

const isProd = process.argv.includes("--prod");
const port = Number(process.env.PORT || 5173);
const host = process.env.SEEKDB_HOST;
const clientArgs = host
  ? {
      host,
      port: Number(process.env.SEEKDB_PORT || 2881),
      user: process.env.SEEKDB_USER || "root",
      password: process.env.SEEKDB_PASSWORD || "",
      database: process.env.SEEKDB_DATABASE || "test",
    }
  : { path: path.join(dbRoot, "kb.db"), database: "test" };

const client = new SeekdbClient(clientArgs);
const collections = new Map();

function collectionName(dimension) {
  return `kb_${dimension}`;
}

async function getCollection(dimension) {
  const name = collectionName(dimension);
  if (collections.has(dimension)) return collections.get(dimension);
  const collection = await client.getOrCreateCollection({
    name,
    configuration: {
      hnsw: { dimension, distance: "cosine" },
      fulltextConfig: { analyzer: "ngram", properties: { ngram_token_size: 2 } },
    },
    embeddingFunction: null,
  });
  collections.set(dimension, collection);
  return collection;
}

function readJsonl(file) {
  return fs
    .readFileSync(file, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

const app = express();
// Cross-origin isolation enables SharedArrayBuffer, which ONNX Runtime Web needs
// for its multithreaded WASM backend.
app.use((_request, response, next) => {
  response.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  response.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  next();
});
app.use(express.json({ limit: "100mb" }));

app.get("/api/health", async (_request, response) => {
  response.json({
    ok: true,
    seekdb: host ? "server" : "embedded",
    dbPath: host ? `${host}:${clientArgs.port}` : clientArgs.path,
  });
});

app.post("/api/init", async (request, response, next) => {
  try {
    const dimension = Number(request.body?.dimension);
    if (!Number.isInteger(dimension) || dimension <= 0) throw new Error("invalid dimension");
    const collection = await getCollection(dimension);
    const count = await collection.count();
    response.json({ count });
  } catch (error) {
    next(error);
  }
});

app.post("/api/reset", async (request, response, next) => {
  try {
    const dimension = Number(request.body?.dimension);
    const name = collectionName(dimension);
    if (await client.hasCollection(name)) {
      await client.deleteCollection(name);
    }
    collections.delete(dimension);
    await getCollection(dimension);
    response.json({ count: 0 });
  } catch (error) {
    next(error);
  }
});

app.post("/api/add", async (request, response, next) => {
  try {
    const dimension = Number(request.body?.dimension);
    const docs = request.body?.docs;
    if (!Array.isArray(docs) || !docs.length) throw new Error("docs is empty");
    const collection = await getCollection(dimension);
    const started = performance.now();
    const batchSize = 100;
    for (let offset = 0; offset < docs.length; offset += batchSize) {
      const batch = docs.slice(offset, offset + batchSize);
      await collection.upsert({
        ids: batch.map((doc) => doc.id),
        documents: batch.map((doc) => doc.text),
        embeddings: batch.map((doc) => doc.embedding),
        metadatas: batch.map((doc) => doc.metadata ?? {}),
      });
    }
    const count = await collection.count();
    response.json({ count, addMs: Math.round(performance.now() - started) });
  } catch (error) {
    next(error);
  }
});

app.post("/api/search", async (request, response, next) => {
  try {
    const dimension = Number(request.body?.dimension);
    const query = String(request.body?.query ?? "").trim();
    const embedding = request.body?.embedding;
    const topK = Math.min(20, Math.max(1, Number(request.body?.topK) || 5));
    if (!query) throw new Error("query is empty");
    if (!Array.isArray(embedding) || embedding.length !== dimension) throw new Error("embedding dimension mismatch");
    const collection = await getCollection(dimension);
    const started = performance.now();
    const candidateCount = Math.max(topK * 3, 10);
    const result = await collection.hybridSearch({
      query: { whereDocument: { $contains: query }, nResults: candidateCount },
      knn: { queryEmbeddings: embedding, nResults: candidateCount },
      rank: { rrf: {} },
      nResults: topK,
      include: ["documents", "metadatas", "distances"],
    });
    const searchMs = Math.round(performance.now() - started);
    const ids = result.ids?.[0] ?? [];
    const documents = result.documents?.[0] ?? [];
    const metadatas = result.metadatas?.[0] ?? [];
    const distances = result.distances?.[0] ?? [];
    const hits = ids.map((id, index) => ({
      id,
      text: documents[index] ?? "",
      metadata: metadatas[index] ?? null,
      distance: typeof distances[index] === "number" ? distances[index] : null,
    }));
    response.json({ hits, searchMs });
  } catch (error) {
    next(error);
  }
});

app.get("/api/stats", async (request, response, next) => {
  try {
    const dimension = Number(request.query?.dimension);
    const collection = await getCollection(dimension);
    response.json({ count: await collection.count() });
  } catch (error) {
    next(error);
  }
});

app.get("/api/sample", (_request, response, next) => {
  try {
    response.json({
      corpus: readJsonl(path.join(dataRoot, "corpus.jsonl")),
      queries: readJsonl(path.join(dataRoot, "queries.jsonl")),
    });
  } catch (error) {
    next(error);
  }
});

app.use((error, _request, response, _next) => {
  console.error("[api]", error);
  response.status(500).json({ error: error instanceof Error ? error.message : String(error) });
});

if (isProd) {
  const dist = path.join(appRoot, "dist");
  app.use(express.static(dist));
  app.get("*", (_request, response) => response.sendFile(path.join(dist, "index.html")));
} else {
  const { createServer } = await import("vite");
  const vite = await createServer({
    root: appRoot,
    server: { middlewareMode: true },
    appType: "spa",
  });
  app.use(vite.middlewares);
}

app.listen(port, () => {
  console.log(`[seekdb-kb] http://localhost:${port}  (${host ? "server" : "embedded"} seekdb, db=${host ? clientArgs.host + ":" + clientArgs.port : clientArgs.path})`);
});
