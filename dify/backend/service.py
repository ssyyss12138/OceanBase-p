import hashlib
import hmac
import json
import threading
import uuid

from fastapi import HTTPException
from filelock import FileLock

from .rules import ACTIONS, CATALOG, initial_state, state_card, transition


def packed(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


class Game:
    def __init__(self, store, pc, secret):
        self.store, self.pc, self.secret = store, pc, secret
        self.lock = threading.RLock()
        self.file_lock = FileLock(store.path + ".lock", timeout=35)

    def identity(self, code):
        return hmac.new(self.secret.encode(), code.encode(), hashlib.sha256).hexdigest()

    def prepare(self, code, turn_id, query):
        key = self.identity(code)
        digest = hashlib.sha256(packed([key, query]).encode()).hexdigest()
        with self.lock, self.file_lock:
            old = self.store.turn(turn_id)
            if old:
                if old["player_hash"] != key or old["payload_hash"] != digest:
                    raise HTTPException(409, "本轮标识已经用于其他输入")
                return json.loads(old["prepared"])
            player = self.store.player(key)
            if not player:
                generation = str(uuid.uuid4())
                scope = self.pc.create_scope(key, generation)
                with self.store.db() as c:
                    c.execute(
                        "INSERT INTO players VALUES (?,?,?,?)",
                        (key, generation, scope, packed(initial_state())),
                    )
                player = self.store.player(key)
            # Recover an interrupted journal before starting a later round.
            with self.store.db() as c:
                pending = c.execute(
                    "SELECT id FROM turns WHERE player_hash=? AND status='committing'", (key,)
                ).fetchall()
            for t in pending:
                self._finish(t["id"])
            player = self.store.player(key)
            state = json.loads(player["state"])
            if state["visits"] >= 60:
                raise HTTPException(409, "此存档已达六十轮，请创建新存档码")
            history = self.pc.recall(player["scope_id"], "便利店 游戏 交易 承诺 笑话 玩家 " + query)
            result = {
                "turn_id": turn_id,
                "state": state,
                "catalog": CATALOG,
                "history": history.get("content") or "",
                "history_status": history.get("status"),
                "query": query,
                "allowed_actions": list(ACTIONS),
            }
            with self.store.db() as c:
                c.execute(
                    "INSERT INTO turns(id,player_hash,payload_hash,expected_version,input,prepared) VALUES (?,?,?,?,?,?)",
                    (turn_id, key, digest, state["version"], query, packed(result)),
                )
            return result

    def commit(self, code, turn_id, action):
        key = self.identity(code)
        with self.lock, self.file_lock:
            turn = self.store.turn(turn_id)
            if turn is None or turn["player_hash"] != key:
                raise HTTPException(404, "找不到本轮")
            if turn["proposal"] and turn["proposal"] != action:
                raise HTTPException(409, "同一轮不能更换行动")
            if turn["status"] == "committed":
                return json.loads(turn["result"])
            if turn["status"] == "prepared":
                state = json.loads(self.store.player(key)["state"])
                if state["version"] != turn["expected_version"]:
                    raise HTTPException(409, "存档已经被另一轮更新，请重新发送输入")
                next_state, receipt = transition(state, action, turn["input"])
                with self.store.db() as c:
                    c.execute(
                        "UPDATE turns SET status='committing',proposal=?,next_state=?,receipt=? WHERE id=?",
                        (action, packed(next_state), receipt, turn_id),
                    )
            return self._finish(turn_id)

    def _finish(self, turn_id):
        turn = self.store.turn(turn_id)
        player = self.store.player(turn["player_hash"])
        # Preserve original player input, not a canned example or an LLM-written false recollection.
        event = packed(
            {
                "turn_id": turn_id,
                "玩家真实输入": turn["input"],
                "确认行动": turn["proposal"],
                "事实结果": turn["receipt"],
                "结算后状态": json.loads(turn["next_state"]),
            }
        )
        if not turn["source"]:
            source = self.pc.capture(player["scope_id"], turn_id, event)
            self.store.checkpoint(turn_id, "source", source)
        if not turn["memory"]:
            memory = self.pc.remember(player["scope_id"], turn_id, event)
            if not memory.get("records"):
                raise RuntimeError("PowerContext 未返回保存的记忆，不能确认结算")
            self.store.checkpoint(turn_id, "memory", memory)
        turn = self.store.turn(turn_id)
        state = json.loads(turn["next_state"])
        memory = json.loads(turn["memory"])
        result = {
            "turn_id": turn_id,
            "action": turn["proposal"],
            "receipt": turn["receipt"],
            "state": state,
            "state_card": state_card(state),
            "memory_saved": True,
            "memory_refs": [r["artifact"] for r in memory["records"]],
            "history": json.loads(turn["prepared"])["history"],
            "query": turn["input"],
        }
        with self.store.db() as c:
            current = c.execute(
                "SELECT state FROM players WHERE player_hash=?", (turn["player_hash"],)
            ).fetchone()
            if json.loads(current["state"])["version"] != turn["expected_version"]:
                raise HTTPException(409, "本轮结算版本冲突")
            c.execute("UPDATE players SET state=? WHERE player_hash=?", (packed(state), turn["player_hash"]))
            c.execute("UPDATE turns SET status='committed',result=? WHERE id=?", (packed(result), turn_id))
        return result
