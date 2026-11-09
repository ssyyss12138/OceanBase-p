"""One small real Qwen call; never prints or stores the API key."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx
from dotenv import dotenv_values

p = argparse.ArgumentParser()
p.add_argument("--config", required=True)
p.add_argument("--output")
a = p.parse_args()
cfg = dotenv_values(a.config)
if not all(cfg.get(k) for k in ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY")):
    raise SystemExit("Model config is incomplete; fill MODEL_BASE_URL, MODEL_NAME, MODEL_API_KEY locally.")
url = cfg["MODEL_BASE_URL"].rstrip("/") + "/chat/completions"
with httpx.Client(timeout=90) as c:
    r = c.post(
        url,
        headers={"Authorization": "Bearer " + cfg["MODEL_API_KEY"]},
        json={
            "model": cfg["MODEL_NAME"],
            "messages": [{"role": "user", "content": "只回复：便利店开门了"}],
            "max_tokens": 30,
            "stream": False,
            "enable_thinking": False,
        },
    )
    if r.status_code != 200:
        raise SystemExit(
            f"Model call failed: HTTP {r.status_code}. Check provider region/workspace, Key and model access."
        )
    result = r.json()
    content = result["choices"][0]["message"]["content"]
    if not content.strip():
        raise SystemExit("Model returned empty content")
    report = {
        "kind": "real-llm-api-check",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model": result.get("model", cfg["MODEL_NAME"]),
        "reply": content,
        "usage": result.get("usage"),
    }
    if a.output:
        Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("Real LLM API: PASS; model returned nonempty text.")
