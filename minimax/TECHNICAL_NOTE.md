# 一页技术说明：规有所依 —— SDD 上下文管理系统

## 作品做什么

用「工单状态筛选」这一最小业务样例，把规格驱动开发（SDD）的上下文闭环做通：**规格/代码/测试入库 → 构造上下文 → 前置测试失败 → MiniMax 真实改代码 → 后置测试通过 → 状态跨进程保存/恢复 → 规格更新自动刷新上下文**。三块技术各司其职，缺一不可。

## 模块结构

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 入口 | `src/sdd_context/cli.py` | 8 个子命令：`import`、`import-spec`、`context`、`run-tests`、`save-state`、`resume`、`modify`、`apply-recorded`；JSON 输出，失败返回非零 |
| 存储/上下文 | `database.py` | pylibseekdb 嵌入式实例 + PyMySQL 参数化 SQL；5 张表：`tasks`、`specifications`（主键含 version）、`code_files`、`test_files`、`pc_artifact_revisions`；`build_context()` 只从数据库取数 |
| 模型接入 | `minimax.py` | OpenAI 兼容调用、推理块剥离、JSON 解析、补丁应用（路径白名单 + `ast.parse` 校验） |
| 状态 | `state.py` | `ArtifactDraft[dict]` / `Artifact[dict]`（family `sdd_task_state`）+ `SeekdbArtifactBackend`（add/revise/get/latest/revisions） |
| 测试执行 | `test_runner.py` | 子进程 pytest，记录命令、退出码、git commit、源文件 SHA256 |

## 上下文是怎么选出来的

1. `import` 读取 `sample_project/task.json`，把任务、规格、代码、测试分别写入 seekdb；规格以 `(task_id, spec_id, version)` 为主键，v2 与 v1 并存。
2. `context` 三条查询：任务行、每个 spec 的 `MAX(version)` 行、该任务的全部代码/测试行；组装为 `specifications[]`、`code[]`、`tests[]`。
3. 每条记录带 `source`：`seekdb.specifications/<spec_id>/v<version>`、`seekdb.code_files/<path>`、`seekdb.test_files/<path>`，使"来源真实存在"可核对。
4. 代码符号由 `ast` 静态解析（类、方法、函数）写入 `symbols_json`；测试按 `task.json` 的 `target_tests` / `regression_tests` 分类。
5. 若存在 PowerContext 状态，作为 `prior_state` 注入上下文，供模型看到已完成/待办事项。

因此规格更新并重新导入后，下一次 `context` 的 `MAX(version)` 直接返回 v2，`REQ-FILTER-004` 自然出现——不是写死的答案。

## 三项技术的分工

**1. seekdb（pylibseekdb）—— 唯一事实来源与制品存储。**
- 嵌入式实例，无需外部数据库服务；每次验收用独立数据目录，互不干扰。
- `connection_options()["unix_socket"]` 交给 PyMySQL 做参数化查询（pylibseekdb 原生 Cursor 不支持参数元组，这是关键适配点）。
- 所有 SQL 写入 `records/.../database/query_log.jsonl`（含参数与行数），可审计"上下文确实来自数据库"。

**2. MiniMax —— 真实生成代码修改，不伪造、不冒充。**
- `POST https://api.minimaxi.com/v1/chat/completions`，`model=MiniMax-M3`，`temperature=0`；系统提示要求只输出 `{"summary","changes":[{"file_path","content"}]}`。
- MiniMax-M3 会在 `content` 前置 `<think>…</think>` 推理块，`_strip_reasoning()` 先剥离再解析 JSON，同时保留原文到 `visible_output.txt` 供核对；`response.json` 只脱敏 `reasoning` 字段，不删除可见输出。
- 应用补丁前强制校验：路径必须在 `allowed_write_paths` **且**出现在上下文代码清单；`.py` 先 `ast.parse` 通过才落盘；被 `max_tokens` 截断则直接报错，不写半截文件。
- 调用全过程落盘：`request.json`、`response.json`、`parsed_changes.json`、`applied.diff`、`run_metadata.json`（含 usage）。

**3. PowerContext —— 跨进程、跨重启的任务状态制品。**
- 制品存 seekdb 的 `pc_artifact_revisions`：先 `add` 得 revision 1（in_progress），再 `revise` 得 revision 2（completed），revision 冲突时拒绝写入。
- 新进程 `resume` 走 `latest` 取回状态，并作为 `prior_state` 注入下一次 `context`，实现"模块重启后读取并使用保存状态"。

## 数据流

```
sample_project ──import──▶ seekdb(tasks/specifications/code_files/test_files)
                                │  SELECT 构造
                                ▼
                          上下文 context.json ──▶ MiniMax(真实调用) ──▶ 补丁 ──▶ ticket_service.py
                                │                                              │
                                └── prior_state ◀── PowerContext Artifact ◀── 测试结果
```

## 关键设计决策

- **业务样例自洽**：`list_tickets` 增加可选 `status` 精确筛选；未传参时默认列表、排序、分页、响应结构、非法分页校验全部保持不变（`task.json` 的 5 条 `must_preserve`），回归 8 条守护兼容性。
- **可复现**：`--mode online` 产生真实记录；`--mode replay` 在干净环境重放同一份记录（`new_minimax_call:false`），前后测试 SHA256 比对证明"测试没改、只有业务代码变"。
- **证据隔离**：`online` 写 `records/minimax/actual-run/`（重放源）与 `records/reproduction/`；`preflight` 写 `records/preflight/` 与独立 `.runtime/preflight.db`，互不覆盖。
- **不可伪造**：无 key、额度不足、输出非法、路径越权、语法错误、输出截断都会显式失败，不存在"降级为通过"的分支。

## 验收对照

| 项 | 状态 |
| --- | --- |
| 1 可运行 / 3 正确上下文 / 4 数据库真实参与 / 5 状态重启可复用 | ✅ `verify.py`（online 与 replay）与 `preflight.py` 均跑通 |
| 2 MiniMax 真实参与 | ✅ `MiniMax-M3` 真实调用，记录在 `records/minimax/actual-run/` |
| 6 代码修改有效 | ✅ 前置 target 4 failed / 回归 8 passed；后置 4 passed / 8 passed；测试哈希不变 |
