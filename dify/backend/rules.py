"""Deterministic game rules. LLM dialogue never changes authoritative state."""

from copy import deepcopy

ACTIONS = ("browse", "buy_courage", "buy_echo", "tell_joke", "confess", "settle", "recall")
CATALOG = [
    {"id": "courage", "name": "勇气汽水", "price": "一个真笑话", "effect": "先喝后付，下次进店要还笑话"},
    {"id": "echo", "name": "回声糖", "price": "一枚旧承诺", "effect": "买过汽水后可取走，但会多欠一个笑话"},
]


def initial_state():
    return {
        "version": 0,
        "visits": 0,
        "inventory": [],
        "joke_debt": 0,
        "jokes_paid": 0,
        "confessed": False,
        "ending": None,
    }


def transition(state, action, player_input):
    """Return new state and a factual receipt; failed actions cannot mutate rewards/debt."""
    if action not in ACTIONS:
        raise ValueError("未知行动")
    s = deepcopy(state)
    note = ""
    if s["ending"] and action not in ("recall", "browse"):
        note = "本局已经结算。可以回忆经历，或用新存档码开始新局。"
    elif action == "buy_courage":
        if "courage" in s["inventory"]:
            note = "勇气汽水每局限购一瓶，不会重复记账。"
        else:
            s["inventory"].append("courage")
            s["joke_debt"] += 1
            note = "你喝下勇气汽水，欠老板一个真笑话。下次见面这笔账仍在。"
    elif action == "buy_echo":
        if "courage" not in s["inventory"]:
            note = "回声糖需要先购买勇气汽水，普通钱币在这里不通用。"
        elif "echo" in s["inventory"]:
            note = "回声糖每局限购一颗，不会重复记账。"
        else:
            s["inventory"].append("echo")
            s["joke_debt"] += 1
            note = "回声糖把你的承诺重复了一遍：现在共欠老板两个笑话。"
    elif action == "tell_joke":
        if s["joke_debt"] == 0:
            note = "老板收下笑话，但你目前没有欠账。"
        elif (
            not player_input.strip().startswith(("讲笑话：", "讲笑话:"))
            or len(player_input.strip()[4:].strip()) < 8
        ):
            note = "请用「讲笑话：具体内容」讲一个至少八个字的笑话，只有“我讲完了”不算还账。"
        else:
            s["joke_debt"] -= 1
            s["jokes_paid"] += 1
            note = "老板把你刚刚讲的真实内容记入笑话账本，笑话欠账减少一个。"
    elif action == "confess":
        s["confessed"] = True
        note = "你承认自己还没准备好兑现承诺。老板记下了这份坦诚，欠账暂时保留。"
    elif action == "settle":
        if not s["inventory"]:
            note = "故事还没开始，先选一件商品。"
        elif s["joke_debt"] == 0:
            s["ending"] = "守约合伙人"
            note = "结局「守约合伙人」：承诺全部兑现，你获得了便利店的夜班钥匙。"
        elif s["confessed"]:
            s["ending"] = "诚实赊账客"
            note = "结局「诚实赊账客」：老板保留欠账，给坦诚的你留了一盏灯。"
        else:
            s["ending"] = "回声通缉令"
            note = "结局「回声通缉令」：没兑现的承诺变成会追着你念的收据。"
    elif action == "recall":
        note = "请根据本轮实际召回的记忆回应玩家；没有召回时不得编造往事。"
    else:
        note = "老板打开货架：勇气汽水和回声糖。每笔交易都留下真实后果。"
    s["version"] += 1
    s["visits"] += 1
    return s, note


def state_card(state):
    items = {"courage": "勇气汽水", "echo": "回声糖"}
    bag = "、".join(items[x] for x in state["inventory"]) or "空"
    ending = f"\n🏁 结局：**{state['ending']}**" if state["ending"] else ""
    return f"🎒 背包：{bag}｜🧾 欠笑话：{state['joke_debt']}｜✅ 已兑现：{state['jokes_paid']}" + ending
