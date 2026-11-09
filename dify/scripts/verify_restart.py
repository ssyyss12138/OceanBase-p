"""Two-stage real HTTP restart check. Restart the two real processes between stages."""

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx

p = argparse.ArgumentParser()
p.add_argument("stage", choices=["before", "after"])
p.add_argument("--state", required=True, help="Private state file outside the repository")
p.add_argument("--output", required=True)
a = p.parse_args()
state_path = Path(a.state)
c = httpx.Client(
    base_url=os.environ.get("GAME_URL", "http://127.0.0.1:8090"),
    headers={"Authorization": "Bearer " + os.environ["GAME_API_KEY"]},
    timeout=60,
    trust_env=False,
)


def prepare(code, query):
    r = c.post("/v1/turns/prepare", json={"player_code": code, "turn_id": str(uuid.uuid4()), "query": query})
    r.raise_for_status()
    return r.json()


if a.stage == "before":
    code = "restart-" + uuid.uuid4().hex
    marker = "打着领带的章鱼开火锅店-" + uuid.uuid4().hex[:10]
    turn = prepare(code, "讲笑话：" + marker)
    r = c.post(
        "/v1/turns/commit", json={"player_code": code, "turn_id": turn["turn_id"], "action": "tell_joke"}
    )
    r.raise_for_status()
    saved = {
        "player_code": code,
        "marker": marker,
        "turn_id": turn["turn_id"],
        "memory_refs": r.json()["memory_refs"],
        "before": datetime.now(timezone.utc).isoformat(),
    }
    state_path.write_text(json.dumps(saved, ensure_ascii=False))
    state_path.chmod(0o600)
    print("Before stage: actual HTTP write confirmed. Now restart game and PowerContext.")
else:
    saved = json.loads(state_path.read_text())
    recalled = prepare(saved["player_code"], "我讲过什么笑话，章鱼呢？")
    assert saved["marker"] in recalled["history"]
    assert recalled["state"]["version"] == 1
    record = {
        "kind": "real-game-and-powercontext-process-restart",
        "before": saved["before"],
        "after": datetime.now(timezone.utc).isoformat(),
        "marker": saved["marker"],
        "memory_refs": saved["memory_refs"],
        "recall_content": recalled["history"],
        "state": recalled["state"],
    }
    Path(a.output).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print("PASS: game business state and actual PowerContext memory survived process restart")
c.close()
