# 技术说明：端侧 AI 知识库（浏览器向量化 + seekdb 混合检索）

## 1. 模型与量化

- 模型：`Xenova/bge-small-zh-v1.5`（BAAI `bge-small-zh-v1.5` 的 ONNX 转换版，Hugging Face revision `75c43b069aac4d136ba6bc1122f995fedcfd2781`，输出 512 维）。备选英文模型 `Xenova/all-MiniLM-L6-v2`（384 维）。
- 量化方案：Transformers.js `dtype: "q8"`，加载 `onnx/model_quantized.onnx`（ONNX Runtime 动态 INT8 量化权重）；`dtype: "fp32"` 作为量化前基线；仓库同时提供 FP16 文件。
- 模型体积（Hugging Face 实际文件 HEAD 实测，`npm run eval:sizes`）：

| 精度 | bge-small-zh-v1.5 | 压缩比 |
| --- | --- | --- |
| FP32 | 90.5 MiB | 1.00× |
| INT8 | 22.9 MiB | **3.95×** |
| FP16 | 45.3 MiB | 1.99× |

- 检索质量（24 条语料 / 12 条带标注查询，Recall@3、MRR@3；`npm run eval:quality`，结果见 `results/quality.json`）：

| 精度 | Recall@3 | MRR@3 |
| --- | --- | --- |
| FP32 | 0.9306 | 1.000 |
| INT8 | 0.9028 | 1.000 |

INT8 量化后 Recall@3 下降 2.78 个百分点（相对约 -3.0%），MRR@3 不变；主要差异来自相近语义片段的排序，未出现相关文档整体丢失。

## 2. 端侧运行与后端降级

- 运行时：Transformers.js 3.x + ONNX Runtime Web；embedding 在 **Web Worker** 中执行，主线程只负责 UI 与进度。
- 后端选择：启动时检测 `navigator.gpu`，存在则用 `device: "webgpu"`；初始化抛错时自动改用 `device: "wasm"`，并在页面顶部显示降级提示；页头徽标始终显示当前实际后端（WebGPU / WASM）、精度与向量维度。
- ONNX Runtime 的 wasm 资源复制到 `app/public/ort` 本地提供，不依赖 CDN；模型文件通过浏览器缓存复用。
- **无云端推理**：文档与查询的向量都在浏览器内计算；网络只用于下载模型文件，可用 `VITE_HF_ENDPOINT` 指向镜像。

## 3. seekdb 存储与混合检索

- 连接：seekdb-js 1.4.0，默认**嵌入式**模式（`app/.data/kb.db`，Node 后端进程内）；设置 `SEEKDB_HOST` 可切换为本地 server 模式。
- Collection：`kb_<dim>`，配置 `hnsw: {dimension, distance: "cosine"}` + 全文索引 `ngram`（`ngram_token_size: 2`，适配中文）。
- 记录结构：`ids`（文档块 ID）、`documents`（原文）、`embeddings`（浏览器生成的向量）、`metadatas`（来源等）。
- 混合检索：浏览器生成查询向量后调用
  `collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings }, rank: { rrf: {} } })`。
  全文与向量的排名融合由 seekdb 完成，客户端不会分别取两路结果再自行拼合。
- 增量与刷新：新文档按 100 条一批 `upsert` 写入；seekdb 写入后索引即可见（下文实测），无需重建集合；清空时 `deleteCollection` 后重新创建。

## 4. 优化与实测

优化点：
1. embedding 批处理（batch = 8）与 seekdb 写入批处理（batch = 100）。
2. Web Worker 隔离推理，模型加载/推理不阻塞输入。
3. 优先 WebGPU；不可用时 WASM 兜底，核心功能不受影响。
4. ORT wasm 本地化 + 浏览器缓存，减少重复下载；增量写入避免全量重建索引。

seekdb 实测（本环境：Node v24.14.0 / linux-arm64 / 192 vCPU、1.5 GiB 内存容器；`npm run bench:seekdb`，结果见 `results/seekdb_bench.json`）：

| 指标 | 结果 |
| --- | --- |
| 文档数 / 维度 | 200 / 512 |
| 写入 | 2517 ms，约 **79.5 docs/s** |
| hybridSearch（100 次） | P50 **7.69 ms**，P90 8.75 ms，mean 7.76 ms |

浏览器端实测（Chromium 158 headless，Linux aarch64，测试环境无 GPU → 自动使用 WASM 后端；模型从本地文件加载；页面内“运行基准测试”执行 36 次查询，语料 24 条）：

| 配置 | 模型体积 | 向量化吞吐 | seekdb 检索延迟 | 端到端查询延迟 | 检索质量 |
| --- | --- | --- | --- | --- | --- |
| FP32 基线（Node 参考） | 90.5 MiB | 未在浏览器运行 | — | — | Recall@3 0.9306 |
| INT8 + WASM | 22.9 MiB | 约 18.8 块/秒（24 块 / 1277 ms） | P50 7 ms，P90 8 ms | P50 31 ms，P90 34 ms | Recall@3 0.9028 |
| INT8 + WebGPU | 22.9 MiB | 本环境无 GPU，未测 | — | — | 同 INT8 |

补充测量：
- 模型加载（WASM，本地文件）：1630 ms（含初始化与一次预热推理）。
- 导入 24 块：浏览器向量化 1277 ms + seekdb 写入 331 ms。
- 查询向量化：P50 19 ms，P90 22 ms（36 次）。
- 导入后首次 `hybridSearch` 约 3.0 s（引擎冷启动），预热后 P50 7 ms。
- 端到端 = 查询向量化 + seekdb 混合检索；不包含首次模型下载/加载。

> 浏览器数值来自本环境实际运行（无 GPU，因此只有 WASM 行）。WebGPU 行需在支持 WebGPU 的 Chrome/Edge 上运行页面基准测试生成；代码中的 `navigator.gpu.requestAdapter()` 探测保证无适配器时直接走 WASM 并在界面提示。

## 5. 复现

```bash
cd stepfun/app
npm install
npm run dev            # http://localhost:5173
# 代理环境下运行 Node 测量脚本：
export NODE_USE_ENV_PROXY=1
npm run eval:sizes     # results/model_sizes.json
npm run eval:quality   # results/quality.json
npm run bench:seekdb   # results/seekdb_bench.json
```

浏览器要求：Chrome / Edge 最新版。WebGPU 不可用时自动降级 WASM；模型下载需要访问 Hugging Face（或配置镜像）。
