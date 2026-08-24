# -*- coding: utf-8 -*-
"""Web 测试壳 — FastAPI 应用：静态测试页 + SSE 接口。

启动：python run.py            # 127.0.0.1:8007
浏览器打开 http://127.0.0.1:8007/
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.agent_actions import router as agent_actions_router
from app.api.address import router as address_router
from app.api.auth import router as auth_router
from app.api.cart import router as cart_router
from app.api.checkout import router as checkout_router
from app.api.conversation import router as conversation_router
from app.api.health import router as health_router
from app.api.memories import router as memories_router
from app.api.preference import router as preference_router
from app.api.products import router as products_router
from app.api.stream import router as stream_router
from app.api.upload import router as upload_router
from app.api.user_profile import router as user_profile_router
from app.api.voice import router as voice_router

app = FastAPI(title="Rewrite-RAG Web 测试台")
app.include_router(stream_router)
app.include_router(health_router)
app.include_router(memories_router)
app.include_router(products_router)
app.include_router(upload_router)
app.include_router(cart_router)
app.include_router(checkout_router)
app.include_router(agent_actions_router)
app.include_router(auth_router)
app.include_router(address_router)
app.include_router(preference_router)
app.include_router(voice_router)
app.include_router(conversation_router)
app.include_router(user_profile_router)

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


@app.get("/", include_in_schema=False)
async def index():
    """根路径返回 Web 测试页（页面内联 CSS/JS，无外部资源依赖）。"""
    return FileResponse(str(_STATIC_DIR / "dialogue_test.html"))
