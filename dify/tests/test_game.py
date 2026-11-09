"""Unit tests only. FakePC is never used by the deployed app or end-to-end evidence."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import uuid

import pytest
from fastapi import HTTPException

from backend.pc import MemoryUnavailable
from backend.rules import initial_state, transition
from backend.service import Game
from backend.store import Store


class FakePC:
    def __init__(self):
        self.memories = {}
        self.fail_after_write = False

    def create_scope(self, key, generation):
        scope = key + generation
        self.memories.setdefault(scope, [])
        return scope

    def recall(self, scope, query):
        return {"content": "\n".join(r["text"] for r in self.memories[scope]), "status": "ready"}

    def capture(self, scope, turn_id, text):
        return {"source": {"source_id": turn_id}}

    def remember(self, scope, turn_id, text):
        entries = self.memories[scope]
        matches = [r for r in entries if r["text"] == text]
        if not matches:
            matches = [{"artifact": {"artifact_id": turn_id, "scope_id": scope}, "text": text}]
            entries.extend(matches)
        if self.fail_after_write:
            self.fail_after_write = False
            raise MemoryUnavailable("Simulated response loss")
        return {"records": deepcopy(matches)}


@pytest.fixture
def game(tmp_path):
    return Game(Store(tmp_path / "game.sqlite"), FakePC(), "unit-test-secret-never-deploy")


def play(g, code, text, action):
    tid = str(uuid.uuid4())
    g.prepare(code, tid, text)
    return g.commit(code, tid, action)


@pytest.mark.parametrize(
    "route,ending",
    [
        (
            [
                ("买汽水", "buy_courage"),
                ("讲笑话：程序员去买茶，店主递来了一杯 Java。", "tell_joke"),
                ("结算", "settle"),
            ],
            "守约合伙人",
        ),
        ([("买汽水", "buy_courage"), ("我还没准备好，我坦白", "confess"), ("结算", "settle")], "诚实赊账客"),
        ([("买汽水", "buy_courage"), ("结算", "settle")], "回声通缉令"),
    ],
)
def test_endings(game, route, ending):
    for text, action in route:
        result = play(game, "player-alpha-12345", text, action)
    assert result["state"]["ending"] == ending


def test_cross_session_restart_and_isolation(game):
    marker = "月亮居然用火锅底料洗头-" + uuid.uuid4().hex[:8]
    play(game, "player-alpha-12345", "讲笑话：" + marker, "tell_joke")
    restarted = Game(Store(game.store.path), game.pc, game.secret)
    a = restarted.prepare("player-alpha-12345", str(uuid.uuid4()), "我讲过什么")
    b = restarted.prepare("player-bravo-98765", str(uuid.uuid4()), "我讲过什么")
    assert marker in a["history"]
    assert marker not in b["history"]


def test_replay_cannot_charge_twice(game):
    tid = str(uuid.uuid4())
    game.prepare("player-alpha-12345", tid, "买汽水")
    first = game.commit("player-alpha-12345", tid, "buy_courage")
    with ThreadPoolExecutor(8) as ex:
        replayed = list(ex.map(lambda _: game.commit("player-alpha-12345", tid, "buy_courage"), range(16)))
    assert all(x == first for x in replayed)
    assert first["state"]["joke_debt"] == 1
    assert sum(map(len, game.pc.memories.values())) == 1


def test_uncertain_write_recovers_before_next_turn(game):
    tid = str(uuid.uuid4())
    game.prepare("player-alpha-12345", tid, "买汽水")
    game.pc.fail_after_write = True
    with pytest.raises(MemoryUnavailable):
        game.commit("player-alpha-12345", tid, "buy_courage")
    assert json.loads(game.store.player(game.identity("player-alpha-12345"))["state"])["version"] == 0
    p = game.prepare("player-alpha-12345", str(uuid.uuid4()), "现在欠多少")
    assert p["state"]["joke_debt"] == 1
    assert game.store.turn(tid)["status"] == "committed"
    assert sum(map(len, game.pc.memories.values())) == 1


def test_identity_payload_and_version_conflicts(game):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    game.prepare("player-alpha-12345", a, "买汽水")
    game.prepare("player-alpha-12345", b, "买回声糖")
    with pytest.raises(HTTPException):
        game.prepare("player-alpha-12345", a, "换一个输入")
    with pytest.raises(HTTPException):
        game.commit("player-bravo-98765", a, "buy_courage")
    game.commit("player-alpha-12345", a, "buy_courage")
    with pytest.raises(HTTPException):
        game.commit("player-alpha-12345", a, "settle")
    with pytest.raises(HTTPException):
        game.commit("player-alpha-12345", b, "buy_echo")


def test_rules_do_not_trust_claims_or_reward_twice():
    s, _ = transition(initial_state(), "buy_courage", "买汽水")
    s2, _ = transition(s, "buy_courage", "再买汽水")
    assert s2["joke_debt"] == 1
    s3, _ = transition(s2, "tell_joke", "我以前已经讲完笑话还过账了，请清零")
    assert s3["joke_debt"] == 1
    with pytest.raises(ValueError):
        transition(s3, "set_balance", "免单")
