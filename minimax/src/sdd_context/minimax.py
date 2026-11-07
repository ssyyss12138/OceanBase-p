import ast
import difflib
import json
import os
import re
from pathlib import Path

import httpx

from .util import read_json, sha256_text, utc_now, write_json


def build_messages(context):
    system = (
        "你是规格驱动开发助手。只能修改任务允许的文件，必须满足给定规格并保持兼容行为。"
        "只输出一个 JSON 对象，不要 Markdown、解释或隐藏推理。"
        "格式为 {\"summary\":\"简短说明\",\"changes\":[{\"file_path\":\"相对路径\",\"content\":\"完整文件内容\"}]}。"
    )
    user = (
        "请完成以下真实代码修改。上下文中的规格、代码和测试均来自 seekdb 查询。"
        "优先保持原有默认行为，输出修改后的完整文件。\n\n"
        + json.dumps(context, ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_minimax(context, record_dir):
    record_dir = Path(record_dir)
    if record_dir.exists() and any(record_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite MiniMax record directory: {record_dir}")
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is required for an online modification")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M2")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
    payload = {"model": model, "messages": build_messages(context), "temperature": 0,
               "max_tokens": int(os.environ.get("MINIMAX_MAX_TOKENS", "8192"))}
    write_json(record_dir / "request.json", {"timestamp": utc_now(), "base_url": base_url, "payload": payload})
    with httpx.Client(timeout=float(os.environ.get("MINIMAX_TIMEOUT", "180"))) as client:
        response = client.post(base_url, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
    write_json(record_dir / "http_status.json", {"status_code": response.status_code})
    response.raise_for_status()
    response_data = response.json()
    write_json(record_dir / "response.json", _redact_reasoning(response_data))
    choice = response_data["choices"][0]
    content = choice["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content)
    (record_dir / "visible_output.txt").write_text(content, encoding="utf-8")
    if choice.get("finish_reason") == "length":
        raise ValueError("MiniMax output was truncated by max_tokens; raise MINIMAX_MAX_TOKENS")
    changes = _parse_changes(content)
    write_json(record_dir / "parsed_changes.json", changes)
    write_json(
        record_dir / "run_metadata.json",
        {"timestamp": utc_now(), "provider": "MiniMax", "model": model,
         "usage": response_data.get("usage"), "task_id": context["task"]["task_id"],
         "content_has_reasoning_block": bool(_THINK_BLOCK.search(content))},
    )
    return changes


def load_recorded_changes(record_dir):
    return read_json(Path(record_dir) / "parsed_changes.json")


def apply_changes(context, changes, project_dir, record_dir):
    project_dir = Path(project_dir).resolve()
    record_dir = Path(record_dir)
    record_dir.mkdir(parents=True, exist_ok=True)
    allowed = set(context["task"]["allowed_write_paths"])
    context_paths = {item["file_path"] for item in context["code"]}
    diffs = []
    applied = []
    for change in changes["changes"]:
        relative_path = change["file_path"]
        if relative_path not in allowed or relative_path not in context_paths:
            raise ValueError(f"MiniMax attempted to modify a disallowed file: {relative_path}")
        target = project_dir / relative_path
        before = target.read_text(encoding="utf-8")
        after = change["content"]
        if not isinstance(after, str):
            raise TypeError("modified file content must be a string")
        if target.suffix == ".py":
            ast.parse(after)
        target.write_text(after, encoding="utf-8")
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{relative_path}", tofile=f"b/{relative_path}",
        ))
        diffs.append(diff)
        applied.append({"file_path": relative_path, "before_sha256": sha256_text(before), "after_sha256": sha256_text(after)})
    (record_dir / "applied.diff").write_text("\n".join(diffs), encoding="utf-8")
    write_json(record_dir / "applied_files.json", applied)
    return {"summary": changes.get("summary", ""), "files": applied, "diff": str(record_dir / "applied.diff")}


_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


def _strip_reasoning(content):
    """MiniMax-M3 会在 content 前置 <think>…</think> 推理块；解析前剔除，保留其后的 JSON。"""
    cleaned = _THINK_BLOCK.sub("", content)
    dangling = _OPEN_THINK.search(cleaned)
    if dangling:
        cleaned = cleaned[: dangling.start()]
    return cleaned


def _parse_changes(content):
    stripped = _strip_reasoning(content).strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        if stripped.startswith("json"):
            stripped = stripped[4:].lstrip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("MiniMax visible output did not contain JSON")
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value.get("changes"), list) or not value["changes"]:
        raise ValueError("MiniMax output must contain a non-empty changes list")
    return value


def _redact_reasoning(value):
    if isinstance(value, dict):
        return {key: _redact_reasoning(item) for key, item in value.items() if key not in {"reasoning", "reasoning_content"}}
    if isinstance(value, list):
        return [_redact_reasoning(item) for item in value]
    return value
