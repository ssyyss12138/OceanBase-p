import json


def main(body, status_code, prepared, player_code):
    if status_code != 200:
        raise ValueError("模型服务未完成行动识别，本轮没有结算；请检查模型配置或稍后重试")
    data = json.loads(body)
    text = data["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    proposal = json.loads(text)
    action = proposal.get("action")
    if action not in ("browse", "buy_courage", "buy_echo", "tell_joke", "confess", "settle", "recall"):
        raise ValueError("模型返回了未知行动，本轮未结算")
    p = json.loads(prepared)
    return {"body": json.dumps({"player_code": player_code, "turn_id": p["turn_id"], "action": action})}
