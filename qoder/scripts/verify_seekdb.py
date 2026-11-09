"""PaperTrail seekdb 验证脚本.

真实测试 pyseekdb 嵌入式模式(非模拟数据库)的:
  写入 / 按 ID 查询 / 全文检索 / 子串检索 / 状态筛选 / 更新 / 删除 / 统计 / 幂等初始化 / 持久化。

用法(在 qoder/ 目录下,分两次独立进程运行,以真实证明跨进程持久化):
    .venv/bin/python scripts/verify_seekdb.py --write   # 第 1 次:清理旧数据并写入/增删改查
    .venv/bin/python scripts/verify_seekdb.py --verify  # 第 2 次:新进程重开数据库,校验数据仍在

退出码:全部通过为 0,任一失败为 1。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.seekdb_store import READ_STATUS, UNREAD_STATUS, SeekdbStore  # noqa: E402

VERIFY_DB_DIR = ROOT / "data" / "verify_seekdb.db"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [通过] {name}")
    else:
        FAILED += 1
        print(f"  [失败] {name}  {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_store() -> SeekdbStore:
    store = SeekdbStore(db_dir=str(VERIFY_DB_DIR))
    store.ensure_ready()
    return store


def phase_write() -> int:
    print(f"数据目录: {VERIFY_DB_DIR}")
    if VERIFY_DB_DIR.exists():
        shutil.rmtree(VERIFY_DB_DIR)
        print("已清理旧验证数据目录")

    store = make_store()

    section("1. 初始化(创建数据库与集合)")
    check("数据库/集合就绪", store.collection is not None)

    section("2. 写入 3 条文献")
    p1 = store.add_paper(
        {
            "title": "分布式数据库系统架构研究",
            "authors": "张三, 李四",
            "abstract": "面向海量数据的分布式数据库存储引擎设计",
            "link": "https://example.edu/paper-1",
            "tags": "数据库,分布式",
            "status": UNREAD_STATUS,
            "note": "存储引擎部分值得精读",
        },
        paper_id="verify-1",
    )
    p2 = store.add_paper(
        {
            "title": "自然语言处理的深度学习方法综述",
            "authors": "Alice",
            "abstract": "Transformer 与注意力机制在 NLP 任务中的应用",
            "link": "https://example.edu/paper-2",
            "tags": "NLP,Transformer",
            "status": READ_STATUS,
            "note": "",
        },
        paper_id="verify-2",
    )
    p3 = store.add_paper(
        {
            "title": "数据库事务与并发控制",
            "authors": "王五",
            "abstract": "事务隔离级别与 MVCC 机制",
            "link": "https://example.edu/paper-3",
            "tags": "数据库,事务",
            "status": UNREAD_STATUS,
            "note": "和期末大作业相关",
        },
        paper_id="verify-3",
    )
    check("写入 3 条后 count == 3", store.collection.count() == 3, f"实际 {store.collection.count()}")

    section("3. 按 ID 查询")
    got = store.get_paper("verify-2")
    check("按 ID 查到文献", got is not None and got["title"] == "自然语言处理的深度学习方法综述")
    check("不存在的 ID 返回 None", store.get_paper("no-such-id") is None)

    section("4. 关键词检索(标题/摘要/笔记)")
    hits = store.search_papers(keyword="架构")
    check("标题关键词「架构」命中 verify-1", "verify-1" in [p["id"] for p in hits], f"命中 {[p['id'] for p in hits]}")
    hits = store.search_papers(keyword="Transformer")
    check("摘要英文关键词「Transformer」命中 verify-2", "verify-2" in [p["id"] for p in hits])
    hits = store.search_papers(keyword="期末")
    check("笔记关键词「期末」命中 verify-3", "verify-3" in [p["id"] for p in hits])
    hits = store.search_papers(keyword="不存在的关键词")
    check("无关关键词返回空", len(hits) == 0, f"实际 {len(hits)}")
    hits = store.search_papers(keyword="的研究")
    hit_ids = {p["id"] for p in hits}
    check("含停用词查询不退化为全表命中(仅允许命中真实相关文献)",
          "verify-2" not in hit_ids and "verify-3" not in hit_ids, f"实际 {sorted(hit_ids)}")

    section("5. 阅读状态筛选")
    hits = store.search_papers(status=READ_STATUS)
    check("状态=已读 仅命中 verify-2", [p["id"] for p in hits] == ["verify-2"], f"实际 {[p['id'] for p in hits]}")
    hits = store.search_papers(status=UNREAD_STATUS)
    check("状态=未读 命中 verify-1/3", {p["id"] for p in hits} == {"verify-1", "verify-3"})
    hits = store.search_papers(keyword="数据库", status=UNREAD_STATUS)
    check("关键词+状态组合过滤正确", {p["id"] for p in hits} == {"verify-1", "verify-3"}, f"实际 {[p['id'] for p in hits]}")

    section("6. 更新")
    updated = store.update_paper("verify-1", {"status": READ_STATUS, "note": "已读完,笔记更新为量子加速相关内容"})
    check("更新返回新内容", updated is not None and updated["status"] == READ_STATUS)
    check("更新已持久化(按 ID 读回)", store.get_paper("verify-1")["status"] == READ_STATUS)
    hits = store.search_papers(keyword="量子加速")
    check("更新后的新笔记可被检索", "verify-1" in [p["id"] for p in hits])
    check("更新不存在的文献返回 None", store.update_paper("no-such-id", {"note": "x"}) is None)
    hits = store.search_papers(keyword="存储引擎的研究")
    hit_ids = [p["id"] for p in hits]
    check("含停用词查询正确回退(命中真实相关文献且无误命中)",
          "verify-1" in hit_ids and "verify-2" not in hit_ids and "verify-3" not in hit_ids, f"实际 {hit_ids}")

    section("7. 删除")
    check("删除 verify-3 返回 True", store.delete_paper("verify-3") is True)
    check("删除后按 ID 查不到", store.get_paper("verify-3") is None)
    check("删除后 count == 2", store.collection.count() == 2, f"实际 {store.collection.count()}")
    check("重复删除返回 False", store.delete_paper("verify-3") is False)

    section("8. 统计")
    stats = store.stats()
    check("统计总数 == 2", stats["total"] == 2, f"实际 {stats['total']}")
    check("统计已读 == 2", stats["read"] == 2, f"实际 {stats['read']}")
    check("统计未读 == 0", stats["unread"] == 0, f"实际 {stats['unread']}")

    section("9. 演示数据幂等初始化")
    inserted1 = store.seed_demo_data()
    inserted2 = store.seed_demo_data()
    check("首次初始化新增 3 条", inserted1 == 3, f"实际 {inserted1}")
    check("重复初始化不产生重复数据", inserted2 == 0, f"实际 {inserted2}")
    total = store.collection.count()
    check("初始化后 count == 5(2 用户数据 + 3 演示)", total == 5, f"实际 {total}")

    print(f"\n写入阶段完成: {PASSED} 通过 / {FAILED} 失败")
    print("请用 --verify 参数重新运行本脚本(新进程),校验数据持久化。")
    return 1 if FAILED else 0


def phase_verify() -> int:
    print(f"重新打开数据目录(新进程): {VERIFY_DB_DIR}")
    if not VERIFY_DB_DIR.exists():
        print("  [失败] 数据目录不存在,先执行 --write")
        return 1

    store = make_store()

    section("10. 跨进程持久化")
    check("verify-1 仍存在", store.get_paper("verify-1") is not None)
    check("verify-2 仍存在", store.get_paper("verify-2") is not None)
    check("verify-3 已被删除(删除操作持久化)", store.get_paper("verify-3") is None)
    check("演示数据 demo-0001 仍存在", store.get_paper("demo-0001") is not None)

    got = store.get_paper("verify-1")
    check("verify-1 更新后的状态持久化", got is not None and got["status"] == READ_STATUS)
    check("verify-1 更新后的笔记持久化", got is not None and "量子加速" in got["note"])

    hits = store.search_papers(keyword="量子加速")
    check("新进程内仍可检索到更新后的笔记", "verify-1" in [p["id"] for p in hits])
    hits = store.search_papers(keyword="Transformer")
    check("新进程内英文关键词检索正常", "verify-2" in [p["id"] for p in hits])

    stats = store.stats()
    check("新进程内统计总数 == 5", stats["total"] == 5, f"实际 {stats['total']}")

    print(f"\n持久化校验完成: {PASSED} 通过 / {FAILED} 失败")
    return 1 if FAILED else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperTrail seekdb 验证脚本")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="写入/增删改查阶段(先清理旧数据)")
    group.add_argument("--verify", action="store_true", help="新进程校验持久化阶段")
    args = parser.parse_args()

    if args.write:
        return phase_write()
    return phase_verify()


if __name__ == "__main__":
    sys.exit(main())
