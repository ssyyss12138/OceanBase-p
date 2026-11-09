"""Real Dify service API + online LLM + PowerContext. No mock evidence."""

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx

p = argparse.ArgumentParser()
p.add_argument("--private-config", required=True)
p.add_argument("--output", required=True)
a = p.parse_args()
cfg = json.loads(Path(a.private_config).read_text())
c = httpx.Client(
    base_url=cfg["base_url"],
    headers={"Authorization": "Bearer " + cfg["service_api_key"]},
    timeout=240,
    trust_env=False,
)
transcript = []


def play(code, query, conversation=None):
    payload = {
        "inputs": {"player_code": code},
        "query": query,
        "response_mode": "blocking",
        "user": "acceptance-" + code,
    }
    if conversation:
        payload["conversation_id"] = conversation
    start = time.monotonic()
    r = c.post("/v1/chat-messages", json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"Dify chat failed: HTTP {r.status_code}")
    data = r.json()
    answer = data.get("answer", "")
    if "✅ 本轮真实互动已存档。" not in answer:
        raise RuntimeError("Round not confirmed. Inspect real Dify node logs and model configuration.")
    if "老板的对讲机暂时失声" in answer:
        raise RuntimeError("Narration fell back to the receipt; this is not a full real-model pass")
    transcript.append(
        {
            "query": query,
            "answer": answer,
            "new_conversation": not bool(conversation),
            "conversation_id": data.get("conversation_id"),
            "message_id": data.get("message_id"),
            "workflow_run_id": data.get("workflow_run_id"),
            "seconds": round(time.monotonic() - start, 3),
        }
    )
    print("Real Dify round", len(transcript), "PASS", flush=True)
    return data


code = "e2e-a-" + uuid.uuid4().hex
first = play(code, "我想买一瓶勇气汽水。")
assert "欠笑话：1" in first["answer"]
marker = "铜锣烧味的月亮-" + uuid.uuid4().hex[:8]
second = play(
    code, "讲笑话：一只章鱼拿着" + marker + "去银行存钱，柜员说我们不收夜宵。", first["conversation_id"]
)
assert "欠笑话：0" in second["answer"]
# Omit conversation_id, requiring actual PowerContext recall.
recall = play(code, "我上次讲的笑话里，月亮是什么味道？请复述那个独特说法。")
assert recall["conversation_id"] != first["conversation_id"]
narrative = recall["answer"].split("**已确认账单**")[0]
assert "铜锣烧" in narrative, "Generated narrative did not use the recalled fact"
assert marker in recall["answer"]
end = play(code, "兑现承诺了，我要结算离店。", recall["conversation_id"])
assert "守约合伙人" in end["answer"]
other = play("e2e-b-" + uuid.uuid4().hex, "我们以前有讲过关于月亮的笑话吗？")
assert marker not in other["answer"]
for confess, ending in [(True, "诚实赊账客"), (False, "回声通缉令")]:
    player = "e2e-route-" + uuid.uuid4().hex
    turn = play(player, "买一瓶勇气汽水。")
    if confess:
        turn = play(player, "我坦白，我现在还没准备好兑现笑话承诺。", turn["conversation_id"])
    turn = play(player, "我要结算离店。", turn["conversation_id"])
    assert ending in turn["answer"]
report = {
    "kind": "real-dify-qwen-powercontext-end-to-end",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "dify_version": "1.9.2",
    "app_id": cfg["app_id"],
    "marker": marker,
    "checks": [
        "real-model-action-recognition",
        "real-memory-write",
        "new-conversation-recall-in-generated-dialogue",
        "player-isolation",
        "three-endings",
    ],
    "rounds": transcript,
}
Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print("PASS: real Dify + online LLM + PowerContext, three endings and new-session recall")
c.close()
