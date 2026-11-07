# 规有所依 —— 面向软件持续演进的 SDD 上下文管理系统

MiniMax × OceanBase 2026 生态赛参赛作品。题目要求为 AI 开发助手实现一个规格驱动开发（SDD）的工程上下文模块：把任务相关的规格、代码、测试从数据库中检索出来组成上下文，交给 MiniMax 完成一次真实代码修改，运行前后测试，并把任务状态跨进程保存与复用。

- 开发分支：`wangyunin/minimax-sdd`，基于官方 [powercontext](https://github.com/oceanbase/powercontext) 的 `2026-competition-ecosystem` 分支（起点提交 `d647275c308e09b7b623f322a26f5c4e85734fb0`）。
- 作品目录：本仓库的 `minimax/`。
- 业务样例：一个 31 行的工单服务 `TicketService.list_tickets`，为它增加 `status` 精确筛选，同时保持默认列表、排序、分页、响应结构完全不变。
- 本次任务已真实调用 MiniMax-M3 生成修改，修改前目标测试 4 failed、回归 8 passed，修改后 4 passed / 8 passed。

## 1. 三块技术各自承担什么

| 组件 | 实际作用 | 落地位置 |
| --- | --- | --- |
| **MiniMax** | 读取由数据库构造的上下文，真实生成 `src/ticket_service.py` 的完整修改后文件（OpenAI 兼容 `POST /v1/chat/completions`，模型 `MiniMax-M3`） | `src/sdd_context/minimax.py`；调用记录 `records/minimax/actual-run/` |
| **seekdb（pylibseekdb 嵌入式）** | 保存任务、规格（多版本）、代码、测试五类工程资料，以及 PowerContext 制品；上下文中的每一条规格/代码/测试都由 SQL 查询得到 | `src/sdd_context/database.py`；查询日志 `records/reproduction/database/query_log.jsonl` |
| **PowerContext** | 以 Artifact 制品（family `sdd_task_state`）保存任务进展与验证结果，revision 递增，新进程重启后读取并作为 `prior_state` 注入下一次上下文 | `src/sdd_context/state.py`；操作日志 `records/reproduction/powercontext/operations.jsonl` |

三者是一条链：seekdb 是事实来源 → 上下文 → MiniMax 改代码 → 测试结果 → PowerContext 存状态 → 下次运行读回状态再构造上下文。

## 2. 目录结构

```
minimax/
├── src/sdd_context/
│   ├── cli.py                  # 统一入口：import / import-spec / context / run-tests /
│   │                           #           save-state / resume / modify / apply-recorded
│   ├── database.py             # seekdb 嵌入式实例 + PyMySQL 参数化 SQL + 上下文构造
│   ├── minimax.py              # MiniMax 调用、推理块剥离、JSON 解析、补丁应用（白名单 + ast 校验）
│   ├── state.py                # PowerContext ArtifactDraft/Artifact + SeekdbArtifactBackend
│   ├── test_runner.py          # 子进程跑 pytest，记录命令、退出码、git commit、源文件 SHA256
│   └── util.py                 # utc_now / sha256 / JSON 读写 / jsonl 追加
├── sample_project/             # 业务样例（工单状态筛选）
│   ├── src/ticket_service.py   # 待修改的业务代码
│   ├── tests/                  # target 4 条 + regression 8 条 + conftest
│   ├── specs/                  # SPEC-TICKET-001 v1 / v2（v2 新增 REQ-FILTER-004）
│   ├── task.json               # task_id、code_files、test_files、allowed_write_paths、must_preserve
│   └── pytest.ini
├── scripts/verify.py           # 端到端验收：--mode online（真实调用）/ replay（离线重放）
├── scripts/preflight.py        # 不依赖 MiniMax 的全链路预检（写独立 records/preflight）
├── verification/snapshots/     # 修改前 / 修改后的业务代码快照
├── tests/                      # 上下文模块自身的单元测试（5 条）
├── records/                    # 运行证据（见 records/README.md）
├── requirements.txt            # pip freeze 固定依赖
├── .env.example                # 配置模板（真实 key 只写 .env，已 gitignore）
└── README.md / TECHNICAL_NOTE.md / VERIFICATION_SAMPLE.md
```

## 3. 环境与依赖

- Linux x86_64，Python 3.11.13（本次验证环境）；比赛标准环境 Python 3.12 + uv 同样可用，依赖已按 `pip freeze` 固定在 `requirements.txt`。
- 关键依赖：`powercontext==1.2.0`、`pylibseekdb==1.4.0.post2`、`PyMySQL==1.2.3`、`pytest==8.4.2`、`httpx==0.28.1`、`python-dotenv==1.2.4`。
- 不依赖外部 OceanBase 服务：pylibseekdb 提供嵌入式实例，每次验收使用独立数据目录（`.runtime/<name>.db`）。
- 除 MiniMax 额度外不需要其他外部资源；`--mode replay` 与 `preflight.py` 完全离线。

## 4. 配置

```bash
cd minimax
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env（该文件已 gitignore，不要提交）：
#   MINIMAX_API_KEY=<你的 key>
#   MINIMAX_MODEL=MiniMax-M3
#   MINIMAX_BASE_URL=https://api.minimaxi.com/v1/chat/completions
```

可选环境变量：`MINIMAX_TIMEOUT`（秒，默认 180）、`MINIMAX_MAX_TOKENS`（默认 8192）。

## 5. 运行

### 5.1 全链路验收（真实调用 MiniMax）

```bash
source .venv/bin/activate
PYTHONPATH=src python3 scripts/verify.py --mode online
```

依次执行：清空复现库 → 恢复修改前代码 → 导入 v1 规格/代码/测试 → 构造上下文 → 修改前测试（target 4 failed、regression 8 passed）→ 保存 `in_progress` 状态 → **真实调用 MiniMax 生成修改**（记录写入 `records/minimax/actual-run/`）→ 校验并应用补丁 → 修改后测试（4 passed、8 passed）→ 保存 `completed` 状态 → 新进程 `resume` 校验 revision=2 → 导入 v2 规格 → 上下文刷新出 `REQ-FILTER-004` → 输出 `records/reproduction/verification_summary.json`。

任一步失败即以非零状态中断，不会产出"通过"结论。

### 5.2 干净环境离线重放（不需要额度）

```bash
PYTHONPATH=src python3 scripts/verify.py --mode replay
```

重放读取 `records/minimax/actual-run/` 中已保存的 MiniMax 输出（默认重放源），不发起新调用，`replay_provenance.json` 中 `new_minimax_call: false`。同一份记录在线上运行后即被保存并提交，因此评审无需额度和 key 也能复现后半条链路。

### 5.3 非 MiniMax 预检（写独立目录，不覆盖真实验收证据）

```bash
PYTHONPATH=src python3 scripts/preflight.py
```

## 6. 分步命令

```bash
# 1) 导入业务样例（v1 规格 + 代码 + 测试）到 seekdb
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  import --project sample_project --spec sample_project/specs/SPEC-TICKET-001.v1.json

# 2) 从数据库构造上下文（含 prior_state）
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  context --task TASK-TICKET-STATUS --output records/demo/context.json

# 3) 跑测试（suite=target|regression|all，退出码即 pytest 退出码）
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  run-tests --project sample_project --suite target --log records/demo/target.log

# 4) 保存 / 恢复 PowerContext 状态（两次独立进程）
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  save-state --task TASK-TICKET-STATUS --state-file records/demo/state.json
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  resume --task TASK-TICKET-STATUS --output records/demo/resume.json

# 5) 真实调用 MiniMax 修改 / 离线重放已记录修改
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  modify --project sample_project --task TASK-TICKET-STATUS --record-dir records/demo/minimax
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  apply-recorded --project sample_project --task TASK-TICKET-STATUS --record-dir records/minimax/actual-run

# 6) 规格更新后重新导入并刷新上下文
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  import-spec --spec sample_project/specs/SPEC-TICKET-001.v2.json
```

### 模块输入与输出

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `import` | `sample_project/`（task.json、规格 JSON、代码、测试） | 写入 seekdb 五张表，返回任务/规格/代码/测试计数 |
| `context` | task_id、seekdb 中的工程资料、可选 prior_state | JSON：`task`、`specifications[]`（spec_id/version/requirements/source）、`code[]`（file_path/symbols/source）、`tests[]`（test_type/source）、`prior_state`、`provenance` |
| `run-tests` | 项目目录、suite | pytest 退出码 + 命令、git commit、源文件 SHA256 |
| `save-state` / `resume` | 状态 JSON / task_id | PowerContext revision 与内容；`resume` 额外返回重新构造的 `reused_context` |
| `modify` | 上下文、`.env` 中的 key | 修改后的业务代码 + `records/.../applied.diff`、`parsed_changes.json`、`run_metadata.json` |

## 7. 六项必做对照

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 1. 能够运行 | ✅ `verify.py --mode online` / `replay`、`preflight.py` 均可从干净状态跑通 | `records/reproduction/commands.jsonl`、`records/preflight/` |
| 2. MiniMax 实际参与 | ✅ 真实调用 `MiniMax-M3`，保存请求、响应、解析、diff、用量 | `records/minimax/actual-run/`（`request.json`/`response.json`/`parsed_changes.json`/`applied.diff`/`run_metadata.json`） |
| 3. 上下文正确 | ✅ 上下文含 v1/v2 规格条目、代码符号、target/regression 测试与 prior_state，来源均为 `seekdb.*`；导入 v2 后出现 `REQ-FILTER-004` | `records/reproduction/context/context_v1.json`、`context_v2.json` |
| 4. 数据库真实参与 | ✅ 建表与 INSERT/SELECT 真实执行，PowerContext 制品存于 seekdb 的 `pc_artifact_revisions` | `records/reproduction/database/query_log.jsonl`、`powercontext/operations.jsonl` |
| 5. 状态可复用 | ✅ 保存后新进程 `resume` 读回同 revision，并作为 `prior_state` 注入下一次上下文 | `records/reproduction/powercontext/save_*.json`、`resume_*.json`、`state_*.json` |
| 6. 代码修改有效 | ✅ 修改前 target 4 failed（退出码 1）/ 回归 8 passed（0）；修改后 4 passed / 8 passed；测试文件 SHA256 不变，业务代码 SHA256 改变 | `records/reproduction/tests/before_*`、`after_*`（日志 + JSON 元数据） |

## 8. 技术使用记录

- **MiniMax 开发记录**：`records/minimax/actual-run/`
  - `request.json`：脱敏请求（base_url、model、messages；不含 key）
  - `response.json`：脱敏响应（去掉 `reasoning`/`reasoning_content` 字段，保留可见输出与 usage）
  - `visible_output.txt`：模型可见输出原文（含 `<think>` 推理块，便于核对）
  - `parsed_changes.json`：解析出的 `{summary, changes}`；`applied.diff`：实际写入业务文件的统一 diff
  - `run_metadata.json`：模型名、usage（prompt/completion/reasoning tokens）、task_id
- **数据库查询记录**：`records/reproduction/database/query_log.jsonl`，逐条记录 SQL、参数、行数；建表 5 张、导入、上下文查询、状态读写均可审计。
- **PowerContext 状态写入与复用**：`records/reproduction/powercontext/operations.jsonl` 记录 `add`（revision 1, in_progress）与 `revise`（revision 2, completed）以及 `latest` 读取；`save_in_progress.json` / `resume_completed.json` 为跨进程读回结果。

## 9. 已知限制与说明

- 业务样例是刻意做小的单个模块，用于把 SDD 闭环做完整；模块本身（seekdb 存取、上下文构造、状态复用）不绑定这个样例，换 task.json + 规格即可换任务。
- 上下文选择目前是"任务声明的 code_files/test_files + 最新版本规格"的直接映射，没有做语义检索或排序；这是当前实现范围的边界，不是遗漏的隐藏行为。
- `MiniMax-M3` 会在 `content` 中返回 `<think>…</think>` 推理块，解析器会剥离该块后再解析 JSON（见 `src/sdd_context/minimax.py` 的 `_strip_reasoning`）；若上游模型/接口变化，需要重新跑 `--mode online`。
- 输出被 `max_tokens` 截断时会显式报错，不落盘半截补丁。
- MiniMax key 只存在于本地 `.env`（已 gitignore），仓库与证据中不含任何凭据。
- 复现所需的一切都在仓库内：`records/minimax/actual-run/` 是 `--mode replay` 的输入，无需额度。
- `verification_summary.json` 中的 `git_commit` 是生成该记录时的代码提交；运行产生的 `records/` 随后以一次证据提交入库。产品代码（`src/`、`scripts/`、`tests/`、`sample_project/`）在两次提交中完全相同，证据提交只新增或刷新 `records/` 与文档中的差异摘录。
