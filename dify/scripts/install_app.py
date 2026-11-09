"""Import/publish the actual DSL through Dify 1.9.2 console API. Secrets stay local."""

import argparse
import json
import secrets
from pathlib import Path

import httpx
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--url", default="http://127.0.0.1:8080")
p.add_argument("--config", required=True)
p.add_argument("--model-config")
p.add_argument("--private-output", required=True)
p.add_argument("--game-url", default="http://game:8090")
p.add_argument("--evidence", default=str(ROOT / "evidence/dify-import.json"))
p.add_argument(
    "--allow-unconfigured-model", action="store_true", help="Import only; never claim LLM end-to-end success"
)
a = p.parse_args()
cfg = dict(dotenv_values(a.config))
if a.model_config:
    cfg.update({k: v for k, v in dotenv_values(a.model_config).items() if v})
if not a.allow_unconfigured_model and not all(
    cfg.get(k) for k in ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY")
):
    raise SystemExit("Fill MODEL_BASE_URL, MODEL_NAME and MODEL_API_KEY in your private config first.")
private = Path(a.private_output)
private.parent.mkdir(parents=True, exist_ok=True)
if private.exists():
    previous = json.loads(private.read_text())
    email, password = previous["admin_email"], previous["admin_password"]
else:
    email, password = "owner@example.com", secrets.token_urlsafe(24) + "Aa1!"
    previous = {"admin_email": email, "admin_password": password}
    private.write_text(json.dumps(previous, indent=2))
    private.chmod(0o600)
client = httpx.Client(base_url=a.url.rstrip("/"), timeout=120, trust_env=False)


def call(method, path, **kw):
    csrf = client.cookies.get("csrf_token")
    if csrf:
        client.headers["X-CSRF-Token"] = csrf
    r = client.request(method, "/console/api" + path, **kw)
    if r.status_code >= 400:
        # Never print a full request or response which might contain credentials.
        try:
            message = str(r.json().get("message", ""))[:240]
        except ValueError:
            message = ""
        for value in (cfg.get("MODEL_API_KEY"), cfg.get("GAME_API_KEY"), password):
            if value:
                message = message.replace(value, "[REDACTED]")
        raise RuntimeError(f"Dify {method} {path}: HTTP {r.status_code} {message}")
    return r.json()


status = call("GET", "/setup")
if status["step"] != "finished":
    call(
        "POST",
        "/setup",
        json={"email": email, "name": "因果便利店", "password": password, "language": "zh-Hans"},
    )
call("POST", "/login", json={"email": email, "password": password, "remember_me": True})
workflow = yaml.safe_load((ROOT / "dsl/causal-store.yml").read_text())
values = {
    "GAME_URL": a.game_url,
    "GAME_API_KEY": cfg["GAME_API_KEY"],
    "MODEL_BASE_URL": (cfg.get("MODEL_BASE_URL") or "https://unconfigured.invalid/v1").rstrip("/"),
    "MODEL_NAME": cfg.get("MODEL_NAME") or "qwen-plus",
    "MODEL_API_KEY": cfg.get("MODEL_API_KEY") or "",
}
for v in workflow["workflow"]["environment_variables"]:
    v["value"] = values[v["name"]]
payload = {
    "mode": "yaml-content",
    "yaml_content": yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False),
}
if previous.get("app_id"):
    payload["app_id"] = previous["app_id"]
result = call("POST", "/apps/imports", json=payload)
if result.get("status") == "pending":
    result = call("POST", "/apps/imports/" + result["id"] + "/confirm", json={})
app_id = result.get("app_id")
if not app_id:
    raise RuntimeError("Dify did not return an imported app ID")
call("POST", f"/apps/{app_id}/workflows/publish", json={})
if previous.get("app_id") == app_id and previous.get("service_api_key"):
    key = {"token": previous["service_api_key"]}
else:
    key = call("POST", f"/apps/{app_id}/api-keys", json={})
site = call("GET", f"/apps/{app_id}")
previous.update(
    {
        "app_id": app_id,
        "service_api_key": key["token"],
        "base_url": a.url.rstrip("/"),
        "webapp_code": site.get("site", {}).get("code"),
    }
)
private.write_text(json.dumps(previous, indent=2))
private.chmod(0o600)
report = {
    "kind": "real-dify-console-import",
    "dify_version": "1.9.2",
    "dsl_version": workflow["version"],
    "app_id": app_id,
    "import_status": result.get("status"),
    "published": True,
    "node_count": len(workflow["workflow"]["graph"]["nodes"]),
    "model_configured": bool(cfg.get("MODEL_BASE_URL") and cfg.get("MODEL_API_KEY")),
    "limitation": "Import/publish success alone does not prove end-to-end Qwen execution.",
}
Path(a.evidence).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print("Imported and published Dify app:", app_id)
print("Private credentials saved locally. No secret values are included in evidence.")
client.close()
