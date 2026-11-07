# 验证样例：要改什么、哪些不能变、怎么核对

样例任务 `TASK-TICKET-STATUS`：给工单列表加「按状态筛选」。这是一个人为缩小的业务样例，但规格、代码、测试、状态四类资料与真实工程同构。

## 一、任务边界

**要改什么**（对应 `sample_project/specs/SPEC-TICKET-001.v1.json`，v2 追加 REQ-FILTER-004）：

| 需求 | 内容 |
| --- | --- |
| REQ-FILTER-001 | `list_tickets` 接受可选 `status`，按 `open`/`in_progress`/`resolved`/`closed` 精确匹配 |
| REQ-FILTER-002 | 先筛选再分页，`total` 为筛选后的总数 |
| REQ-FILTER-003 | 其他 `status` 值抛 `ValueError`，消息包含 `unsupported status` |
| REQ-FILTER-004（v2） | 状态匹配区分大小写，`OPEN` 属于非法值 |

**哪些原有行为必须保持**（`sample_project/task.json` 的 `must_preserve`，由 8 条回归测试守护）：

1. 未传 `status` 时返回全部工单；
2. `created_at` 降序，同 `created_at` 时 `id` 降序；
3. 筛选后再执行 1 基分页，`total` 是筛选后的总数；
4. 响应字段（`items`/`page`/`page_size`/`total`）及工单字段不变；
5. 原有非法分页参数校验（`page<1`、`page_size<1`）保持不变。

**允许修改的文件**只有 `src/ticket_service.py`（`task.json` 的 `allowed_write_paths`）；测试文件不在白名单内，`steps` 结束时会断言其 SHA256 未变。

## 二、初始数据

`TicketService` 用 6 条工单构造（`sample_project/tests/conftest.py`）：

| id | status | created_at |
| ---: | --- | --- |
| 1 | open | 2026-01-01T09:00:00Z |
| 2 | closed | 2026-01-03T09:00:00Z |
| 3 | in_progress | 2026-01-02T09:00:00Z |
| 4 | open | 2026-01-05T09:00:00Z |
| 5 | resolved | 2026-01-04T09:00:00Z |
| 6 | open | 2026-01-05T09:00:00Z |

默认顺序（`created_at` 降序、同日 `id` 降序）：`6, 4, 5, 2, 3, 1`。

修改前 `src/ticket_service.py` 的 `list_tickets` 虽有 `status` 形参，但完全忽略它——这就是"目标测试应当失败"的起点，不是环境错误。

## 三、上下文必须包含的来源（评审核对用）

`context` 命令输出中的必需项，全部带 `source: seekdb.*`：

| 类别 | 必需内容 | 来源字符串 |
| --- | --- | --- |
| 规格 | `SPEC-TICKET-001` 当前版本（v1：3 条需求；更新后 v2：4 条需求，含 `REQ-FILTER-004`） | `seekdb.specifications/SPEC-TICKET-001/v1`（更新后 `…/v2`） |
| 代码 | `src/ticket_service.py`，符号 `Ticket`、`TicketService`、`TicketService.list_tickets` | `seekdb.code_files/src/ticket_service.py` |
| 目标测试 | `tests/test_ticket_status_filter.py`，`test_type=target`，4 个测试名 | `seekdb.test_files/tests/test_ticket_status_filter.py` |
| 回归测试 | `tests/test_ticket_regression.py`，`test_type=regression` | `seekdb.test_files/tests/test_ticket_regression.py` |
| 支撑文件 | `tests/conftest.py`，`test_type=support` | `seekdb.test_files/tests/conftest.py` |
| 任务状态 | 首次为 `null`；保存后为 `prior_state`（含 `status`/`completed`/`todo`/`validation`/`code_sha256`） | PowerContext 制品 revision 1→2 |

## 四、一键复现

```bash
cd minimax
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # 填入 MINIMAX_API_KEY；模型默认 MiniMax-M3
```

```bash
# A. 真实调用 MiniMax 的完整验收（需要额度）
PYTHONPATH=src python3 scripts/verify.py --mode online

# B. 或用已提交的真实调用记录离线重放（不需要额度、不需要 key）
PYTHONPATH=src python3 scripts/verify.py --mode replay

# C. 只验证不依赖 MiniMax 的全链路（写 records/preflight，不覆盖 A/B 证据）
PYTHONPATH=src python3 scripts/preflight.py
```

三条命令成功时最后一行均输出 `verification_summary.json`（C 为 `PREFLIGHT OK`），进程退出码 0；任一步断言失败都会抛异常并以非零退出。

## 五、操作步骤与预期结果（A 模式）

| 步骤 | 命令（由 `verify.py` 自动执行） | 预期结果 |
| --- | --- | --- |
| 1 | 恢复 `verification/snapshots/before/src/ticket_service.py` | 代码回到"忽略 status"状态 |
| 2 | `cli import --project sample_project --spec …v1.json` | 写入 1 任务 / 1 规格 / 1 代码 / 3 测试文件 |
| 3 | `cli context --task TASK-TICKET-STATUS` | 规格 v1、代码符号、target/regression 测试齐全，`prior_state=null`，来源全部 `seekdb.*` |
| 4 | `cli run-tests --suite target` | **exit 1，4 failed**（见下方失败清单） |
| 5 | `cli run-tests --suite regression` | **exit 0，8 passed** |
| 6 | `cli save-state`（in_progress） | PowerContext revision 1 |
| 7 | `cli modify` → 真实 POST MiniMax | 记录 `request/response/parsed_changes/applied.diff`；补丁通过白名单与 `ast` 校验后写入 |
| 8 | `cli run-tests --suite target` | **exit 0，4 passed** |
| 9 | `cli run-tests --suite regression` | **exit 0，8 passed** |
| 10 | 校验测试未变 | 两个测试文件 SHA256 前后一致；`src/ticket_service.py` SHA256 改变 |
| 11 | `cli save-state`（completed） | PowerContext revision 2 |
| 12 | 新进程 `cli resume` | 读回 revision 2、`status=completed`、`todo=[]` |
| 13 | `cli import-spec …v2.json` + `cli context` | 规格版本变 v2，需求中出现 `REQ-FILTER-004`，`prior_state.status=completed` |

### 修改前：目标测试 4 failed（退出码 1）

```
FAILED tests/test_ticket_status_filter.py::test_open_filter_limits_items_and_total
FAILED tests/test_ticket_status_filter.py::test_filter_applies_before_pagination
FAILED tests/test_ticket_status_filter.py::test_closed_filter_returns_only_closed_tickets
FAILED tests/test_ticket_status_filter.py::test_invalid_status_is_rejected
4 failed in 0.07s
```

失败原因均为目标行为缺失（断言 `[6,4,1] != [6,4,5,2,3,1]`、`DID NOT RAISE ValueError` 等），不是缺依赖或收集错误；回归 8 passed 证明环境可用、原有行为正常。

### 修改后：4 passed / 8 passed（退出码 0）

```
....                                                                     [100%]
4 passed in 0.01s
```

### 实际生效的代码差异

```diff
@@ -20,7 +20,15 @@
         if page_size < 1:
             raise ValueError("page_size must be at least 1")
-        ordered = sorted(self._tickets, key=lambda ticket: (ticket.created_at, ticket.id), reverse=True)
+        if status is not None and status not in self.VALID_STATUSES:
+            raise ValueError(f"unsupported status: {status}")
+
+        filtered = (
+            self._tickets
+            if status is None
+            else tuple(ticket for ticket in self._tickets if ticket.status == status)
+        )
+        ordered = sorted(filtered, key=lambda ticket: (ticket.created_at, ticket.id), reverse=True)
```

（完整 diff 见 `records/minimax/actual-run/applied.diff`，修改前后源码见 `verification/snapshots/`。）

## 六、规格更新后上下文刷新（不能是固定答案）

```bash
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  import-spec --spec sample_project/specs/SPEC-TICKET-001.v2.json
PYTHONPATH=src python3 -m sdd_context.cli --db .runtime/demo.db --records records/demo \
  context --task TASK-TICKET-STATUS --output records/demo/context_v2.json
```

`context_v2.json` 中 `specifications[0].version == 2`，需求 id 列表包含 `REQ-FILTER-004`；再把 v1 的 JSON 内容改一处（如新增 REQ-FILTER-005）并 `import-spec`（版本号需递增），重新 `context` 即可看到新条目——上下文的规格内容来自数据库最新版本，不是代码里的常量。

## 七、证据清单

| 验收点 | 文件 |
| --- | --- |
| 命令、退出码、执行顺序 | `records/reproduction/commands.jsonl` |
| 修改前/后测试日志与元数据 | `records/reproduction/tests/{before,after}_{target,regression}.log` 与同名 `.json` |
| 上下文 v1/v2 | `records/reproduction/context/context_v1.json`、`context_v2.json` |
| 数据库真实查询 | `records/reproduction/database/query_log.jsonl` |
| PowerContext 写入/读取 | `records/reproduction/powercontext/{save,resume,state,operations}*` |
| MiniMax 调用全过程 | `records/minimax/actual-run/`（`request.json`、`response.json`、`visible_output.txt`、`parsed_changes.json`、`applied.diff`、`run_metadata.json`） |
| 总览 | `records/reproduction/verification_summary.json` |

## 八、重置方法

- `verify.py` 每次运行都会先删除 `.runtime/<name>.db` 与对应 `records/` 目录，并把业务代码恢复为 `verification/snapshots/before/src/ticket_service.py`，因此可直接重跑，不需要手工清理。
- 手工重置：

```bash
rm -rf .runtime/reproduction.db records/reproduction
cp verification/snapshots/before/src/ticket_service.py sample_project/src/ticket_service.py
```

- `.runtime/`、`.venv/`、`.env` 均不入库；`records/minimax/actual-run/` 是重放输入，**不要**删除，否则 `--mode replay` 会明确报错（而不是给出假结果）。
