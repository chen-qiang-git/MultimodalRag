# -*- coding: utf-8 -*-
"""Web 测试用 SSE 接口 — 包装新版 Agent 主图（P9 已接入）。

注意：会话记忆为进程内内存（重启即清空），仅用于 Web 联调；
PG 会话持久化 / Android 契约适配属于 D6 API 兼容层的正式工作。
"""

import json
import logging
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.schemas.agent_state import AgentState
from app.workflow.graph import run_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommend", tags=["web-test"])

_sessions: dict[str, dict] = {}


class StreamRequest(BaseModel):
    session_id: str = ""
    conversation_id: str = ""
    message: str = ""
    fast_mode: bool = False  # 契约兼容：新版图暂未实现 fast 分支


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _get_memory(session_id: str) -> dict:
    return _sessions.setdefault(session_id, {"chat_history": [], "snapshot": {}})


async def _run_turn(req: StreamRequest) -> tuple[AgentState, str]:
    sid = req.session_id or ("web-" + uuid.uuid4().hex[:8])
    cid = req.conversation_id or ("web-cid-" + uuid.uuid4().hex[:8])
    mem = _get_memory(sid)

    state = AgentState(
        user_input=req.message,
        session_id=sid,
        conversation_id=cid,
        chat_history=list(mem["chat_history"]),
        context_snapshot=dict(mem["snapshot"]),
    )
    result = await run_agent(state)

    # ---- 回合记忆更新（多轮 / P9 指代依赖这里）----
    mem["chat_history"].append({"user": req.message, "assistant": result.final_response})
    mem["chat_history"] = mem["chat_history"][-20:]

    snap = mem["snapshot"]
    if result.ranked_items:
        new_last = [
            {
                "product_id": p.get("product_id", ""),
                "title": p.get("title", ""),
                "brand": p.get("brand", ""),
                "price": p.get("price", 0),
            }
            for p in result.ranked_items[:10]
        ]
        if result.intent == "direct_answer":
            # 直答商品置顶为当前引用，保留原列表供序数指代
            new_ids = {p["product_id"] for p in new_last}
            rest = [p for p in snap.get("last_products", []) if p["product_id"] not in new_ids]
            snap["last_products"] = new_last + rest[:9]
        else:
            snap["last_products"] = new_last
    snap["pending_question"] = (
        result.clarification_question if result.needs_clarification else None
    )
    slots = result.slots
    snap["constraints"] = {
        "category": slots.category,
        "sub_category": slots.sub_category,
        "brand": slots.brand,
        "budget_max": slots.budget.max if slots.budget else None,
        "budget_min": slots.budget.min if slots.budget else None,
    }
    snap["last_query"] = req.message
    return result, cid


def _products_payload(state: AgentState) -> list[dict]:
    out = []
    for p in state.ranked_items[:3]:
        out.append({
            "product_id": p.get("product_id", ""),
            "title": p.get("title", ""),
            "brand": p.get("brand", ""),
            "price": p.get("price", 0),
            "rank_score": p.get("rank_score"),
        })
    return out


def _slots_payload(state: AgentState) -> dict:
    data = state.slots.model_dump()
    data["intent"] = state.intent
    data["rewritten_query"] = state.rewritten_query
    data["candidate_ids"] = state.candidate_ids
    data["clarification_question"] = state.clarification_question
    return data


@router.post("/stream")
async def recommend_stream(req: StreamRequest):
    async def gen():
        try:
            result, cid = await _run_turn(req)
            payload = {
                "answer": result.final_response,
                "products": _products_payload(result),
                "governor_slots": _slots_payload(result),
                "conversation_id": cid,
            }
            yield _sse("result", json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.exception("web stream failed")
            payload = {
                "answer": f"❌ 服务异常：{e}",
                "products": [],
                "governor_slots": None,
                "conversation_id": req.conversation_id,
            }
            yield _sse("result", json.dumps(payload, ensure_ascii=False))

    return StreamingResponse(gen(), media_type="text/event-stream")
