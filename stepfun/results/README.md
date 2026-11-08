# 测量结果

本目录保存提交应用实际运行产生的原始测量数据。

| 文件 | 生成方式 | 内容 |
| --- | --- | --- |
| `model_sizes.json` | `npm run eval:sizes` | 三个候选模型 FP32 / INT8 / FP16 的实际文件体积与压缩比 |
| `quality.json` | `npm run eval:quality` | 24 条语料、12 条查询下 FP32 与 INT8 的 Recall@3 / MRR@3 |
| `seekdb_bench.json` | `npm run bench:seekdb` | 嵌入式 seekdb 200 文档写入吞吐与 100 次 hybridSearch 的 P50/P90 |
| `browser_bench.json` | 应用内“运行基准测试”（Chromium 158 headless / WASM） | 模型加载、导入、查询向量化、seekdb 检索与端到端延迟 |

说明：

- `model_sizes.json` 由 Hugging Face 文件 HEAD 请求得到，是模型文件真实字节数。
- `quality.json`、`seekdb_bench.json` 在本环境（Node v24.14.0 / linux-arm64）运行得到，代理环境下需要 `NODE_USE_ENV_PROXY=1`。
- `browser_bench.json` 是浏览器 WASM 后端实测；本环境无 GPU，WebGPU 数值需在支持 WebGPU 的 Chrome/Edge 中运行页面基准测试后填写。
