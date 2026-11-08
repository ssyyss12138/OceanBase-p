# 端侧 AI 知识库：浏览器本地向量化 + seekdb 混合检索

在浏览器中完成 embedding 推理，把向量与原文写入本地 seekdb，并用 seekdb 的
`hybridSearch`（向量 + 全文，RRF 融合）返回排序后的原文片段。

```text
导入文本 → 浏览器切块 → Transformers.js 本地向量化（WebGPU / WASM）
        → seekdb-js 嵌入式写入（HNNSW + 全文索引）
        → 查询向量本地生成 → seekdb hybridSearch → 展示结果
```

## 目录结构

```text
stepfun/
├── README.md               # 本文件：运行与复现说明
├── TECH_NOTE.md            # 一页技术说明（模型、量化、后端、seekdb、优化与实测）
├── data/
│   ├── corpus.jsonl        # 示例语料（24 条中文技术片段）
│   └── queries.jsonl       # 12 条带标注的评测查询
├── results/                # 实际运行产生的测量结果（JSON）
└── app/
    ├── server/index.mjs    # Express + Vite 中间件 + seekdb-js 嵌入式后端
    ├── src/                # React 前端、Embedding Web Worker
    ├── scripts/            # 质量评测、模型体积、seekdb 性能脚本
    └── package.json
```

## 环境要求

- Node.js **>= 20**（开发与本地 seekdb 后端）
- Chrome / Edge 最新版（WebGPU 可选；不支持时自动降级 WASM）
- Linux / macOS 可使用 seekdb 嵌入式模式；Windows 请用本地 server 模式（见下）
- 浏览器需要能访问 Hugging Face（国内网络可设置 HF 镜像，见“模型下载”）

## 启动

```bash
cd stepfun/app
npm install          # 自动把 ONNX Runtime 的 wasm 资源复制到 public/ort
npm run dev          # http://localhost:5173
```

`npm install` 会安装 `seekdb`（内嵌 seekdb 引擎）和 `@huggingface/transformers`。
首次启动 seekdb 时会在 `app/.data/kb.db` 创建嵌入式数据库。

生产模式：

```bash
npm run build
npm start            # 同端口，服务 dist/ + API
```

### seekdb 连接方式

默认嵌入式：

```text
app/.data/kb.db
```

也可以连接本地 server 模式的 seekdb / OceanBase：

```bash
SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD= npm run dev
```

### Linux 下的 LD_PRELOAD

嵌入式引擎是一个原生共享库。部分 Linux 环境需要先预加载它，否则 Node 会报
`cannot allocate memory in static TLS block`。`app/scripts/preload.mjs` 会自动
查找 `~/.seekdb/bindings/**/libseekdb.so` 并带 `LD_PRELOAD` 重新执行，无需手工设置。
若你的环境仍报错，可手动：

```bash
LIB=$(find ~/.seekdb/bindings -name libseekdb.so | head -1)
LD_PRELOAD=$LIB npm run dev
```

## 使用

1. 打开页面后自动加载模型（默认 `Xenova/bge-small-zh-v1.5`，INT8）。
2. 点“导入示例语料”，或拖入 / 粘贴自己的 `.txt`、`.md`、`.jsonl` 文本。
3. 观察进度条：切块 → 本地向量化 → 写入 seekdb。
4. 输入问题检索；结果包含原文、来源元数据和向量距离。
5. 点“运行基准测试”得到查询向量化 / seekdb 检索 / 端到端 P50、P90，并可导出 `bench.json`。

页头徽标会显示当前推理后端（WebGPU / WASM）、量化精度、向量维度和文本条数。
WebGPU 初始化失败时自动降级 WASM，并在页面顶部给出提示。

## 模型下载与镜像

默认从 Hugging Face 下载 ONNX 模型并缓存到浏览器。国内网络可在启动前设置镜像：

```bash
# Linux / macOS
VITE_HF_ENDPOINT=https://hf-mirror.com npm run dev
```

（该变量会写入 Transformers.js 的 `env.remoteHost`。）

## 复现测量

所有测量脚本都会写入 `stepfun/results/`。代理环境下 Node 需要开启环境代理：

```bash
export NODE_USE_ENV_PROXY=1   # Node 24+；或使用系统代理
```

```bash
cd stepfun/app
npm run eval:sizes      # 模型文件体积与压缩比（HEAD 请求 HF）
npm run eval:quality    # fp32 vs INT8 的 Recall@3 / MRR@3（会下载模型）
npm run bench:seekdb    # 嵌入式 seekdb 写入吞吐与 hybridSearch 延迟
node scripts/browser_check.mjs   # 可选：Playwright 无头浏览器端到端冒烟/基准
```

浏览器冒烟脚本需要 `npm i --no-save playwright && npx playwright install chromium`。
所有结果保存在 `stepfun/results/`。

浏览器内的性能（模型加载、向量化吞吐、端到端延迟）由页面“运行基准测试”生成，
结果可导出 `bench.json`；不同设备差异较大，报告中需注明设备与浏览器版本。

## 本环境实测（摘要）

- 模型体积：bge-small-zh-v1.5 FP32 90.5 MiB → INT8 22.9 MiB（3.95×）
- 检索质量（Recall@3 / MRR@3）：FP32 0.9306 / 1.000 → INT8 0.9028 / 1.000
- seekdb（200 文档，Node/CPU）：写入约 79.5 docs/s，hybridSearch P50 7.69 ms / P90 8.75 ms
- 浏览器（Chromium 158 headless，WASM 后端）：查询向量化 P50 19 ms，seekdb 检索 P50 7 ms，端到端 P50 31 ms

完整数据与测量方法见 `TECH_NOTE.md` 与 `results/`。

## 数据与限制

- `data/corpus.jsonl` 为本项目自建的中文技术片段，用于演示与质量评测；可替换为自己的语料。
- 不做云端推理：embedding 全部在浏览器 Worker 中执行。
- 向量与原文只写入 seekdb；检索由 seekdb `hybridSearch` 完成，客户端不拼接两路结果。
- 图片/PDF 解析不在当前范围内（文本知识库为基础要求）。
