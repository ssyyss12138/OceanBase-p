"""Real Dify check with deliberately unconfigured LLM; never a positive Qwen pass."""

import argparse
import json
import uuid
from pathlib import Path

import httpx

p = argparse.ArgumentParser()
p.add_argument("--private-config", required=True)
p.add_argument("--output", required=True)
p.add_argument("--expect-unconfigured-model", action="store_true", required=True)
a = p.parse_args()
cfg = json.loads(Path(a.private_config).read_text())
code = "configuration-check-" + uuid.uuid4().hex
with httpx.Client(base_url=cfg["base_url"], timeout=240, trust_env=False) as c:
    r = c.post(
        "/v1/chat-messages",
        headers={"Authorization": "Bearer " + cfg["service_api_key"]},
        json={
            "inputs": {"player_code": code},
            "query": "看看货架，验证模型未配置时不能假装完成交易",
            "response_mode": "blocking",
            "user": "configuration-check-" + uuid.uuid4().hex,
        },
    )
    r.raise_for_status()
    data = r.json()
    answer = data.get("answer", "")
    assert "便利店暂时无法完成这一轮" in answer and "✅ 本轮真实互动已存档" not in answer
    r = c.post("/console/api/login", json={"email": cfg["admin_email"], "password": cfg["admin_password"]})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = c.cookies.get("csrf_token")
    prefix = "/console/api/apps/" + cfg["app_id"]
    r = c.get(prefix + "/advanced-chat/workflow-runs", params={"limit": 20, "triggered_from": "app-run"})
    r.raise_for_status()
    run = next(x for x in r.json()["data"] if x.get("message_id") == data["message_id"])
    r = c.get(prefix + "/workflow-runs/" + run["id"] + "/node-executions")
    r.raise_for_status()
    nodes = r.json()["data"]
    # Never export node inputs, HTTP Authorization headers, environment variables or raw prompts.
    summary = [
        {k: n.get(k) for k in ["node_id", "node_type", "status", "error", "elapsed_time"]} for n in nodes
    ]
    for entry in summary:
        if entry.get("error"):
            entry["error"] = entry["error"].split("\n")[0][:200]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    by_id = {n["node_id"]: n for n in nodes}
    for nid in ["start", "encode", "prepare", "plan"]:
        assert by_id[nid]["status"] == "succeeded", f"Expected actual preparation success at {nid}"
    assert by_id["prepare"]["outputs"]["status_code"] == 200
    assert by_id.get("save", {}).get("status") != "succeeded"
    report = {
        "kind": "real-dify-expected-model-configuration-failure",
        "app_id": cfg["app_id"],
        "workflow_run_id": run["id"],
        "message_id": data["message_id"],
        "answer": answer,
        "checks": [
            "real-sandbox-code-execution",
            "real-backend-PowerContext-preparation",
            "unconfigured-model-did-not-commit-a-round",
        ],
        "nodes": summary,
        "limitation": "No successful Qwen call; this is not full end-to-end acceptance.",
    }
    Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        "PASS: real Dify reached actual memory preparation and did not pretend an unavailable model succeeded"
    )
