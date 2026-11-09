import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")

from verify import Verification  # noqa: E402

# 预检写入独立 records/preflight 与 .runtime/preflight.db，
# 不覆盖真实验收证据 records/reproduction（其中包含 online 运行记录）。
v = Verification("replay", "records/minimax/actual-run", records_dir="records/preflight")
v._clean()
shutil.copyfile(
    v.root / "verification/snapshots/before/src/ticket_service.py",
    v.root / "sample_project/src/ticket_service.py",
)
v.cli(
    ["import", "--project", "sample_project", "--spec", "sample_project/specs/SPEC-TICKET-001.v1.json"],
    stdout="database/import.json",
)
context_path = v.records / "context/context_v1.json"
v.cli(["context", "--task", v.task, "--output", str(context_path)], stdout="context/stdout_v1.json")
context = json.loads(context_path.read_text(encoding="utf-8"))
v._check_context(context, 1)
assert context["prior_state"] is None

before_target = v.tests("target", "before_target", 1, "4 failed")
before_regression = v.tests("regression", "before_regression", 0, "8 passed")
v.save_state("in_progress", before_target, before_regression, None)

resume_path = v.records / "powercontext/resume_in_progress.json"
v.cli(["resume", "--task", v.task, "--output", str(resume_path)], stdout="powercontext/resume_stdout.json")
resumed = json.loads(resume_path.read_text(encoding="utf-8"))
assert resumed["resumed"] is True
assert resumed["state"]["revision"] == 1
assert resumed["state"]["content"]["status"] == "in_progress"

v.cli(["import-spec", "--spec", "sample_project/specs/SPEC-TICKET-001.v2.json"], stdout="database/spec_update.json")
v2_path = v.records / "context/context_v2.json"
v.cli(["context", "--task", v.task, "--output", str(v2_path)], stdout="context/stdout_v2.json")
v2 = json.loads(v2_path.read_text(encoding="utf-8"))
v._check_context(v2, 2)
req_ids = [item["id"] for item in v2["specifications"][0]["content"]["requirements"]]
assert "REQ-FILTER-004" in req_ids
assert v2["prior_state"]["content"]["status"] == "in_progress"

print("PREFLIGHT OK: non-MiniMax flow verified (import, context, before tests, save/resume state, spec v2 update)")
