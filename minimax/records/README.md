# records 索引：本次运行的真实证据

本目录全部由 `scripts/verify.py`、`scripts/preflight.py` 与 `sdd_context.cli` 真实运行产生，没有手工编写的记录，也没有伪造的模型输出。`.runtime/` 下的 seekdb 数据目录不入库（已在 `.gitignore`）。

## 目录说明

| 路径 | 产生方式 | 内容 |
| --- | --- | --- |
| `minimax/actual-run/` | `verify.py --mode online` 的真实 MiniMax 调用 | `request.json`（脱敏请求）、`response.json`（脱敏响应，保留可见输出与 usage）、`visible_output.txt`（原文，含 `<think>` 推理块）、`parsed_changes.json`、`applied.diff`、`applied_files.json`、`http_status.json`、`run_metadata.json` |
| `reproduction/` | `verify.py` 全链路 | `commands.jsonl`（每条子命令、退出码、期望值）、`tests/`（前/后 target+regression 日志与元数据）、`context/`（v1/v2 上下文）、`database/`（query_log.jsonl、导入与规格更新结果）、`powercontext/`（状态、save/resume 结果、operations.jsonl）、`verification_summary.json` |
| `preflight/` | `scripts/preflight.py` | 与 `reproduction/` 同结构，但不调用 MiniMax、且写独立 `.runtime/preflight.db`，用于快速自检 |

> `records/minimax/actual-run/` 同时是 `--mode replay` 的输入。删除它会让 replay 明确报错，而不是产出假结果。

## 与验收项对应

| 验收项 | 先看这些文件 |
| --- | --- |
| 1 能够运行 | `reproduction/commands.jsonl`（12 条子命令的退出码）、`reproduction/verification_summary.json` |
| 2 MiniMax 实际参与 | `minimax/actual-run/request.json`、`response.json`、`parsed_changes.json`、`applied.diff`、`run_metadata.json` |
| 3 上下文正确 | `reproduction/context/context_v1.json`、`context_v2.json` |
| 4 数据库真实参与 | `reproduction/database/query_log.jsonl`、`reproduction/powercontext/operations.jsonl` |
| 5 状态可复用 | `reproduction/powercontext/save_in_progress.json`、`resume_completed.json`、`state_completed.json` |
| 6 代码修改有效 | `reproduction/tests/before_target.log`、`after_target.log`、`before_regression.log`、`after_regression.log` 及同名 `.json` |

## 阅读提示

- 测试 `.json` 元数据里的 `source_sha256` 是判断"测试没被改、只有业务代码变"的依据；`git_commit` 是生成记录时的代码提交。
- 各文件中的绝对路径来自本次运行的容器环境（`/mnt/kai_ckp/...`）；在自己的目录重跑时路径会随之改变，属正常现象。
- 重新生成：`PYTHONPATH=src python3 scripts/verify.py --mode online`（需要额度）或 `--mode replay`（离线）；`preflight` 单独生成 `preflight/`。
