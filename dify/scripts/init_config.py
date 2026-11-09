"""Generate local credentials without printing them; never overwrite an existing config."""

import argparse
import secrets
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", default=".env")
args = parser.parse_args()
p = Path(args.output)
if p.exists():
    raise SystemExit(f"{p} already exists; keeping existing credentials and player identities")
keys = [
    "GAME_API_KEY",
    "PLAYER_HASH_SECRET",
    "POWERCONTEXT_TOKEN",
    "DIFY_SECRET_KEY",
    "DB_PASSWORD",
    "REDIS_PASSWORD",
    "SANDBOX_API_KEY",
]
p.parent.mkdir(parents=True, exist_ok=True)
with p.open("x") as f:
    f.write("".join(k + "=" + secrets.token_urlsafe(32) + "\n" for k in keys))
    f.write("MODEL_BASE_URL=\nMODEL_NAME=qwen-plus\nMODEL_API_KEY=\n")
p.chmod(0o600)
print(f"Created private config {p}. Fill MODEL_* before the real LLM end-to-end run.")
