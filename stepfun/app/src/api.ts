async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "";
    try {
      detail = await response.text();
    } catch {
      /* ignore */
    }
    throw new Error(`${method} ${url} -> ${response.status} ${detail}`);
  }
  return (await response.json()) as T;
}

export const api = {
  init: (dimension: number) =>
    request<{ count: number }>("POST", "/api/init", { dimension }),
  reset: (dimension: number) =>
    request<{ count: number }>("POST", "/api/reset", { dimension }),
  add: (dimension: number, docs: Array<{ id: string; text: string; metadata?: Record<string, unknown>; embedding: number[] }>) =>
    request<{ count: number; addMs: number }>("POST", "/api/add", { dimension, docs }),
  search: (dimension: number, query: string, embedding: number[], topK: number) =>
    request<{ hits: Array<{ id: string; text: string; metadata?: Record<string, unknown> | null; distance?: number | null }>; searchMs: number }>(
      "POST",
      "/api/search",
      { dimension, query, embedding, topK },
    ),
  stats: (dimension: number) =>
    request<{ count: number }>("GET", `/api/stats?dimension=${dimension}`),
  sample: () =>
    request<{ corpus: Array<{ id: string; text: string; tags?: string[] }>; queries: Array<{ query: string; relevant: string[] }> }>(
      "GET",
      "/api/sample",
    ),
};
