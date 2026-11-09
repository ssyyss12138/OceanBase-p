# 赛题六检查结果

检查的提交为 `bed3d9e2d0af788edbd4a89434f445eefcc6889f`，来自
`https://github.com/ssyyss12138/OceanBase-p` 的 `competition-0607` 分支。
公开资产来自官方 `2026-competition` 分支，提交为
`dab240063bfcfa7917898dc44da4baadb3747bd0`。
产品历史包含题目要求的冻结基线。

## 已复现的错误

1. **普通 prepare 的查询语义被累积池改变。** `_merge_memory_pool` 按 Scope
   累积历次查询命中，返回全部历史命中，替换了本轮候选集合和排序，并把条目上限
   从 8 扩到最多 32。先查询命中内容，再查询完全无关的词，仍返回旧正文。
   仓库原有测试 `test_builtin_runtime_uses_sqlite_fts_without_vector_extension`
   在提交版上失败：预期 `empty`，实际 `ready`。
2. **查询切换可能无法产生足够的不同 Context。** 同一份本地 QMSum 40 轮序列，
   提交版只产生 2 种 Context；切换 controls、programme 后仍返回累积内容。
   修复后产生 4 种 Context，且每轮非空。
3. **非法 Unicode 的异常类型不符合契约。**
   `ContextPack.from_content(chr(0xd800))` 直接抛出 `UnicodeEncodeError`，
   而不是 `ContextPackError`。短行编码发生在原有错误处理之外。

平台未提供失败日志，因此不能断言这些就是隐藏测试失败的全部原因。
公开 14 项测试没有覆盖第一、第二项；仅运行公开测试不足以发现它们。

## 修复内容

- 移除跨请求 Memory 累积池，恢复本轮检索候选、排序、条目预算和空结果行为。
- 移除 Builder 的池化旁路和来源生命周期中对应的缓存清理。
- 保留 prepare_incremental、公开 wire 协议及原有分块算法。
- 将非法 Unicode 的短行编码异常转换为 ContextPackError。
- 增加 34 项回归用例，覆盖查询切换、非空转空、UTF-8 上限、非法编码和有效前缀。
- 添加可复现的本地 40 轮测量脚本与带来源、许可的会议样本。

## 验证

在 Windows x64、Python 3.12 环境运行。使用公开 requirements 的固定依赖版本，
移除了 Linux 平台标记以安装 Windows 对应包；未安装 Linux 专用 pylibseekdb。
本题 Runtime 测试使用真实 SQLite 存储，不依赖 seekdb 或在线模型。
这不是官方 Linux 容器的完整环境复现。

各组在独立 Python 进程运行，避免公开测试包的同名模块发生冲突。

| 检查 | 结果 |
| --- | --- |
| 赛题六官方公开测试 | 14 通过 |
| 原有 PreparedContext Builder 与原生 Runtime 测试 | 77 通过 |
| 新增回归测试 | 34 通过 |
| 来源生命周期公开测试 | 4 通过 |
| 证据选择公开测试 | 3 通过 |
| 涉及文件的 Ruff lint、git diff 空白检查 | 通过 |

原有 evidence-selection 代码含预先存在的格式问题，未作无关格式改写。

## 本地 40 轮实际传输成本

输入和安排见 `tests/fixtures/context_pack/README.md`。
前八个真实会议 turn 初始化，第 11 轮追加一个 turn，并两次切换查询。
该序列由本地诊断定义，不是官方未公开的完整成本验收序列。

| 指标 | 修复版 |
| --- | ---: |
| 非空轮数 | 40 / 40 |
| 不同 Context 数 | 4 |
| 普通 prepare 正文 UTF-8 累计字节 | 117,670 |
| 首次完整 pack 加后续 delta 累计字节 | 24,095 |
| 相对普通正文的净节省率 | 79.52% |
| 完整 v1 pack 累计字节 | 164,090 |
| 相对完整 v1 pack 的节省率 | 85.32% |
| 独立逐轮 gzip 累计字节 | 49,910 |
| 相对 gzip 的节省率 | 51.72% |

首次完整 pack、JSON、Base64、摘要和全部字段开销均计入分子。
每轮均核对序列化重建后的正文、目标分块、摘要和实际字节数。
Runtime 每次创建的新 Scope 身份会影响 gzip 体积，重复测量可能略有波动。

提交版在同一输入安排下报告 91.27% 的净节省，但普通正文累计被扩大为
198,200 bytes，且只产生 2 种 Context。因此不能把该节省率作为合格结果。

本次还比较了固定容量分块与现有按行分块：在修复后的同一组正文上，
固定 512 bytes 分块传输 25,257 bytes，原有分块传输 24,095 bytes。
因此保留原有分块；当前优先修复查询和空结果兼容性。

公开包只有两条会议 turn 和基础集成测试，没有完整 40 轮测量配置。
这份本地报告不能保证官方隐藏序列达到 45%；仍需平台重新验收。

修复分支为 `fix/context-pack-compatibility`。提测时应填写该分支实际推送后的完整
40 位 commit SHA；原始 `bed3d9e2…` 不包含本报告中的修复。
