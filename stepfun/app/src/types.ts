export type Backend = "webgpu" | "wasm";
export type Precision = "q8" | "fp32";

export interface Chunk {
  id: string;
  text: string;
  metadata?: Record<string, unknown>;
  embedding?: number[];
}

export interface SearchHit {
  id: string;
  text: string;
  metadata?: Record<string, unknown> | null;
  distance?: number | null;
}

export interface ModelInfo {
  model: string;
  precision: Precision;
  backend: Backend;
  dim: number;
}

export interface ImportStats {
  chunks: number;
  chars: number;
  embedMs: number;
  addMs: number;
  totalMs: number;
}

export interface SearchStats {
  embedMs: number;
  searchMs: number;
  totalMs: number;
}
