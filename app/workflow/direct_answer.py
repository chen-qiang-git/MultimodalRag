# -*- coding: utf-8 -*-
"""P9：direct_answer 直答节点 — 基于商品 FAQ / 评价证据直接回答用户追问。

场景："刚才那款能带上飞机吗" / "这个支持快充吗" / "这款防水吗"
流程：resolved_product_id → 取商品 → 收集 FAQ/评价证据 → P9 LLM 直答
      → 未引用证据则回退"最匹配 FAQ 原答" → 无匹配则诚实告知
      → 无 resolved_product_id → 追问"你指的是哪一款"
"""

import asyncio
import logging

from app.model_gateway.gateway import get_model_gateway
from app.prompts.direct_answer_prompt import build_direct_answer_prompt
from app.repositories import get_product_repo
from app.schemas.agent_state import AgentState

logger = logging.getLogger(__name__)

_repo = get_product_repo()
_gateway = get_model_gateway()

_OVERVIEW_HINTS = (
    "介绍一下", "介绍下", "说一下", "说说", "第一个", "第二个", "第三个",
    "这一款", "这款怎么样", "怎么样", "是什么", "卖点", "特点",
    "适合什么人", "适合谁", "好不好", "值不值得", "值得买",
)


async def direct_answer_node(state: AgentState) -> AgentState:
    slots = state.slots
    pid = (
        slots.resolved_product_id
        or slots.rule_resolved_product_id
        or (state.candidate_ids[0] if state.candidate_ids else None)
    )

    if not pid:
        # 隐性指代兜底：快照里上一轮讨论的商品（last_products[0]）
        last_products = (state.context_snapshot or {}).get("last_products") or []
        if last_products and isinstance(last_products[0], dict):
            pid = last_products[0].get("product_id")
    if not pid:
        state.final_response = "你指的是哪一款呀？告诉豆仔是刚才推荐的第几个，或者直接说商品名～"
        return state

    product = None
    try:
        product = _repo.get_by_id(pid)
    except Exception as e:
        logger.warning("direct_answer: get_by_id failed: %s", e)

    if product is None:
        state.final_response = "抱歉，豆仔没找到你问的那款商品，要不要换个说法试试？"
        return state

    faqs = _faq_list(product)
    reviews = _review_list(product)
    mkt = (product.rag_knowledge.marketing_description or "") if product.rag_knowledge else ""

    state.evidence_list = [
        {"product_id": pid, "source_type": "policy_faq", "content": f"Q:{q} A:{a}"}
        for q, a in faqs
    ]

    prompt = build_direct_answer_prompt(state.user_input, product, faqs, reviews, mkt)
    answer = ""
    try:
        answer = await asyncio.wait_for(_gateway.chat("chat_generation", prompt), timeout=6.0)
        answer = (answer or "").strip()
        if len(answer) < 5 or not _cites_evidence(answer, product, faqs, mkt):
            answer = _fallback_answer(state.user_input, product, faqs, reviews, mkt)
    except Exception:
        answer = _fallback_answer(state.user_input, product, faqs, reviews, mkt)

    state.final_response = answer
    # 把回答过的商品置顶为"当前讨论商品"（供下一轮隐性指代 & 记忆层使用）
    state.ranked_items = [{
        "product_id": pid,
        "title": product.title,
        "brand": product.brand,
        "price": product.base_price,
    }]
    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "Direct Answer Node (P9)",
        "action": "faq_grounded_answer",
        "input_summary": state.user_input[:60],
        "output_summary": f"product={pid}, faqs={len(faqs)}",
        "latency_ms": 0,
        "status": "success",
    })
    return state


# ================================================================
# 内部工具
# ================================================================

def _faq_list(product):
    if not product.rag_knowledge:
        return []
    return [(f.question or "", f.answer or "") for f in product.rag_knowledge.official_faq]


def _review_list(product):
    if not product.rag_knowledge:
        return []
    return [
        (r.nickname or "", r.rating or 0, r.content or "")
        for r in product.rag_knowledge.user_reviews
    ]


def _cites_evidence(answer: str, product, faqs: list[tuple[str, str]], mkt: str = "") -> bool:
    brand = product.brand or ""
    title = product.title or ""
    if brand and len(brand) >= 2 and brand in answer:
        return True
    for window in (4, 3):
        for i in range(len(title) - window + 1):
            sub = title[i:i + window].strip()
            if len(sub) >= 2 and sub in answer:
                return True
    # FAQ 答案片段命中（8 字连续文本）
    for _q, a in faqs:
        for i in range(len(a) - 7):
            if a[i:i + 8] in answer:
                return True
    # 营销描述片段（概览回答常引用描述原文）
    for i in range(len(mkt) - 7):
        if mkt[i:i + 8] in answer:
            return True
    return False


def _fallback_answer(
    question: str, product, faqs: list[tuple[str, str]],
    reviews: list[tuple[str, int, str]], mkt: str,
) -> str:
    best: tuple[str, str] | None = None
    best_score = 0
    for q, a in faqs:
        score = _bigram_overlap(question, q)
        if score > best_score:
            best = (q, a)
            best_score = score
    if best and best_score >= 2:
        q, a = best
        return f"关于「{q}」：{a}"
    if _is_overview_request(question):
        return _overview_answer(product, mkt, reviews)
    return "豆仔查了商品资料，没找到这个信息，建议到商品详情页确认一下～"


def _is_overview_request(question: str) -> bool:
    q = question.lower()
    return any(h in q for h in _OVERVIEW_HINTS)


def _overview_answer(product, mkt: str, reviews: list[tuple[str, int, str]]) -> str:
    title = product.title or ""
    brand = product.brand or ""
    price = product.base_price
    price_str = f"¥{int(price)}" if price else "价格见详情"
    parts = [f"{title}（{brand}），{price_str}"]
    if mkt:
        parts.append(mkt[:100])
    if reviews:
        good = sum(1 for _nick, rating, _c in reviews if rating >= 4)
        parts.append(f"用户评价整体不错，好评 {good}/{len(reviews)} 条～")
    parts.append("想了解更具体的细节（比如能不能上飞机、支持什么协议），随时问豆仔～")
    return "。".join(parts)


def _bigram_overlap(a: str, b: str) -> int:
    a_low, b_low = a.lower(), b.lower()
    grams_a = {a_low[i:i + 2] for i in range(len(a_low) - 1) if a_low[i:i + 2].strip()}
    grams_b = {b_low[i:i + 2] for i in range(len(b_low) - 1) if b_low[i:i + 2].strip()}
    return len(grams_a & grams_b)
