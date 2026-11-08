import { env, pipeline } from "@huggingface/transformers";

// Serve ONNX Runtime wasm assets from /public/ort (copied at install time) so the
// app does not depend on a CDN at runtime.
(env.backends as any).onnx.wasm.wasmPaths = "/ort/";

const hfEndpoint = (import.meta as any).env?.VITE_HF_ENDPOINT;
const localModelMode = (import.meta as any).env?.VITE_LOCAL_MODEL === "1";

if (localModelMode) {
  // Development/testing mode: load the model from /public/models instead of the network.
  env.allowLocalModels = true;
  env.allowRemoteModels = false;
  (env as any).localModelPath = "/models/";
} else {
  env.allowLocalModels = false;
  env.allowRemoteModels = true;
  if (hfEndpoint) {
    (env as any).remoteHost = hfEndpoint;
  }
}
env.useBrowserCache = true;

type Backend = "webgpu" | "wasm";

let extractor: any = null;
let backend: Backend = "wasm";
let dim = 0;

function progressCallback(event: any) {
  if (event?.status === "progress" && typeof event.progress === "number") {
    (self as any).postMessage({
      type: "load-progress",
      file: event.file,
      progress: event.progress,
      loaded: event.loaded,
      total: event.total,
    });
  } else if (event?.status === "done") {
    (self as any).postMessage({ type: "load-progress", file: event.file, progress: 100 });
  }
}

async function createPipeline(model: string, precision: string, device: Backend) {
  return await pipeline("feature-extraction", model, {
    device,
    dtype: precision as any,
    progress_callback: progressCallback,
  });
}

async function init(model: string, precision: string, preferWebgpu: boolean) {
  let hasWebGpu = false;
  if (preferWebgpu && typeof navigator !== "undefined" && (navigator as any).gpu) {
    try {
      hasWebGpu = !!(await (navigator as any).gpu.requestAdapter());
    } catch {
      hasWebGpu = false;
    }
    if (!hasWebGpu) {
      (self as any).postMessage({
        type: "fallback",
        reason: "navigator.gpu.requestAdapter() returned no adapter",
      });
    }
  }
  const started = performance.now();
  backend = hasWebGpu ? "webgpu" : "wasm";
  try {
    extractor = await createPipeline(model, precision, backend);
  } catch (error) {
    if (backend === "webgpu") {
      (self as any).postMessage({
        type: "fallback",
        reason: error instanceof Error ? error.message : String(error),
      });
      backend = "wasm";
      extractor = await createPipeline(model, precision, backend);
    } else {
      throw error;
    }
  }
  const warmup = await extractor(["warmup"], { pooling: "mean", normalize: true });
  dim = Number(warmup.dims?.[1] ?? 0);
  if (!dim) {
    throw new Error("could not determine embedding dimension");
  }
  return { backend, dim, loadMs: Math.round(performance.now() - started) };
}

async function embed(texts: string[], batchSize: number) {
  if (!extractor) {
    throw new Error("model is not initialized");
  }
  const vectors: number[][] = [];
  for (let offset = 0; offset < texts.length; offset += batchSize) {
    const batch = texts.slice(offset, offset + batchSize);
    const output = await extractor(batch, { pooling: "mean", normalize: true });
    const rows = output.tolist() as number[][];
    for (const row of rows) {
      vectors.push(row);
    }
    (self as any).postMessage({
      type: "embed-progress",
      done: Math.min(offset + batch.length, texts.length),
      total: texts.length,
    });
  }
  return vectors;
}

self.onmessage = async (event: MessageEvent) => {
  const message = event.data;
  try {
    if (message.type === "init") {
      const result = await init(message.model, message.precision, message.preferWebgpu);
      (self as any).postMessage({ type: "ready", requestId: message.requestId, ...result });
    } else if (message.type === "embed") {
      const vectors = await embed(message.texts, message.batchSize ?? 8);
      (self as any).postMessage({ type: "embeddings", requestId: message.requestId, vectors });
    } else {
      throw new Error(`unknown worker message: ${message.type}`);
    }
  } catch (error) {
    (self as any).postMessage({
      type: "error",
      requestId: message.requestId,
      message: error instanceof Error ? error.message : String(error),
    });
  }
};
