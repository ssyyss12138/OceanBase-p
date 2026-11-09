"""Only real PowerContext HTTP calls; no local memory substitute."""

import unicodedata

import httpx


class MemoryUnavailable(RuntimeError):
    pass


class PowerContext:
    def __init__(self, url, token=""):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=25, trust_env=False)

    def post(self, path, payload):
        try:
            r = self.client.post(path, json=payload)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never echo response bodies, URLs, credentials, or raw private context to clients/logs.
            raise MemoryUnavailable(f"PowerContext {path} 暂不可用") from exc

    def create_scope(self, player_hash, generation):
        r = self.post(
            "/v1/scopes",
            {
                "title": "因果便利店玩家存档",
                "summary": "玩家交易、承诺与真实互动记忆",
                "idempotency_key": f"causal-store:{player_hash}:{generation}",
            },
        )
        return r["scope_id"]

    def recall(self, scope_id, query):
        return self.post("/v1/context/prepare", {"scope_id": scope_id, "query": query, "max_bytes": 5500})

    def capture(self, scope_id, turn_id, text):
        return self.post(
            "/v1/sources/content",
            {
                "scope_id": scope_id,
                "source_id": "store-" + turn_id,
                "content": text,
                "metadata": {"origin": "causal-corner-store", "turn_id": turn_id},
            },
        )

    def remember(self, scope_id, turn_id, text):
        # Match the pinned Server canonical text representation before uncertain-write reconciliation.
        text = unicodedata.normalize("NFC", text).strip()
        # Reconcile a write whose HTTP response/checkpoint was lost. Each game is capped at 60 rounds.
        existing = self.post("/v1/memory/entries/list", {"scope_id": scope_id, "limit": 100})
        matches = [e for e in existing.get("entries", []) if e.get("text") == text]
        if matches:
            return {"changed": False, "records": matches}
        return self.post(
            "/v1/memory/remember",
            {
                "scope_id": scope_id,
                "kind": "game-event",
                "text": text,
                "reason": "玩家在互动游戏中的已确认选择；存档用于之后的因果互动",
            },
        )
