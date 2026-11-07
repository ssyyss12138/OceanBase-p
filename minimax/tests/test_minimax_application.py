import json

import pytest

from sdd_context.minimax import _parse_changes, apply_changes, build_messages


def test_parse_json_visible_output():
    payload = {"summary": "x", "changes": [{"file_path": "a.py", "content": "pass\n"}]}
    text = "```json\n" + json.dumps(payload) + "\n```"
    value = _parse_changes(text)
    assert value["changes"][0]["file_path"] == "a.py"


def test_parse_strips_reasoning_block_before_json():
    payload = {"summary": "x", "changes": [{"file_path": "a.py", "content": "pass\n"}]}
    text = "<think>thinking with { braces } and \"quotes\"</think>\n" + json.dumps(payload)
    value = _parse_changes(text)
    assert value["changes"][0]["file_path"] == "a.py"


def test_apply_changes_writes_only_allowed_file(tmp_path):
    (tmp_path / "a.py").write_text("before\n")
    context = {"task": {"allowed_write_paths": ["a.py"]}, "code": [{"file_path": "a.py"}]}
    changes = {"summary": "x", "changes": [{"file_path": "a.py", "content": "after\n"}]}
    apply_changes(context, changes, tmp_path, tmp_path / "record")
    assert (tmp_path / "a.py").read_text() == "after\n"
    assert "-before" in (tmp_path / "record/applied.diff").read_text()
    assert "+after" in (tmp_path / "record/applied.diff").read_text()


def test_apply_changes_rejects_unlisted_file(tmp_path):
    context = {"task": {"allowed_write_paths": ["a.py"]}, "code": [{"file_path": "a.py"}]}
    changes = {"changes": [{"file_path": "../secret", "content": "bad"}]}
    with pytest.raises(ValueError, match="disallowed"):
        apply_changes(context, changes, tmp_path, tmp_path / "record")


def test_messages_contain_real_context_sources():
    context = {"task": {"task_id": "T"}, "specifications": [{"source": "seekdb.specifications/S/v1"}]}
    messages = build_messages(context)
    assert "seekdb.specifications/S/v1" in messages[1]["content"]
    assert json.loads(messages[1]["content"].split("\n\n", 1)[1])["task"]["task_id"] == "T"
