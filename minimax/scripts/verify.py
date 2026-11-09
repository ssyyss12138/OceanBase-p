import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sdd_context.util import append_jsonl, read_json, write_json


class Verification:
    def __init__(self, mode, replay_source, records_dir="records/reproduction"):
        self.root = Path(__file__).resolve().parents[1]
        self.mode = mode
        replay_path = Path(replay_source)
        self.replay_source = replay_path if replay_path.is_absolute() else (self.root / replay_path).resolve()
        records_dir = Path(records_dir)
        self.db = self.root / ".runtime" / (records_dir.name + ".db")
        self.records = self.root / records_dir
        self.python = sys.executable
        self.task = "TASK-TICKET-STATUS"

    def run(self):
        self._clean()
        shutil.copyfile(
            self.root / "verification/snapshots/before/src/ticket_service.py",
            self.root / "sample_project/src/ticket_service.py",
        )
        self.cli(["import", "--project", "sample_project", "--spec",
                  "sample_project/specs/SPEC-TICKET-001.v1.json"], stdout="database/import.json")
        context_path = self.records / "context/context_v1.json"
        self.cli(["context", "--task", self.task, "--output", str(context_path)], stdout="context/stdout_v1.json")
        context = read_json(context_path)
        self._check_context(context, 1)
        assert context["prior_state"] is None
        before_target = self.tests("target", "before_target", 1, "4 failed")
        before_regression = self.tests("regression", "before_regression", 0, "8 passed")
        self.save_state("in_progress", before_target, before_regression, None)
        if self.mode == "online":
            model_record = self.root / "records/minimax/actual-run"
            self.cli(["modify", "--project", "sample_project", "--task", self.task,
                      "--record-dir", str(model_record)], stdout="minimax/apply_result.json")
        else:
            if not self.replay_source.is_dir():
                raise FileNotFoundError(
                    f"replay source not found: {self.replay_source}; run --mode online once to record it"
                )
            model_record = self.records / "minimax/replayed-run"
            shutil.copytree(self.replay_source, model_record)
            write_json(model_record / "replay_provenance.json",
                       {"mode": "recorded-replay",
                        "source": str(self.replay_source.relative_to(self.root))
                        if self.replay_source.is_relative_to(self.root) else str(self.replay_source),
                        "new_minimax_call": False})
            self.cli(["apply-recorded", "--project", "sample_project", "--task", self.task,
                      "--record-dir", str(model_record)], stdout="minimax/apply_result.json")
        assert (model_record / "applied.diff").stat().st_size > 0
        after_target = self.tests("target", "after_target", 0, "4 passed")
        after_regression = self.tests("regression", "after_regression", 0, "8 passed")
        for name in ("tests/test_ticket_status_filter.py", "tests/test_ticket_regression.py"):
            assert before_target["source_sha256"][name] == after_target["source_sha256"][name]
        assert (before_target["source_sha256"]["src/ticket_service.py"]
                != after_target["source_sha256"]["src/ticket_service.py"])
        completed = self.save_state("completed", before_target, before_regression,
                                    {"after_target": after_target, "after_regression": after_regression})
        resume_path = self.records / "powercontext/resume_completed.json"
        self.cli(["resume", "--task", self.task, "--output", str(resume_path)], stdout="powercontext/resume_stdout.json")
        resumed = read_json(resume_path)
        assert resumed["resumed"] is True
        assert resumed["state"]["revision"] == completed["revision"] == 2
        assert resumed["state"]["content"]["status"] == "completed"
        assert resumed["reused_context"]["prior_state"]["content"]["todo"] == []
        self.cli(["import-spec", "--spec", "sample_project/specs/SPEC-TICKET-001.v2.json"],
                 stdout="database/spec_update.json")
        v2_path = self.records / "context/context_v2.json"
        self.cli(["context", "--task", self.task, "--output", str(v2_path)], stdout="context/stdout_v2.json")
        v2 = read_json(v2_path)
        self._check_context(v2, 2)
        requirement_ids = [item["id"] for item in v2["specifications"][0]["content"]["requirements"]]
        assert "REQ-FILTER-004" in requirement_ids
        assert v2["prior_state"]["content"]["status"] == "completed"
        summary = {
            "mode": self.mode,
            "task_id": self.task,
            "before": {"target_exit": before_target["exit_code"], "regression_exit": before_regression["exit_code"]},
            "after": {"target_exit": after_target["exit_code"], "regression_exit": after_regression["exit_code"]},
            "tests_unchanged": True,
            "powercontext_revision": completed["revision"],
            "state_reused": True,
            "spec_version_after_update": 2,
            "database_context_verified": True,
            "minimax_record": str(model_record.relative_to(self.root)),
            "git_commit": after_target["git_commit"],
        }
        write_json(self.records / "verification_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    def _clean(self):
        if self.db.exists():
            shutil.rmtree(self.db)
        if self.records.exists():
            shutil.rmtree(self.records)
        self.records.mkdir(parents=True)
        if self.mode == "online":
            online_record = self.root / "records/minimax/actual-run"
            if online_record.exists():
                shutil.rmtree(online_record)
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, set(sorted(os.sched_getaffinity(0))[:2]))

    def cli(self, arguments, expected=0, stdout=None):
        command = [self.python, "-m", "sdd_context.cli", "--db", str(self.db),
                   "--records", str(self.records), *arguments]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "src")
        result = subprocess.run(command, cwd=self.root, env=env, text=True, capture_output=True)
        append_jsonl(self.records / "commands.jsonl", {
            "command": command, "cwd": str(self.root), "exit_code": result.returncode,
            "expected_exit": expected,
        })
        if stdout:
            destination = self.records / stdout
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode != expected:
            raise RuntimeError(f"command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}")
        return result

    def tests(self, suite, phase, expected_exit, expected_summary):
        log = self.records / f"tests/{phase}.log"
        self.cli(["run-tests", "--project", "sample_project", "--suite", suite, "--log", str(log)],
                 expected=expected_exit, stdout=f"tests/{phase}_command.json")
        metadata = read_json(log.with_suffix(".json"))
        output = log.read_text(encoding="utf-8")
        assert expected_summary in output, output
        assert "ERROR at" not in output, output
        return metadata

    def save_state(self, status, before_target, before_regression, after):
        completed = ["导入规格、代码和测试", "构造数据库上下文", "执行修改前测试"]
        todo = ["调用 MiniMax 修改代码", "执行修改后目标与回归测试"]
        validation = {
            "before_target": {"exit_code": before_target["exit_code"], "result": "4 failed"},
            "before_regression": {"exit_code": before_regression["exit_code"], "result": "8 passed"},
        }
        code_hash = before_target["source_sha256"]["src/ticket_service.py"]
        if status == "completed":
            completed += ["MiniMax 代码修改", "修改后目标与回归测试"]
            todo = []
            code_hash = after["after_target"]["source_sha256"]["src/ticket_service.py"]
            validation["after_target"] = {"exit_code": after["after_target"]["exit_code"], "result": "4 passed"}
            validation["after_regression"] = {
                "exit_code": after["after_regression"]["exit_code"], "result": "8 passed",
            }
        state = {
            "task_id": self.task, "status": status, "completed": completed, "todo": todo,
            "validation": validation, "code_sha256": code_hash, "spec_version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        state_path = self.records / f"powercontext/state_{status}.json"
        write_json(state_path, state)
        result_path = self.records / f"powercontext/save_{status}.json"
        self.cli(["save-state", "--task", self.task, "--state-file", str(state_path)],
                 stdout=f"powercontext/save_{status}.json")
        return read_json(result_path)

    def _check_context(self, context, version):
        assert [(item["spec_id"], item["version"]) for item in context["specifications"]] == [("SPEC-TICKET-001", version)]
        code = {item["file_path"]: item for item in context["code"]}
        assert "TicketService.list_tickets" in code["src/ticket_service.py"]["symbols"]
        tests = {item["file_path"]: item["test_type"] for item in context["tests"]}
        assert tests["tests/test_ticket_status_filter.py"] == "target"
        assert tests["tests/test_ticket_regression.py"] == "regression"
        assert all(item["source"].startswith("seekdb.") for item in context["specifications"] + context["code"] + context["tests"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("online", "replay"), default="replay")
    parser.add_argument("--replay-source", default="records/minimax/actual-run")
    arguments = parser.parse_args()
    Verification(arguments.mode, arguments.replay_source).run()
