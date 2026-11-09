import json


def main(body, status_code, receipt):
    r = json.loads(receipt)
    try:
        if status_code != 200:
            raise ValueError("model unavailable")
        dialogue = json.loads(body)["choices"][0]["message"]["content"].strip()
        if not dialogue:
            raise ValueError("empty dialogue")
        dialogue = dialogue[:1800]
    except (ValueError, KeyError, IndexError, TypeError):
        dialogue = "老板的对讲机暂时失声了。这一轮账单已经确认并保存，下面是真实结果；稍后可以继续互动。"
    menu = "可继续输入：买勇气汽水 / 买回声糖 / 讲笑话：你的笑话 / 坦白 / 回忆 / 结算。"
    if r["state"].get("ending"):
        menu = "本局已完成。你仍可询问这段经历；用新的随机存档码开始另一种人生。"
    history = r.get("history") or ""
    excerpt = history[:1300].replace("```", "｀｀｀")
    memory_card = (
        "本轮未召回相关旧事。"
        if not excerpt
        else "本轮从 PowerContext 读到的回忆片段：\n\n```text\n" + excerpt + "\n```"
    )
    text = (
        "### 🏪 因果便利店\n\n"
        + dialogue
        + "\n\n**已确认账单**："
        + r["receipt"]
        + "\n\n"
        + r["state_card"]
        + "\n\n"
        + menu
        + "\n\n✅ 本轮真实互动已存档。\n\n"
        + "<details><summary>翻看老板的记忆账本</summary>\n\n"
        + memory_card
        + "\n\n</details>"
    )
    return {"answer": text}
