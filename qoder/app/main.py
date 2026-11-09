"""PaperTrail 科研文献检索工作台 — FastAPI 后端入口.

运行方式(在 qoder/ 目录下):
    .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

数据由 seekdb 嵌入式模式持久化在 qoder/data/seekdb.db/ 目录中。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .seekdb_store import VALID_STATUSES, SeekdbStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("papertrail")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = os.environ.get("PAPERTRAIL_DATA_DIR", str(BASE_DIR / "data" / "seekdb.db"))
STATIC_DIR = Path(__file__).resolve().parent / "static"

store = SeekdbStore(db_dir=DATA_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.ensure_ready()
    # 首次启动自动写入 3 条演示文献(幂等,不重复)
    store.seed_demo_data()
    logger.info("PaperTrail 已就绪,数据目录: %s", DATA_DIR)
    yield


app = FastAPI(title="PaperTrail 科研文献检索工作台", lifespan=lifespan)


# ---------------- 请求模型 ----------------

class PaperIn(BaseModel):
    title: str = Field(..., min_length=1, description="标题")
    authors: str = ""
    abstract: str = ""
    link: str = ""
    tags: str = ""
    status: str = "unread"
    note: str = ""


class PaperUpdate(BaseModel):
    title: Optional[str] = None
    authors: Optional[str] = None
    abstract: Optional[str] = None
    link: Optional[str] = None
    tags: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None


# ---------------- 接口 ----------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "database": store.database, "collection": store.collection_name}


@app.get("/api/stats")
def get_stats() -> dict[str, Any]:
    """工作台统计:文献总数、已读、未读、最近添加。"""
    return store.stats()


@app.get("/api/papers")
def list_papers(
    q: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """文献列表与检索:q 为关键词(标题/摘要/笔记),status 为阅读状态筛选。"""
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status 仅支持 {VALID_STATUSES}")
    papers = store.search_papers(keyword=q, status=status, limit=limit, offset=offset)
    return {"total": len(papers), "items": papers, "query": {"q": q, "status": status}}


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str) -> dict[str, Any]:
    paper = store.get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")
    return paper


@app.post("/api/papers", status_code=201)
def create_paper(payload: PaperIn) -> dict[str, Any]:
    if payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status 仅支持 {VALID_STATUSES}")
    return store.add_paper(payload.model_dump())


@app.put("/api/papers/{paper_id}")
def update_paper(paper_id: str, payload: PaperUpdate) -> dict[str, Any]:
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status 仅支持 {VALID_STATUSES}")
    paper = store.update_paper(paper_id, fields)
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")
    return paper


@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: str) -> dict[str, Any]:
    if not store.delete_paper(paper_id):
        raise HTTPException(status_code=404, detail="文献不存在")
    return {"ok": True, "id": paper_id}


@app.post("/api/init")
def init_demo_data() -> dict[str, Any]:
    """幂等写入 3 条演示文献。"""
    inserted = store.seed_demo_data()
    return {"ok": True, "inserted": inserted}


# ---------------- 静态页面 ----------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
