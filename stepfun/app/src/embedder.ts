import type { Backend, Precision } from "./types";

export interface EmbedderEvents {
  onLoadProgress?: (info: { file?: string; progress: number }) => void;
  onEmbedProgress?: (done: number, total: number) => void;
  onFallback?: (reason: string) => void;
}

interface Pending {
  resolve: (value: any) => void;
  reject: (reason: Error) => void;
}

export class Embedder {
  readonly events: EmbedderEvents = {};
  private readonly worker: Worker;
  private readonly pending = new Map<number, Pending>();
  private seq = 0;

  constructor() {
    this.worker = new Worker(new URL("./embedder.worker.ts", import.meta.url), { type: "module" });
    this.worker.onmessage = (event: MessageEvent) => this.handle(event.data);
    this.worker.onerror = (event) => {
      const error = new Error(event.message || "embedding worker crashed");
      for (const item of this.pending.values()) item.reject(error);
      this.pending.clear();
    };
  }

  private handle(message: any) {
    if (message.type === "load-progress") {
      this.events.onLoadProgress?.({ file: message.file, progress: message.progress });
      return;
    }
    if (message.type === "embed-progress") {
      this.events.onEmbedProgress?.(message.done, message.total);
      return;
    }
    if (message.type === "fallback") {
      this.events.onFallback?.(message.reason);
      return;
    }
    const item = this.pending.get(message.requestId);
    if (!item) return;
    this.pending.delete(message.requestId);
    if (message.type === "error") item.reject(new Error(message.message));
    else item.resolve(message);
  }

  private call(payload: Record<string, unknown>): Promise<any> {
    const requestId = ++this.seq;
    const promise = new Promise<any>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
    });
    this.worker.postMessage({ ...payload, requestId });
    return promise;
  }

  init(model: string, precision: Precision, preferWebgpu: boolean) {
    return this.call({ type: "init", model, precision, preferWebgpu }) as Promise<{
      backend: Backend;
      dim: number;
      loadMs: number;
    }>;
  }

  async embed(texts: string[], batchSize = 8) {
    const message = await this.call({ type: "embed", texts, batchSize });
    return message.vectors as number[][];
  }

  dispose() {
    this.worker.terminate();
  }
}
