"""FastAPI 应用入口。

路由：
  POST /api/conversations/* — 持久化旅行对话
  POST /api/runs/*          — 后台规划、修改与续接
  POST /api/auth/*      — 注册 / 登录
  GET  /api/history     — 历史行程列表
  GET  /api/history/:id — 历史行程详情
  GET  /api/health      — 健康检查
  GET  /api/config      — 前端高德 JS Key
  GET  /                — 前端首页
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.database import init_db
from app.core.env import load_local_env
from app.api.auth_routes import router as auth_router
from app.api.history_routes import router as history_router
from app.api.profile_routes import router as profile_router
from app.api.plan_routes import router as plan_router
from app.api.sweep_routes import router as sweep_router
from app.api.runtime_routes import router as runtime_router
from app.runtime.container import start_runtime, stop_runtime

load_local_env()
init_db()

# ─── 应用 ────────────────────────────────────────────────────

app = FastAPI(title="AI 旅游规划助手", version="0.1.0")

app.include_router(auth_router)
app.include_router(history_router)
app.include_router(profile_router)
app.include_router(plan_router)
app.include_router(sweep_router)
app.include_router(runtime_router)


@app.on_event("startup")
async def start_agent_runtime():
    await start_runtime()


@app.on_event("shutdown")
async def stop_agent_runtime():
    await stop_runtime()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    """暴露前端地图所需的高德 JS API 密钥（不含敏感 REST Key）。"""
    return {
        "amap_js_key":           os.getenv("AMAP_JS_KEY", ""),
        "amap_js_security_code": os.getenv("AMAP_JS_SECURITY_CODE", ""),
    }
# ─── 静态文件（前端）—— 必须在 API 路由之后挂载 ─────────────

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _frontend_index_response() -> FileResponse:
    # 前端没有构建产物指纹；HTML 必须每次取新版本，否则它会继续引用旧的
    # ChatState/pages 脚本并静默丢弃服务端新增的 PlanningBrief 字段。
    return FileResponse(
        _FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


@app.get("/")
def index():
    return _frontend_index_response()


@app.get("/history")
def history_page():
    return _frontend_index_response()


@app.get("/profile")
def profile_page():
    return _frontend_index_response()


app.mount("/", RevalidatingStaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")
