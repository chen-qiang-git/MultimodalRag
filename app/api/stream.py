# -*- coding: utf-8 -*-
"""Android-compatible persisted SSE recommendation endpoint."""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.schemas.agent_state import AgentState
from app.schemas.cart import DEMO_USER_ID
from app.services.conversation_service import get_conversation_service
from app.workflow.graph import run_agent


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recommend", tags=["stream"])


class StreamRequest(BaseModel):
    session_id: str = ""
    user_id: str = ""
    conversation_id: str = ""
    message: str = ""
    image_url: str | None = None
    voice_url: str | None = None
    mode: str = "normal_recommend"
    target_product_id: str | None = None
    allow_same_category_comparison: bool = False
    fast_mode: bool = False


class RecommendRequest(BaseModel):
    user_query: str
    image_url: str | None = None
    demo_mode: bool = False
    session_id: str = ""
    user_id: str = ""
    conversation_id: str = ""


class GuideRequest(BaseModel):
    user_query: str
    session_id: str = ""
    user_id: str = ""
    conversation_id: str = ""
    category: str = ""
    sub_category: str = ""
    concern: str = ""
    budget_max: float | None = None
    budget_min: float | None = None
    round_num: int = 0


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _chat_history(messages: list[dict]) -> list[dict[str, str]]:
    """Convert persisted role messages to the governor's turn-pair format."""
    turns: list[dict[str, str]] = []
    pending_user = ""
    for message in messages:
        role = message.get("role", "")
        content = message.get("content", "")
        if role == "user":
            if pending_user:
                turns.append({"user": pending_user, "assistant": ""})
            pending_user = content
        elif role == "assistant":
            turns.append({"user": pending_user, "assistant": content})
            pending_user = ""
    if pending_user:
        turns.append({"user": pending_user, "assistant": ""})
    return turns[-30:]


def _products_payload(state: AgentState) -> list[dict]:
    return [
        {
            "product_id": product.get("product_id", ""),
            "title": product.get("title", ""),
            "brand": product.get("brand", ""),
            "price": product.get("price", 0),
            "image_url": product.get("image_url", ""),
            "rank_score": product.get("rank_score"),
        }
        for product in state.ranked_items[:3]
    ]


def _snapshot_update(state: AgentState) -> dict:
    slots = state.slots
    return {
        "last_products": _products_payload(state),
        "pending_question": state.clarification_question if state.needs_clarification else None,
        "constraints": {
            "category": slots.category,
            "sub_category": slots.sub_category,
            "brand": slots.brand,
            "budget_max": slots.budget.max,
            "budget_min": slots.budget.min,
        },
        "last_query": state.user_input,
        "last_intent": state.intent,
    }


def _recommendation_payload(state: AgentState) -> dict:
    return {
        "answer": state.final_response,
        "products": _products_payload(state),
        "decision_results": state.decision_results,
        "evidence_list": state.evidence_list,
        "trace_steps": state.trace_steps,
        "harness_report": state.harness_report,
        "sufficiency_report": state.sufficiency_report,
        "constraints": _snapshot_update(state)["constraints"],
        "needs_clarification": state.needs_clarification,
        "clarification_question": state.clarification_question,
        "clarification_options": state.clarification_options,
        "shop_action": state.intent == "shop_action",
    }


async def _run_compat_recommendation(
    user_query: str,
    user_id: str,
    session_id: str,
    conversation_id: str,
    image_url: str | None = None,
) -> tuple[AgentState, str, str]:
    resolved_user_id = user_id or DEMO_USER_ID
    resolved_session_id = session_id or f"android-{uuid.uuid4().hex[:8]}"
    conversation_service = get_conversation_service()
    conversation = await conversation_service.aget_or_create(
        user_id=resolved_user_id,
        session_id=resolved_session_id,
        conversation_id=conversation_id,
        title=user_query[:40],
    )
    resolved_conversation_id = conversation["conversation_id"]
    context = await conversation_service.aget_context(resolved_conversation_id, limit=30)
    await conversation_service.aappend_user_message(
        conversation_id=resolved_conversation_id,
        user_id=resolved_user_id,
        session_id=resolved_session_id,
        content=user_query,
        image_url=image_url or "",
    )
    result = await run_agent(AgentState(
        user_input=user_query,
        user_id=resolved_user_id,
        session_id=resolved_session_id,
        conversation_id=resolved_conversation_id,
        image_url=image_url,
        chat_history=_chat_history(context["recent_messages"]),
        context_snapshot=context["context_snapshot"],
    ))
    products = _products_payload(result)
    await conversation_service.aappend_assistant_message(
        conversation_id=resolved_conversation_id,
        user_id=resolved_user_id,
        session_id=resolved_session_id,
        content=result.final_response,
        product_refs=[product["product_id"] for product in products if product["product_id"]],
        evidence_refs=result.evidence_list,
    )
    await conversation_service.aupdate_context_snapshot(
        resolved_conversation_id,
        _snapshot_update(result),
    )
    return result, resolved_session_id, resolved_conversation_id


@router.post("/v2")
async def recommend_v2(req: RecommendRequest):
    result, session_id, conversation_id = await _run_compat_recommendation(
        user_query=req.user_query,
        user_id=req.user_id,
        session_id=req.session_id,
        conversation_id=req.conversation_id,
        image_url=req.image_url,
    )
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        **_recommendation_payload(result),
    }


@router.post("/guide")
async def recommend_guide(req: GuideRequest):
    constraints = " ".join(filter(None, [
        req.category,
        req.sub_category,
        req.concern,
        f"预算{req.budget_min}-{req.budget_max}" if req.budget_min is not None or req.budget_max is not None else "",
    ]))
    result, session_id, conversation_id = await _run_compat_recommendation(
        user_query=" ".join(filter(None, [req.user_query, constraints])),
        user_id=req.user_id,
        session_id=req.session_id,
        conversation_id=req.conversation_id,
    )
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "answer": result.final_response,
        "should_recommend": not result.needs_clarification,
        "options": result.clarification_options,
        "locked_category": result.slots.category or req.category,
        "locked_sub_category": result.slots.sub_category or req.sub_category,
        "locked_concern": req.concern,
        "budget_max": result.slots.budget.max or req.budget_max,
        "budget_min": result.slots.budget.min or req.budget_min,
        "products": _products_payload(result),
        "decision_results": result.decision_results,
        "evidence_list": result.evidence_list,
        "trace_steps": result.trace_steps,
    }


@router.post("/stream")
async def recommend_stream(req: StreamRequest):
    async def gen():
        try:
            result, _, conversation_id = await _run_compat_recommendation(
                user_query=req.message,
                user_id=req.user_id,
                session_id=req.session_id,
                conversation_id=req.conversation_id,
                image_url=req.image_url,
            )
            for character in result.final_response:
                yield _sse("token", json.dumps({"text": character}, ensure_ascii=False))
                await asyncio.sleep(0)
            yield _sse("result", json.dumps({
                "answer": result.final_response,
                "products": _products_payload(result),
                "conversation_id": conversation_id,
                "governor_slots": {
                    **result.slots.model_dump(),
                    "intent": result.intent,
                    "rewritten_query": result.rewritten_query,
                    "candidate_ids": result.candidate_ids,
                    "clarification_question": result.clarification_question,
                },
            }, ensure_ascii=False))
        except Exception as exc:
            logger.exception("Android recommendation stream failed")
            yield _sse("error", json.dumps({"message": str(exc)}, ensure_ascii=False))
        finally:
            yield _sse("done", "{}")

    return StreamingResponse(gen(), media_type="text/event-stream")
