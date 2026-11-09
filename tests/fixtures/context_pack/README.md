# 本地增量传输诊断数据

会议正文来自 Yale-LILY/QMSum，固定 commit 为
`83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`，文件为
`data/ALL/test/ES2004a.json`。上游数据采用 MIT 许可，见同目录 `LICENSE`。

`qmsum-wire-sequence.json` 按原始顺序选取该会议所有正文含 remote 的九个
turn，保留完整正文，并以原始 speaker 加 `: ` 作为前缀。
原始 turn 索引记录在 `original_turn_indices`。

本地测量条件为：最初采集前八个 turn；共 40 轮；第 11 轮追加第九个 turn；
第 1–20 轮查询 remote，第 21–30 轮查询 controls，第 31–40 轮查询 programme；
每轮预算为 8000 bytes。追加时间和查询安排由本地诊断定义。

这是自建诊断序列，不是组织方的完整验收数据，也不代表隐藏序列通过。
这些数据只供测试脚本读取，产品 `src/` 不读取该文件。

从仓库根目录运行：

```powershell
$env:PYTHONPATH = "$PWD/src"
.venv/Scripts/python.exe scripts/measure_context_pack.py tests/fixtures/context_pack/qmsum-wire-sequence.json --output .artifacts/cost.json
```

脚本对首次 pack 和后续每个 delta 调用 `to_bytes()`，以实际长度计费。
接收端序列化往返后重建，并逐轮核对普通 prepare 的正文和完整目标 pack。
另报告完整 v1 pack 和逐轮独立 gzip level 9、mtime 0 对照。
