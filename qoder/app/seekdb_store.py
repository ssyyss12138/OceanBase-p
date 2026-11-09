"""PaperTrail seekdb 存储层.

封装 pyseekdb 官方 SDK 的本地嵌入式模式(Client(path=..., database=...)),
负责 seekdb 数据库/集合的初始化与文献的写入、查询、更新、删除、检索与持久化。

设计说明:
- 使用 seekdb 默认 IK 分词器的全文索引;检索时对关键词同时做全文匹配($contains)
  与子串匹配($regex),避免中文复合词分词导致短关键词漏检。
- 集合创建时显式传入 embedding_function=None,不下载任何外部模型(离线可用);
  写入时提供定长占位向量以满足集合的向量列约束,应用不使用向量检索能力。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional

import pyseekdb

logger = logging.getLogger("papertrail")

# 集合默认向量维度(与 SDK 默认 HNSW 配置一致,仅作占位,不参与检索)
VECTOR_DIM = 384

READ_STATUS = "read"
UNREAD_STATUS = "unread"
VALID_STATUSES = (READ_STATUS, UNREAD_STATUS)

# 写入后全文索引异步构建,轮询等待其可见的最长时间
INDEX_WAIT_TIMEOUT = 3.0
INDEX_WAIT_INTERVAL = 0.5

# seekdb 嵌入式引擎启动/退出有短暂窗口,初始化失败时重试
INIT_MAX_ATTEMPTS = 8
INIT_RETRY_INTERVAL = 3.0

DEMO_PAPERS = [
    {
        "id": "demo-0001",
        "title": "OceanBase: 可扩展的分布式关系数据库架构与存储引擎",
        "authors": "杨传辉, 阳振坤",
        "abstract": "本文介绍 OceanBase 分布式关系数据库的整体架构,重点讨论其存储引擎、多副本一致性协议以及面向海量数据的高可用设计,并给出了在金融核心场景下的实践效果。",
        "link": "https://oceanbase.com/docs",
        "tags": "分布式数据库,OceanBase",
        "status": UNREAD_STATUS,
        "note": "参赛必读,重点关注存储引擎与事务部分。",
    },
    {
        "id": "demo-0002",
        "title": "Attention Is All You Need",
        "authors": "Vaswani et al.",
        "abstract": "提出完全基于注意力机制的 Transformer 网络结构,摒弃循环与卷积结构,在机器翻译任务上取得显著提升,成为后续大语言模型的基础架构。",
        "link": "https://arxiv.org/abs/1706.03762",
        "tags": "深度学习,NLP,Transformer",
        "status": READ_STATUS,
        "note": "已读完,Self-Attention 部分值得反复看。",
    },
    {
        "id": "demo-0003",
        "title": "数据库事务隔离级别与并发控制机制综述",
        "authors": "王珊, 陈红",
        "abstract": "系统梳理了读未提交、读已提交、可重复读与串行化四种隔离级别,对比了基于锁与多版本并发控制(MVCC)的实现机制及其对数据库性能的影响。",
        "link": "https://example.edu.cn/txn-survey",
        "tags": "数据库,事务,MVCC",
        "status": UNREAD_STATUS,
        "note": "",
    },
]


# 常见中文功能字/停用字:实测 seekdb 全文检索(MATCH ... AGAINST)查询中包含这些字时
# 条件会失效并产生误命中(如 '已' 让不相关文献全部命中),因此对 $contains 子句
# 先剔除这些字再检索,同时用 $regex 保留原始关键词的子串精确匹配兜底
_STRIP_CHARS = frozenset(
    "的了是在和与及或之其这那等们着呢吗于对为从向把被不也已就都而且但只又很太更最并我你他她它些个"
)
_ENGLISH_STOP_WORDS = (
    "the", "a", "an", "of", "and", "or", "to", "in", "is", "are",
    "was", "for", "with", "on", "at", "by", "as", "that", "this",
)


def _clean_keyword_for_fulltext(keyword: str) -> str:
    """剔除查询中的常见停用字/词,供 $contains 全文检索使用。"""
    text = "".join(ch for ch in keyword if ch not in _STRIP_CHARS)
    pattern = r"\b(?:" + "|".join(_ENGLISH_STOP_WORDS) + r")\b"
    text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_document(paper: dict[str, Any]) -> str:
    """将文献各字段拼接为 seekdb document 全文检索字段(标题/作者/标签/摘要/笔记)。"""
    parts = [
        paper.get("title", ""),
        paper.get("authors", ""),
        paper.get("tags", ""),
        paper.get("abstract", ""),
        paper.get("note", ""),
    ]
    return "\n".join(str(p) for p in parts)


def _normalize_paper(raw: dict[str, Any]) -> dict[str, Any]:
    """规范化文献字段,保证写库元数据结构稳定。"""
    status = raw.get("status", UNREAD_STATUS)
    if status not in VALID_STATUSES:
        status = UNREAD_STATUS
    return {
        "title": str(raw.get("title", "")).strip(),
        "authors": str(raw.get("authors", "")).strip(),
        "abstract": str(raw.get("abstract", "")).strip(),
        "link": str(raw.get("link", "")).strip(),
        "tags": str(raw.get("tags", "")).strip(),
        "status": status,
        "note": str(raw.get("note", "")).strip(),
        "created_at": str(raw.get("created_at", _now_iso())),
    }


class SeekdbStore:
    """基于 seekdb 嵌入式模式的文献存储与检索封装(进程内全局仅一个数据目录)。"""

    def __init__(self, db_dir: str, database: str = "papertrail", collection_name: str = "papers"):
        self.db_dir = os.path.abspath(db_dir)
        self.database = database
        self.collection_name = collection_name
        self._init_lock = threading.Lock()
        self._op_lock = threading.RLock()  # 可重入:部分方法内部会嵌套调用带锁方法
        self._client: Optional[pyseekdb.BaseClient] = None
        self._collection: Optional[pyseekdb.Collection] = None

    # ---------------- 初始化 ----------------

    def ensure_ready(self) -> None:
        """幂等地完成数据库与集合初始化(可重复调用,含短暂重试)。

        seekdb 嵌入式引擎在进程启动/退出时存在短暂的锁冲突窗口,
        初始化失败时自动重试,避免应用重启后立即启动失败。
        """
        if self._collection is not None:
            return
        with self._init_lock:
            if self._collection is not None:
                return
            os.makedirs(self.db_dir, exist_ok=True)
            self._cleanup_stale_runtime()
            last_error: Optional[Exception] = None
            for attempt in range(INIT_MAX_ATTEMPTS):
                try:
                    self._init_once()
                    return
                except Exception as exc:  # noqa: BLE001 引擎启动窗口期,重试即可
                    last_error = exc
                    logger.warning("seekdb 初始化第 %s 次失败: %s", attempt + 1, exc)
                    time.sleep(INIT_RETRY_INTERVAL)
            raise RuntimeError(f"seekdb 初始化失败(已重试 {INIT_MAX_ATTEMPTS} 次): {last_error}") from last_error

    def _cleanup_stale_runtime(self) -> None:
        """清理上次进程遗留的 seekdb 运行时文件,避免嵌入式引擎启动失败。

        仅当 pid 文件中记录的进程已不存在时清理;若引擎仍在运行(同机其他进程),
        则保留文件,由上层重试机制处理。
        """
        run_dir = os.path.join(self.db_dir, "run")
        pid_file = os.path.join(run_dir, "seekdb.pid")
        if not os.path.isfile(pid_file):
            return
        alive = False
        try:
            with open(pid_file, encoding="utf-8") as fp:
                pid = int(fp.read().strip())
            os.kill(pid, 0)  # 仅探测进程是否存在
            alive = True
        except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
            alive = False
        if alive:
            return
        for name in ("seekdb.pid", "seekdb.startup", "seekdb.clients"):
            try:
                os.remove(os.path.join(run_dir, name))
            except FileNotFoundError:
                pass
        logger.info("已清理 seekdb 残留运行时文件: %s", run_dir)

    def _init_once(self) -> None:
        admin = pyseekdb.AdminClient(path=self.db_dir)
        existing_dbs = {db.name for db in admin.list_databases()}
        if self.database not in existing_dbs:
            admin.create_database(self.database)
            logger.info("已创建 seekdb 数据库: %s", self.database)

        client = pyseekdb.Client(path=self.db_dir, database=self.database)
        existing_collections = {c.name for c in client.list_collections()}
        if self.collection_name in existing_collections:
            # embedding_function=None 显式声明:本应用不依赖嵌入模型,向量仅占位
            collection = client.get_collection(self.collection_name, embedding_function=None)
            logger.info("已打开 seekdb 集合: %s", self.collection_name)
        else:
            collection = client.create_collection(self.collection_name, embedding_function=None)
            logger.info("已创建 seekdb 集合: %s", self.collection_name)

        self._client = client
        self._collection = collection

    @property
    def collection(self) -> pyseekdb.Collection:
        self.ensure_ready()
        assert self._collection is not None
        return self._collection

    # ---------------- 基础读写 ----------------

    @staticmethod
    def _placeholder_embedding() -> list[float]:
        return [0.0] * VECTOR_DIM

    def add_paper(self, raw: dict[str, Any], paper_id: Optional[str] = None) -> dict[str, Any]:
        paper = _normalize_paper(raw)
        paper_id = paper_id or uuid.uuid4().hex
        document = _build_document(paper)
        with self._op_lock:
            col = self.collection
            col.add(
                ids=paper_id,
                embeddings=self._placeholder_embedding(),
                documents=document,
                metadatas=paper,
            )
            self._refresh_and_wait(paper_id, paper["title"])
        result = dict(paper)
        result["id"] = paper_id
        return result

    def get_paper(self, paper_id: str) -> Optional[dict[str, Any]]:
        with self._op_lock:
            res = self.collection.get(ids=paper_id, include=["documents", "metadatas"])
        if not res.get("ids"):
            return None
        paper = dict(res["metadatas"][0])
        paper["id"] = res["ids"][0]
        return paper

    def update_paper(self, paper_id: str, raw: dict[str, Any]) -> Optional[dict[str, Any]]:
        with self._op_lock:
            existing = self.get_paper(paper_id)
            if existing is None:
                return None
            merged = {**existing, **{k: v for k, v in raw.items() if k != "id"}}
            merged.pop("id", None)
            paper = _normalize_paper(merged)
            self.collection.update(
                ids=paper_id,
                embeddings=self._placeholder_embedding(),
                documents=_build_document(paper),
                metadatas=paper,
            )
            self._refresh_and_wait(paper_id, paper["title"])
        result = dict(paper)
        result["id"] = paper_id
        return result

    def delete_paper(self, paper_id: str) -> bool:
        with self._op_lock:
            if self.get_paper(paper_id) is None:
                return False
            self.collection.delete(ids=paper_id)
            self.collection.refresh_index()
        return True

    # ---------------- 检索 ----------------

    @staticmethod
    def _build_where_document(keyword: Optional[str]) -> Optional[dict[str, Any]]:
        """关键词检索条件:全文索引匹配($contains)与子串匹配($regex)取并集。

        seekdb 全文检索在查询含停用字时会失效并误命中(实测行为),
        因此 $contains 使用剔除停用字后的关键词,同时 $regex 对原始关键词
        做精确子串匹配作为兜底。
        """
        if not keyword:
            return None
        keyword = keyword.strip()
        if not keyword:
            return None
        cleaned = _clean_keyword_for_fulltext(keyword)
        if cleaned:
            return {"$or": [{"$contains": cleaned}, {"$regex": re.escape(keyword)}]}
        return {"$regex": re.escape(keyword)}

    def search_papers(
        self,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """按关键词(标题/摘要/笔记等)与阅读状态检索文献,数据来自 seekdb 真实查询。"""
        where_document = self._build_where_document(keyword)
        where = None
        if status in VALID_STATUSES:
            where = {"status": status}
        with self._op_lock:
            res = self.collection.get(
                where_document=where_document,
                where=where,
                limit=limit,
                offset=offset,
                include=["documents", "metadatas"],
            )
        papers = []
        for i, pid in enumerate(res.get("ids", [])):
            paper = dict(res.get("metadatas", [{}])[i] or {})
            paper["id"] = pid
            papers.append(paper)
        papers.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return papers

    def list_all(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        return self.search_papers(keyword=None, status=status, limit=10000, offset=0)

    # ---------------- 统计 ----------------

    def stats(self) -> dict[str, Any]:
        with self._op_lock:
            total = self.collection.count()
            read_ids = self.collection.get(where={"status": READ_STATUS}, limit=10000).get("ids", [])
            unread_ids = self.collection.get(where={"status": UNREAD_STATUS}, limit=10000).get("ids", [])
        recent = self.search_papers(limit=5, offset=0)
        return {
            "total": total,
            "read": len(read_ids),
            "unread": len(unread_ids),
            "recent": recent,
        }

    # ---------------- 演示数据(幂等) ----------------

    def seed_demo_data(self) -> int:
        """写入 3 条演示文献,固定 ID 幂等 upsert,重复执行不产生重复数据。"""
        inserted = 0
        with self._op_lock:
            for demo in DEMO_PAPERS:
                paper_id = demo["id"]
                exists = self.get_paper(paper_id) is not None
                paper = _normalize_paper(demo)
                self.collection.upsert(
                    ids=paper_id,
                    embeddings=self._placeholder_embedding(),
                    documents=_build_document(paper),
                    metadatas=paper,
                )
                if not exists:
                    inserted += 1
            self._refresh_and_wait(DEMO_PAPERS[0]["id"], DEMO_PAPERS[0]["title"])
        logger.info("演示数据初始化完成,新增 %s 条", inserted)
        return inserted

    # ---------------- 内部工具 ----------------

    def _refresh_and_wait(self, paper_id: str, title: str) -> None:
        """刷新索引并轮询等待全文索引可见(seekdb 索引异步构建)。"""
        self.collection.refresh_index()
        marker = title.strip()[:12] or " "
        deadline = time.monotonic() + INDEX_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(INDEX_WAIT_INTERVAL)
            try:
                res = self.collection.get(
                    where_document={"$regex": re.escape(marker)}, limit=10000
                )
            except Exception:  # noqa: BLE001 索引构建中的临时错误直接重试
                continue
            if paper_id in res.get("ids", []):
                return
        logger.warning("全文索引在 %ss 内未就绪(文献 %s 已持久化,稍后可检索)", INDEX_WAIT_TIMEOUT, paper_id)
