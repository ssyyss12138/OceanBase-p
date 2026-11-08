import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Embedder } from "./embedder";
import type { Backend, SearchHit, SearchStats, ImportStats, ModelInfo, Precision } from "./types";

const MODELS = [
  { id: "Xenova/bge-small-zh-v1.5", label: "bge-small-zh-v1.5（中文，512 维，推荐）" },
  { id: "Xenova/all-MiniLM-L6-v2", label: "all-MiniLM-L6-v2（英文，384 维）" },
];

const PRECISIONS: Array<{ id: Precision; label: string }> = [
  { id: "q8", label: "INT8 量化（q8，推荐）" },
  { id: "fp32", label: "FP32 基线（体积约 4 倍）" },
];

const BENCH_QUERIES = [
  "浏览器里怎么用 WebGPU 跑模型",
  "INT8 量化后模型体积能小多少",
  "混合检索怎么融合全文和向量结果",
  "增量导入新文档后检索不到怎么办",
  "如何评测检索质量 Recall@K",
  "文档切块大小怎么选",
];

function djb2(text: string): string {
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash + text.charCodeAt(i)) >>> 0;
  }
  return hash.toString(36);
}

function splitText(text: string, size: number, overlap: number): string[] {
  const clean = text.replace(/\r\n/g, "\n").trim();
  if (!clean) return [];
  if (clean.length <= size) return [clean];
  const chunks: string[] = [];
  let start = 0;
  while (start < clean.length) {
    let end = Math.min(start + size, clean.length);
    if (end < clean.length) {
      const paragraph = clean.indexOf("\n", start + Math.floor(size * 0.6));
      if (paragraph > start && paragraph < end) end = paragraph;
    }
    const piece = clean.slice(start, end).trim();
    if (piece) chunks.push(piece);
    if (end >= clean.length) break;
    start = Math.max(end - overlap, start + 1);
  }
  return chunks;
}

function percentile(values: number[], p: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index];
}

export default function App() {
  const embedderRef = useRef<Embedder | null>(null);
  const initStarted = useRef(false);
  const sampleCache = useRef<{ queries: Array<{ query: string; relevant: string[] }> } | null>(null);

  const [model, setModel] = useState(MODELS[0].id);
  const [precision, setPrecision] = useState<Precision>("q8");
  const [preferWebgpu, setPreferWebgpu] = useState(true);
  const [loading, setLoading] = useState(false);
  const [loadProgress, setLoadProgress] = useState(0);
  const [loadFile, setLoadFile] = useState("");
  const [ready, setReady] = useState<ModelInfo | null>(null);
  const [loadMs, setLoadMs] = useState<number | null>(null);
  const [status, setStatus] = useState("准备加载模型…");
  const [fallback, setFallback] = useState("");
  const [docCount, setDocCount] = useState(0);

  const [rawText, setRawText] = useState("");
  const [chunkSize, setChunkSize] = useState(480);
  const [overlap, setOverlap] = useState(80);
  const [importing, setImporting] = useState(false);
  const [embedProgress, setEmbedProgress] = useState({ done: 0, total: 0 });
  const [importStats, setImportStats] = useState<ImportStats | null>(null);

  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);
  const [searching, setSearching] = useState(false);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searchStats, setSearchStats] = useState<SearchStats | null>(null);
  const [searchedQuery, setSearchedQuery] = useState("");

  const [benchRunning, setBenchRunning] = useState(false);
  const [benchRows, setBenchRows] = useState<Array<{ embedMs: number; searchMs: number; totalMs: number }>>([]);
  const [log, setLog] = useState<string[]>([]);

  const appendLog = (line: string) => {
    setLog((current) => [`${new Date().toLocaleTimeString()}  ${line}`, ...current].slice(0, 40));
  };

  const ensureEmbedder = () => {
    if (!embedderRef.current) {
      const embedder = new Embedder();
      embedder.events.onLoadProgress = (info) => {
        setLoadFile(info.file ?? "");
        setLoadProgress(Math.round(info.progress));
      };
      embedder.events.onEmbedProgress = (done, total) => setEmbedProgress({ done, total });
      embedder.events.onFallback = (reason) => {
        setFallback(reason);
        appendLog(`WebGPU 初始化失败，已自动降级到 WASM：${reason}`);
      };
      embedderRef.current = embedder;
    }
    return embedderRef.current;
  };

  const initModel = async (nextModel = model, nextPrecision = precision, gpu = preferWebgpu) => {
    setLoading(true);
    setFallback("");
    setLoadProgress(0);
    setLoadFile("");
    setStatus("正在加载端侧模型…");
    const started = performance.now();
    try {
      const embedder = ensureEmbedder();
      const result = await embedder.init(nextModel, nextPrecision, gpu);
      setReady({ model: nextModel, precision: nextPrecision, backend: result.backend, dim: result.dim });
      setLoadMs(result.loadMs);
      const init = await api.init(result.dim);
      setDocCount(init.count);
      setStatus(
        `模型就绪：${nextModel} · ${nextPrecision.toUpperCase()} · ${result.backend.toUpperCase()} · ${result.dim} 维`,
      );
      appendLog(`模型加载完成（${result.backend.toUpperCase()}，${result.loadMs} ms）`);
    } catch (error) {
      setStatus(`模型加载失败：${error instanceof Error ? error.message : String(error)}`);
      appendLog(`模型加载失败：${String(error)}`);
    } finally {
      setLoading(false);
      setLoadMs(Math.round(performance.now() - started));
    }
  };

  useEffect(() => {
    if (initStarted.current) return;
    initStarted.current = true;
    void initModel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => {
      embedderRef.current?.dispose();
      embedderRef.current = null;
    };
  }, []);

  const importTexts = async (items: Array<{ name: string; text: string }>) => {
    if (!ready) return;
    const chunks: Array<{ id: string; text: string }> = [];
    for (const item of items) {
      splitText(item.text, chunkSize, overlap).forEach((piece, index) => {
        chunks.push({ id: `${item.name}#${index}-${djb2(piece)}`, text: piece });
      });
    }
    if (!chunks.length) {
      setStatus("没有可导入的文本");
      return;
    }
    setImporting(true);
    setEmbedProgress({ done: 0, total: chunks.length });
    setStatus(`正在本地向量化 ${chunks.length} 个文本块…`);
    const started = performance.now();
    try {
      const vectors = await ensureEmbedder().embed(chunks.map((chunk) => chunk.text), 8);
      const embedMs = Math.round(performance.now() - started);
      setStatus("正在写入 seekdb…");
      const writeStarted = performance.now();
      const result = await api.add(
        ready.dim,
        chunks.map((chunk, index) => ({
          id: chunk.id,
          text: chunk.text,
          metadata: { source: chunk.id.split("#")[0] },
          embedding: vectors[index],
        })),
      );
      const addMs = Math.round(performance.now() - writeStarted);
      const totalMs = Math.round(performance.now() - started);
      setDocCount(result.count);
      setImportStats({ chunks: chunks.length, chars: chunks.reduce((sum, item) => sum + item.text.length, 0), embedMs, addMs, totalMs });
      setStatus(`导入完成：${chunks.length} 块，写入 ${result.count} 条，seekdb 索引已刷新`);
      appendLog(`导入 ${chunks.length} 块：向量化 ${embedMs} ms，写入 ${addMs} ms`);
    } catch (error) {
      setStatus(`导入失败：${error instanceof Error ? error.message : String(error)}`);
      appendLog(`导入失败：${String(error)}`);
    } finally {
      setImporting(false);
    }
  };

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const items: Array<{ name: string; text: string }> = [];
    for (const file of Array.from(files)) {
      items.push({ name: file.name, text: await file.text() });
    }
    await importTexts(items);
  };

  const loadSample = async () => {
    try {
      const sample = await api.sample();
      sampleCache.current = sample;
      await importTexts(sample.corpus.map((doc) => ({ name: doc.id, text: doc.text })));
    } catch (error) {
      setStatus(`示例语料加载失败：${String(error)}`);
    }
  };

  const search = async () => {
    if (!ready || !query.trim()) return;
    setSearching(true);
    setStatus("正在本地生成查询向量并检索…");
    const started = performance.now();
    try {
      const vectorStarted = performance.now();
      const [embedding] = await ensureEmbedder().embed([query.trim()], 1);
      const embedMs = Math.round(performance.now() - vectorStarted);
      const result = await api.search(ready.dim, query.trim(), embedding, topK);
      const totalMs = Math.round(performance.now() - started);
      setHits(result.hits);
      setSearchedQuery(query.trim());
      setSearchStats({ embedMs, searchMs: result.searchMs, totalMs });
      setStatus(`检索完成：${result.hits.length} 条结果，端到端 ${totalMs} ms`);
      appendLog(`检索 "${query.trim()}"：向量化 ${embedMs} ms，seekdb ${result.searchMs} ms`);
    } catch (error) {
      setStatus(`检索失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSearching(false);
    }
  };

  const reset = async () => {
    if (!ready) return;
    try {
      const result = await api.reset(ready.dim);
      setDocCount(result.count);
      setHits([]);
      setImportStats(null);
      setStatus("知识库已清空");
      appendLog("已删除 collection 并重建");
    } catch (error) {
      setStatus(`清空失败：${String(error)}`);
    }
  };

  const runBenchmark = async () => {
    if (!ready) return;
    setBenchRunning(true);
    setBenchRows([]);
    try {
      const sample = sampleCache.current ?? (await api.sample());
      sampleCache.current = sample;
      const queries = sample.queries.length ? sample.queries.map((item) => item.query) : BENCH_QUERIES;
      const rows: Array<{ embedMs: number; searchMs: number; totalMs: number }> = [];
      for (const text of queries) {
        for (let repetition = 0; repetition < 3; repetition += 1) {
          const started = performance.now();
          const vectorStarted = performance.now();
          const [embedding] = await ensureEmbedder().embed([text], 1);
          const embedMs = Math.round(performance.now() - vectorStarted);
          const result = await api.search(ready.dim, text, embedding, topK);
          rows.push({ embedMs, searchMs: result.searchMs, totalMs: Math.round(performance.now() - started) });
        }
      }
      setBenchRows(rows);
      appendLog(`基准测试完成：${rows.length} 次查询`);
    } catch (error) {
      setStatus(`基准测试失败：${String(error)}`);
    } finally {
      setBenchRunning(false);
    }
  };

  const exportBenchmark = () => {
    const payload = {
      model: ready?.model,
      precision: ready?.precision,
      backend: ready?.backend,
      dim: ready?.dim,
      docCount,
      rows: benchRows,
      p50: {
        embedMs: percentile(benchRows.map((row) => row.embedMs), 50),
        searchMs: percentile(benchRows.map((row) => row.searchMs), 50),
        totalMs: percentile(benchRows.map((row) => row.totalMs), 50),
      },
      exportedAt: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "bench.json";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const p50 = benchmarkP50(benchRows);

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>seekdb 端侧知识库</h1>
          <p className="subtitle">浏览器本地向量化 · seekdb 向量 + 全文混合检索</p>
        </div>
        <div className="badges">
          <span className={`badge ${ready ? "badge-ok" : "badge-muted"}`}>
            {loading ? "加载中…" : ready ? `${ready.backend === "webgpu" ? "WebGPU" : "WASM"} 后端` : "未就绪"}
          </span>
          <span className="badge badge-muted">{ready ? `${ready.precision.toUpperCase()} · ${ready.dim} 维` : "模型未加载"}</span>
          <span className="badge badge-muted">{docCount} 条文本</span>
        </div>
      </header>

      {fallback && (
        <div className="alert">
          当前环境不支持或无法使用 WebGPU，已自动降级到 WASM 后端。推理仍在本机完成。原因：{fallback}
        </div>
      )}

      <main className="grid">
        <section className="panel">
          <h2>1. 端侧模型</h2>
          <label className="field">
            <span>模型</span>
            <select value={model} onChange={(event) => setModel(event.target.value)} disabled={loading || importing}>
              {MODELS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>精度</span>
            <select value={precision} onChange={(event) => setPrecision(event.target.value as Precision)} disabled={loading || importing}>
              {PRECISIONS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={preferWebgpu} onChange={(event) => setPreferWebgpu(event.target.checked)} />
            优先 WebGPU（不可用时自动降级 WASM）
          </label>
          <button className="primary" onClick={() => void initModel()} disabled={loading || importing}>
            {loading ? "加载中…" : "重新加载模型"}
          </button>
          {loading && (
            <div className="progress">
              <div className="progress-bar" style={{ width: `${loadProgress}%` }} />
              <span>
                {loadProgress}% {loadFile ? `· ${loadFile}` : ""}
              </span>
            </div>
          )}
          <p className="hint">{status}</p>
          {loadMs !== null && !loading && <p className="hint">模型加载耗时：{loadMs} ms（首次含下载/解压）</p>}
        </section>

        <section className="panel">
          <h2>2. 导入语料</h2>
          <input type="file" accept=".txt,.md,.json,.jsonl" multiple onChange={(event) => void onFiles(event.target.files)} disabled={!ready || importing} />
          <textarea
            rows={5}
            placeholder="也可以直接粘贴文本，多个文档用空行分隔"
            value={rawText}
            onChange={(event) => setRawText(event.target.value)}
            disabled={!ready || importing}
          />
          <div className="row">
            <label className="field compact">
              <span>块大小</span>
              <input type="number" min={100} max={2000} value={chunkSize} onChange={(event) => setChunkSize(Number(event.target.value) || 480)} />
            </label>
            <label className="field compact">
              <span>重叠</span>
              <input type="number" min={0} max={400} value={overlap} onChange={(event) => setOverlap(Number(event.target.value) || 0)} />
            </label>
          </div>
          <div className="row">
            <button
              className="primary"
              onClick={() => void importTexts(rawText.split(/\n\s*\n/).map((text, index) => ({ name: `paste-${index}`, text })))}
              disabled={!ready || importing || !rawText.trim()}
            >
              导入粘贴文本
            </button>
            <button onClick={() => void loadSample()} disabled={!ready || importing}>
              导入示例语料
            </button>
            <button onClick={() => void reset()} disabled={!ready || importing}>
              清空知识库
            </button>
          </div>
          {importing && (
            <div className="progress">
              <div className="progress-bar" style={{ width: `${embedProgress.total ? (embedProgress.done / embedProgress.total) * 100 : 0}%` }} />
              <span>
                向量化 {embedProgress.done}/{embedProgress.total}
              </span>
            </div>
          )}
          {importStats && (
            <p className="hint">
              本次导入 {importStats.chunks} 块 / {importStats.chars} 字符：向量化 {importStats.embedMs} ms，写入 seekdb{" "}
              {importStats.addMs} ms，合计 {importStats.totalMs} ms
            </p>
          )}
        </section>

        <section className="panel">
          <h2>3. 混合检索</h2>
          <div className="row">
            <input
              className="grow"
              placeholder="输入问题，例如：混合检索怎么融合两路结果？"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void search();
              }}
              disabled={!ready || searching}
            />
            <label className="field compact">
              <span>TopK</span>
              <input type="number" min={1} max={20} value={topK} onChange={(event) => setTopK(Number(event.target.value) || 5)} />
            </label>
            <button className="primary" onClick={() => void search()} disabled={!ready || searching || !query.trim()}>
              {searching ? "检索中…" : "检索"}
            </button>
          </div>
          {searchStats && (
            <p className="hint">
              查询向量化 {searchStats.embedMs} ms · seekdb 混合检索 {searchStats.searchMs} ms · 端到端 {searchStats.totalMs} ms
            </p>
          )}
          <div className="results">
            {hits.map((hit, index) => (
              <article key={hit.id} className="hit">
                <div className="hit-head">
                  <span className="rank">#{index + 1}</span>
                  <span className="hit-id">{hit.id}</span>
                  {typeof hit.distance === "number" && <span className="distance">cosine 距离 {hit.distance.toFixed(4)}</span>}
                </div>
                <p>{hit.text}</p>
                {hit.metadata && <p className="meta">{Object.entries(hit.metadata).map(([key, value]) => `${key}: ${String(value)}`).join(" · ")}</p>}
              </article>
            ))}
            {!hits.length && searchedQuery && !searching && <p className="hint">没有找到结果，可以尝试换一种说法或先导入语料。</p>}
          </div>
        </section>

        <section className="panel">
          <h2>4. 基准测试</h2>
          <p className="hint">对示例查询重复 3 次，记录查询向量化、seekdb 检索与端到端延迟（浏览器实际运行数据）。</p>
          <div className="row">
            <button className="primary" onClick={() => void runBenchmark()} disabled={!ready || benchRunning || docCount === 0}>
              {benchRunning ? "测试中…" : "运行基准测试"}
            </button>
            <button onClick={exportBenchmark} disabled={!benchRows.length}>
              导出 bench.json
            </button>
          </div>
          {benchRows.length > 0 && (
            <>
              <table className="bench">
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>P50</th>
                    <th>P90</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>查询向量化</td>
                    <td>{p50.embedMs} ms</td>
                    <td>{percentile(benchRows.map((row) => row.embedMs), 90)} ms</td>
                  </tr>
                  <tr>
                    <td>seekdb 检索</td>
                    <td>{p50.searchMs} ms</td>
                    <td>{percentile(benchRows.map((row) => row.searchMs), 90)} ms</td>
                  </tr>
                  <tr>
                    <td>端到端</td>
                    <td>{p50.totalMs} ms</td>
                    <td>{percentile(benchRows.map((row) => row.totalMs), 90)} ms</td>
                  </tr>
                </tbody>
              </table>
              <p className="hint">{benchRows.length} 次查询 · 模型 {ready?.model} · {ready?.precision.toUpperCase()} · {ready?.backend.toUpperCase()}</p>
            </>
          )}
        </section>

        <section className="panel log-panel">
          <h2>运行日志</h2>
          <pre className="log">{log.join("\n")}</pre>
        </section>
      </main>
    </div>
  );
}

function benchmarkP50(rows: Array<{ embedMs: number; searchMs: number; totalMs: number }>) {
  return {
    embedMs: percentile(rows.map((row) => row.embedMs), 50),
    searchMs: percentile(rows.map((row) => row.searchMs), 50),
    totalMs: percentile(rows.map((row) => row.totalMs), 50),
  };
}
