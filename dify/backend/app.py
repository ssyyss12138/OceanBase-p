import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .pc import MemoryUnavailable, PowerContext
from .service import Game
from .store import Store

load_dotenv()


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player_code: str = Field(min_length=12, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    turn_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    query: str = Field(min_length=1, max_length=1200)


class Commit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player_code: str = Field(min_length=12, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    turn_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    action: Literal["browse", "buy_courage", "buy_echo", "tell_joke", "confess", "settle", "recall"]


def create_app(game=None, api_key=None):
    key = api_key or os.environ.get("GAME_API_KEY", "")
    if len(key) < 24:
        raise RuntimeError("请设置至少 24 位 GAME_API_KEY")
    if game is None:
        secret = os.environ.get("PLAYER_HASH_SECRET", "")
        if len(secret) < 24:
            raise RuntimeError("请设置稳定的 PLAYER_HASH_SECRET（至少 24 位）")
        path = Path(os.environ.get("GAME_DB", ".runtime/game.sqlite"))
        game = Game(
            Store(path),
            PowerContext(
                os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000"),
                os.environ.get("POWERCONTEXT_TOKEN", ""),
            ),
            secret,
        )

    @asynccontextmanager
    async def lifespan(app):
        yield
        game.pc.client.close()

    app = FastAPI(title="因果便利店", version="1.0.0", lifespan=lifespan)

    def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(401, "工作流服务鉴权失败")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "causal-corner-store"}

    @app.post("/v1/turns/prepare", dependencies=[Depends(authorize)])
    def prepare(body: Prepare):
        if not body.query.strip():
            raise HTTPException(422, "请输入你想做什么")
        try:
            return game.prepare(body.player_code, body.turn_id, body.query.strip())
        except MemoryUnavailable:
            raise HTTPException(503, "记忆服务暂不可用，本轮尚未结算，请稍后重试") from None

    @app.post("/v1/turns/commit", dependencies=[Depends(authorize)])
    def commit(body: Commit):
        try:
            return game.commit(body.player_code, body.turn_id, body.action)
        except MemoryUnavailable:
            raise HTTPException(503, "记忆写入尚未确认，已保留待恢复记录；请稍后重试") from None

    return app
