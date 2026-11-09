import json
import re
import uuid


def main(player_code, query):
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", player_code):
        raise ValueError("存档码应为 12—80 位英文字母、数字、下划线或连字符；请保存自己的码")
    if not query.strip() or len(query) > 1200:
        raise ValueError("请输入 1—1200 字的行动")
    return {
        "body": json.dumps(
            {"player_code": player_code, "query": query, "turn_id": str(uuid.uuid4())}, ensure_ascii=False
        )
    }
