import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .util import read_json, sha256_text, utc_now, write_json


def run_tests(project_dir, suite, log_path):
    project_dir = Path(project_dir).resolve()
    log_path = Path(log_path).resolve()
    task = read_json(project_dir / "task.json")
    files = {
        "target": task["target_tests"],
        "regression": task["regression_tests"],
        "all": task["target_tests"] + task["regression_tests"],
    }[suite]
    command = [sys.executable, "-m", "pytest", *files, "-q"]
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(command, cwd=project_dir, env=env, text=True, capture_output=True)
    hashes = {
        path: sha256_text((project_dir / path).read_text(encoding="utf-8"))
        for path in task["code_files"] + task["target_tests"] + task["regression_tests"]
    }
    repo_root = Path(__file__).resolve().parents[3]
    git_commit = subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True).strip()
    metadata = {
        "timestamp": utc_now(), "command": command, "command_display": shlex.join(command),
        "cwd": str(project_dir), "exit_code": result.returncode, "suite": suite,
        "git_commit": git_commit, "source_sha256": hashes,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"command: {metadata['command_display']}\nexit_code: {result.returncode}\n"
        f"git_commit: {git_commit}\nsource_sha256: {json.dumps(hashes, ensure_ascii=False)}\n\n"
        + result.stdout + result.stderr,
        encoding="utf-8",
    )
    write_json(log_path.with_suffix(".json"), metadata)
    return result.returncode, metadata
