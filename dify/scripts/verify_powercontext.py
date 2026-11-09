"""Integration check against a running, real PowerContext Server; no LLM/Dify claims."""

import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.pc import PowerContext
from backend.service import Game
from backend.store import Store

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
url = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8100")
secret = os.environ["PLAYER_HASH_SECRET"]
pc = PowerContext(url, os.environ.get("POWERCONTEXT_TOKEN", ""))
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "game.sqlite"
    game = Game(Store(path), pc, secret)
    a, b = "verify-a-" + uuid.uuid4().hex, "verify-b-" + uuid.uuid4().hex
    tid = str(uuid.uuid4())
    first = game.prepare(a, tid, "买一瓶勇气汽水")
    result = game.commit(a, tid, "buy_courage")
    assert result["state"]["joke_debt"] == 1
    assert result["memory_refs"]
    assert game.commit(a, tid, "buy_courage") == result
    row = game.store.turn(tid)
    stored_record = json.loads(row["memory"])["records"][0]
    scope_id = game.store.player(game.identity(a))["scope_id"]
    reconciled = pc.remember(scope_id, tid, stored_record["text"])
    assert reconciled["changed"] is False
    assert reconciled["records"][0]["artifact"] == stored_record["artifact"]
    marker = "蒸汽章鱼把月亮煮成了火锅-" + uuid.uuid4().hex[:10]
    joke = str(uuid.uuid4())
    game.prepare(a, joke, "讲笑话：" + marker)
    paid = game.commit(a, joke, "tell_joke")
    assert paid["state"]["joke_debt"] == 0
    restarted = Game(Store(path), pc, secret)
    recalled = restarted.prepare(a, str(uuid.uuid4()), "我刚刚讲了什么笑话，蒸汽章鱼呢？")
    other = restarted.prepare(b, str(uuid.uuid4()), "蒸汽章鱼讲过什么？")
    assert marker in recalled["history"], recalled
    assert marker not in other["history"]
    end = str(uuid.uuid4())
    restarted.prepare(a, end, "结算离店")
    ending = restarted.commit(a, end, "settle")
    assert ending["state"]["ending"] == "守约合伙人"
    record = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "kind": "real-powercontext-http-integration",
        "runtime_commit": "c4c95e465775b5b87ccac796194e01086bd7cc8f",
        "checks": [
            "real-scope-create",
            "real-source-capture",
            "real-memory-write",
            "real-context-prepare",
            "business-store-reopen",
            "cross-session-recall",
            "player-isolation",
            "idempotent-commit",
            "real-uncertain-memory-write-reconciliation",
            "ending",
        ],
        "marker": marker,
        "first_memory_refs": result["memory_refs"],
        "recall_content": recalled["history"],
        "other_player_content": other["history"],
        "ending": ending["state"]["ending"],
        "limitations": "This script does not execute Dify, call Qwen, or restart the PowerContext process.",
    }
    Path(args.output).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print("PASS: real PowerContext write/read, isolation, replay, store reopen and ending; evidence saved")
pc.client.close()
