import ast
import json
import os
from pathlib import Path

import pylibseekdb
import pymysql

from .util import append_jsonl, read_json, sha256_text, utc_now


class SeekdbDatabase:
    def __init__(self, db_dir, records_dir):
        self.db_dir = Path(db_dir).resolve()
        self.records_dir = Path(records_dir).resolve()
        self.instance = None
        self.connection = None

    def __enter__(self):
        if hasattr(os, "sched_setaffinity"):
            cpus = sorted(os.sched_getaffinity(0))
            os.sched_setaffinity(0, set(cpus[:2]))
        self.db_dir.parent.mkdir(parents=True, exist_ok=True)
        self.instance = pylibseekdb.open(db_dir=str(self.db_dir))
        options = self.instance.connection_options()
        self.connection = pymysql.connect(
            user=options["user"], unix_socket=options["unix_socket"],
            database="test", autocommit=True, charset="utf8mb4",
        )
        self._create_schema()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.connection is not None:
            self.connection.close()
        if self.instance is not None:
            self.instance.close()

    def execute(self, sql, params=()):
        record = {"timestamp": utc_now(), "sql": sql, "params": list(params)}
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall() if cursor.description else None
                record["row_count"] = cursor.rowcount
                if rows is not None:
                    record["rows"] = rows
        except Exception as error:
            record["error"] = str(error)
            append_jsonl(self.records_dir / "database/query_log.jsonl", record)
            raise
        append_jsonl(self.records_dir / "database/query_log.jsonl", record)
        return rows

    def _create_schema(self):
        statements = [
            """CREATE TABLE IF NOT EXISTS tasks (
                task_id VARCHAR(128) PRIMARY KEY, title TEXT,
                task_json LONGTEXT NOT NULL, imported_at VARCHAR(64) NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS specifications (
                task_id VARCHAR(128) NOT NULL, spec_id VARCHAR(128) NOT NULL, version INT NOT NULL,
                title TEXT, content_json LONGTEXT NOT NULL, sha256 VARCHAR(64) NOT NULL,
                imported_at VARCHAR(64) NOT NULL, PRIMARY KEY (task_id, spec_id, version))""",
            """CREATE TABLE IF NOT EXISTS code_files (
                task_id VARCHAR(128) NOT NULL, file_path VARCHAR(512) NOT NULL,
                content LONGTEXT NOT NULL, symbols_json LONGTEXT NOT NULL,
                sha256 VARCHAR(64) NOT NULL, imported_at VARCHAR(64) NOT NULL,
                PRIMARY KEY (task_id, file_path))""",
            """CREATE TABLE IF NOT EXISTS test_files (
                task_id VARCHAR(128) NOT NULL, file_path VARCHAR(512) NOT NULL,
                test_type VARCHAR(32) NOT NULL, test_names_json LONGTEXT NOT NULL,
                content LONGTEXT NOT NULL, sha256 VARCHAR(64) NOT NULL,
                imported_at VARCHAR(64) NOT NULL, PRIMARY KEY (task_id, file_path))""",
            """CREATE TABLE IF NOT EXISTS pc_artifact_revisions (
                family VARCHAR(128) NOT NULL, artifact_id VARCHAR(256) NOT NULL, revision INT NOT NULL,
                content_json LONGTEXT NOT NULL, lineage_json LONGTEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL, PRIMARY KEY (family, artifact_id, revision))""",
        ]
        for statement in statements:
            self.execute(statement)

    def import_task(self, project_dir, spec_path):
        project_dir = Path(project_dir).resolve()
        task = read_json(project_dir / "task.json")
        task_id = task["task_id"]
        self.execute(
            """INSERT INTO tasks (task_id, title, task_json, imported_at) VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE title=VALUES(title), task_json=VALUES(task_json), imported_at=VALUES(imported_at)""",
            (task_id, task["title"], json.dumps(task, ensure_ascii=False), utc_now()),
        )
        spec = self.import_spec(spec_path, require_task=False)
        if spec["task_id"] != task_id or spec["spec_id"] not in task["spec_ids"]:
            raise ValueError("specification does not belong to the task")
        self.refresh_code(project_dir)
        for relative_path in task["test_files"]:
            content = (project_dir / relative_path).read_text(encoding="utf-8")
            if relative_path in task["target_tests"]:
                test_type = "target"
            elif relative_path in task["regression_tests"]:
                test_type = "regression"
            else:
                test_type = "support"
            self.execute(
                """INSERT INTO test_files
                    (task_id, file_path, test_type, test_names_json, content, sha256, imported_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE test_type=VALUES(test_type), test_names_json=VALUES(test_names_json),
                    content=VALUES(content), sha256=VALUES(sha256), imported_at=VALUES(imported_at)""",
                (task_id, relative_path, test_type, json.dumps(_test_names(content)), content, sha256_text(content), utc_now()),
            )
        return {"task_id": task_id, "spec_version": spec["version"],
                "code_files": len(task["code_files"]), "test_files": len(task["test_files"])}

    def refresh_code(self, project_dir):
        project_dir = Path(project_dir).resolve()
        task = read_json(project_dir / "task.json")
        for relative_path in task["code_files"]:
            content = (project_dir / relative_path).read_text(encoding="utf-8")
            self.execute(
                """INSERT INTO code_files (task_id, file_path, content, symbols_json, sha256, imported_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE content=VALUES(content), symbols_json=VALUES(symbols_json),
                    sha256=VALUES(sha256), imported_at=VALUES(imported_at)""",
                (task["task_id"], relative_path, content, json.dumps(_symbols(content)),
                 sha256_text(content), utc_now()),
            )

    def import_spec(self, spec_path, require_task=True):
        spec_path = Path(spec_path).resolve()
        raw = spec_path.read_text(encoding="utf-8")
        spec = json.loads(raw)
        if require_task:
            rows = self.execute("SELECT task_id FROM tasks WHERE task_id = %s", (spec["task_id"],))
            if not rows:
                raise ValueError(f"task {spec['task_id']} must be imported before specification updates")
        self.execute(
            """INSERT INTO specifications
                (task_id, spec_id, version, title, content_json, sha256, imported_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE title=VALUES(title), content_json=VALUES(content_json),
                sha256=VALUES(sha256), imported_at=VALUES(imported_at)""",
            (spec["task_id"], spec["spec_id"], spec["version"], spec["title"],
             json.dumps(spec, ensure_ascii=False), sha256_text(raw), utc_now()),
        )
        return spec

    def build_context(self, task_id, prior_state=None):
        task_rows = self.execute("SELECT task_json FROM tasks WHERE task_id = %s", (task_id,))
        if not task_rows:
            raise ValueError(f"unknown task: {task_id}")
        task = json.loads(task_rows[0][0])
        spec_rows = self.execute(
            """SELECT s.spec_id, s.version, s.title, s.content_json, s.sha256
            FROM specifications s JOIN (
                SELECT spec_id, MAX(version) AS version FROM specifications WHERE task_id=%s GROUP BY spec_id
            ) latest ON s.spec_id=latest.spec_id AND s.version=latest.version
            WHERE s.task_id=%s ORDER BY s.spec_id""",
            (task_id, task_id),
        )
        code_rows = self.execute(
            "SELECT file_path, symbols_json, sha256, content FROM code_files WHERE task_id=%s ORDER BY file_path",
            (task_id,),
        )
        test_rows = self.execute(
            """SELECT file_path, test_type, test_names_json, sha256, content
            FROM test_files WHERE task_id=%s ORDER BY file_path""",
            (task_id,),
        )
        specifications = [
            {"spec_id": row[0], "version": row[1], "title": row[2], "content": json.loads(row[3]),
             "sha256": row[4], "source": f"seekdb.specifications/{row[0]}/v{row[1]}"}
            for row in spec_rows
        ]
        if {item["spec_id"] for item in specifications} != set(task["spec_ids"]):
            raise ValueError("not all required specifications are available")
        return {
            "task": task,
            "specifications": specifications,
            "code": [
                {"file_path": row[0], "symbols": json.loads(row[1]), "sha256": row[2], "content": row[3],
                 "source": f"seekdb.code_files/{row[0]}"} for row in code_rows
            ],
            "tests": [
                {"file_path": row[0], "test_type": row[1], "test_names": json.loads(row[2]),
                 "sha256": row[3], "content": row[4], "source": f"seekdb.test_files/{row[0]}"}
                for row in test_rows
            ],
            "prior_state": prior_state,
            "provenance": {"engine": "pylibseekdb", "database": str(self.db_dir), "generated_at": utc_now()},
        }


def _symbols(content):
    tree = ast.parse(content)
    symbols = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(node.name)
            symbols.extend(f"{node.name}.{child.name}" for child in node.body
                           if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
    return symbols


def _test_names(content):
    return [node.name for node in ast.parse(content).body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
