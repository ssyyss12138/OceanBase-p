import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .database import SeekdbDatabase
from .minimax import apply_changes, call_minimax, load_recorded_changes
from .state import TaskStateRepository
from .test_runner import run_tests
from .util import read_json, write_json


def main(argv=None):
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "run-tests":
        code, metadata = run_tests(args.project, args.suite, args.log)
        print(json.dumps(metadata, ensure_ascii=False))
        return code
    try:
        with SeekdbDatabase(args.db, args.records) as database:
            result = _execute(args, database)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


def _execute(args, database):
    if args.command == "import":
        return database.import_task(args.project, args.spec)
    if args.command == "import-spec":
        spec = database.import_spec(args.spec)
        return {"task_id": spec["task_id"], "spec_id": spec["spec_id"], "version": spec["version"]}
    if args.command == "context":
        state = TaskStateRepository(database).load(args.task)
        context = database.build_context(args.task, prior_state=state)
        if args.output:
            write_json(args.output, context)
        return context
    if args.command == "resume":
        state = TaskStateRepository(database).load(args.task)
        if state is None:
            raise ValueError(f"no saved PowerContext state for {args.task}")
        result = {"resumed": True, "state": state, "reused_context": database.build_context(args.task, prior_state=state)}
        if args.output:
            write_json(args.output, result)
        return result
    if args.command == "save-state":
        state = read_json(args.state_file)
        if state["task_id"] != args.task:
            raise ValueError("state task_id does not match command task")
        return TaskStateRepository(database).save(args.task, state)
    if args.command in {"modify", "apply-recorded"}:
        state = TaskStateRepository(database).load(args.task)
        context = database.build_context(args.task, prior_state=state)
        if args.command == "modify":
            changes = call_minimax(context, args.record_dir)
        else:
            changes = load_recorded_changes(args.record_dir)
        result = apply_changes(context, changes, args.project, args.record_dir)
        database.refresh_code(args.project)
        result["mode"] = "online-minimax" if args.command == "modify" else "recorded-replay"
        return result
    raise ValueError(f"unsupported command: {args.command}")


def _parser():
    parser = argparse.ArgumentParser(prog="sdd-context")
    parser.add_argument("--db", default=".runtime/seekdb")
    parser.add_argument("--records", default="records")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("import")
    command.add_argument("--project", default="sample_project")
    command.add_argument("--spec", required=True)
    command = commands.add_parser("import-spec")
    command.add_argument("--spec", required=True)
    for name in ("context", "resume"):
        command = commands.add_parser(name)
        command.add_argument("--task", default="TASK-TICKET-STATUS")
        command.add_argument("--output")
    command = commands.add_parser("save-state")
    command.add_argument("--task", default="TASK-TICKET-STATUS")
    command.add_argument("--state-file", required=True)
    for name in ("modify", "apply-recorded"):
        command = commands.add_parser(name)
        command.add_argument("--project", default="sample_project")
        command.add_argument("--task", default="TASK-TICKET-STATUS")
        command.add_argument("--record-dir", required=True)
    command = commands.add_parser("run-tests")
    command.add_argument("--project", default="sample_project")
    command.add_argument("--suite", choices=("target", "regression", "all"), required=True)
    command.add_argument("--log", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
