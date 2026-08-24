# -*- coding: utf-8 -*-
"""主链路节点 — retrieval / reranker / evidence / decision / response / guard。"""

import asyncio
import logging

from app.core.config import DEFAULT_TOP_K, ENABLE_MULTI_QUERY
from app.decision.rules import BRAND_ALIASES, PARENT_SUB_MAP
from app.decision.scoring import DecisionScoring
from app.model_gateway.gateway import get_model_gateway
from app.prompts.response_prompt import CHITCHAT_PROMPT, build_response_prompt
from app.repositories import get_product_repo
from app.rerank.blender import blend_rank_score
from app.retrieval.llm_evaluator import LlmEvaluator
from app.retrieval.text_retriever import TextRetriever
from app.schemas.agent_state import AgentState, BudgetSchema
from app.schemas.product import Product
from app.verification.response_guard import ResponseGuard

logger = logging.getLogger(__name__)

_repo = get_product_repo()
_text = TextRetriever(_repo)
_gateway = get_model_gateway()
_scorer = DecisionScoring()
_llm_evaluator = LlmEvaluator()
_guard = ResponseGuard()

_CHUNK_TO_SOURCE = {
    "summary": "text_retrieval",
    "mkt": "text_retrieval",
    "faq": "policy_faq",
    "rev": "review_positive",
}
_REQUIRED_EVIDENCE = {
    "search": {"text_retrieval"},
    "narrow": {"text_retrieval"},
    "direct_answer": {"text_retrieval", "policy_faq"},
    "scene_search": {"text_retrieval"},
    "shop_action": set(),
    "chitchat": set(),
}


# ================================================================
# Retrieval Node
# ================================================================

async def retrieval_node(state: AgentState) -> AgentState:
    """多路检索：chunk 语义召回 + 硬过滤（category/sub_category/price/brand/exclusions）。"""
    slots = state.slots
    budget = slots.budget or BudgetSchema()
    query = state.rewritten_query or state.user_input

    # P5: Multi-Query 多路变体召回（sub/brand/spec/must_tags 组合），默认开启
    if ENABLE_MULTI_QUERY:
        variants = _build_query_variants(state, query)
    else:
        variants = [query]
    if len(variants) > 1:
        results = await _multi_search(variants, state)
    else:
        results = await _text.search_chunked(
            query,
            top_k=DEFAULT_TOP_K,
            category=slots.category,
            sub_category=slots.sub_category,
            price_max=budget.max,
            price_min=budget.min,
            candidate_ids=state.candidate_ids or None,
        )

    # 排除硬过滤（"不要耐克" / "除了X"）
    exclusions = slots.exclusions or []
    if exclusions and results:
        before = len(results)
        results = [
            p for p in results
            if not any(
                t.lower() in (p.get("title", "") + p.get("brand", "")).lower()
                for t in exclusions
            )
        ]
        if len(results) < before:
            logger.info("Hard-excluded %d products: %s", before - len(results), exclusions)

    # 品牌硬过滤（"我想买耐克" → 只保留 Nike，别名双向展开；过滤空则回退原序）
    brand = slots.brand
    if brand and results:
        variants = _brand_variants(brand)
        filtered = [p for p in results if _brand_matches(p, variants)]
        if filtered:
            if len(filtered) < len(results):
                logger.info("Brand hard-filter: %d -> %d (brand=%s)", len(results), len(filtered), brand)
            results = filtered
        else:
            # 品牌不可得：区分"不在库" vs "在库但该品类无货"，回复层走诚实话术
            exists = _brand_exists(brand)
            subs = [slots.sub_category] if slots.sub_category else _parent_sub_categories(query)
            siblings = _brand_siblings_for_subs(subs, brand)
            state.brand_unavailable = {
                "brand": brand,
                "reason": "brand_not_in_catalog" if not exists else "brand_no_match",
                "siblings": siblings,
                "sub_categories": subs,
            }
            logger.info(
                "Brand unavailable: %s (%s), siblings=%s",
                brand, state.brand_unavailable["reason"], siblings,
            )
            state.ranked_items = []
            state.evidence_list = []
            return state

    # 父级词 → 子类硬过滤（"耐克的鞋子"防上衣混入；sub 已精确时跳过）
    if not slots.sub_category and results:
        parent_subs = _parent_sub_categories(query)
        if parent_subs:
            filtered = [p for p in results if p.get("sub_category") in parent_subs]
            if filtered:
                if len(filtered) < len(results):
                    logger.info(
                        "Parent-term sub filter %s: %d -> %d",
                        parent_subs, len(results), len(filtered),
                    )
                results = filtered
            else:
                logger.info("Parent-term sub filter emptied results, keep order")

    state.ranked_items = results
    state.evidence_list = _build_evidence(results)
    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "Retrieval Node",
        "action": "multi_channel_recall",
        "input_summary": query[:60],
        "output_summary": f"candidates={len(results)}, evidence={len(state.evidence_list)}",
        "latency_ms": 0,
        "status": "success" if results else "empty",
    })
    return state


def _build_query_variants(state: AgentState, base: str) -> list[str]:
    """P5：基于槽位构造检索变体（原句 + 槽位补全），增强泛查询召回。"""
    slots = state.slots
    parts: list[str] = []
    if slots.sub_category and slots.sub_category not in base:
        parts.append(slots.sub_category)
    if slots.brand and slots.brand.lower() not in base.lower():
        parts.append(slots.brand)
    for kw in (slots.spec_keywords or [])[:3]:
        if kw and kw not in base:
            parts.append(kw)
    for tag in (slots.must_tags or [])[:3]:
        if tag and tag not in base:
            parts.append(tag)
    variants = [base]
    if parts:
        variants.append(f"{base} {' '.join(parts)}")
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out[:3]


async def _multi_search(variants: list[str], state: AgentState) -> list[dict]:
    """P5：多路召回后按 product_id 合并，保留最高分。"""
    slots = state.slots
    budget = slots.budget or BudgetSchema()
    merged: dict[str, dict] = {}
    for v in variants:
        hits = await _text.search_chunked(
            v,
            top_k=DEFAULT_TOP_K,
            category=slots.category,
            sub_category=slots.sub_category,
            price_max=budget.max,
            price_min=budget.min,
            candidate_ids=state.candidate_ids or None,
        )
        for p in hits:
            pid = p.get("product_id")
            if not pid:
                continue
            score = float(p.get("score") or 0.0)
            cur = merged.get(pid)
            if cur is None or score > float(cur.get("score") or 0.0):
                merged[pid] = p
    ranked = sorted(merged.values(), key=lambda p: float(p.get("score") or 0.0), reverse=True)
    return ranked[:DEFAULT_TOP_K]


def _brand_variants(brand: str) -> set[str]:
    """品牌别名双向展开：Nike → {nike, 耐克}；耐克 → {耐克, nike}。"""
    b = str(brand).strip().lower()
    variants = {b}
    alias = BRAND_ALIASES.get(b)
    if alias:
        variants.add(str(alias).lower())
    return variants


def _brand_matches(p: dict, variants: set[str]) -> bool:
    pb = str(p.get("brand") or "").lower()
    if not pb:
        return False
    return any(v and (v in pb or pb in v) for v in variants)


def _brand_exists(brand: str) -> bool:
    """品牌是否在商品库中存在（别名感知：Nike/耐克 都算）。"""
    variants = _brand_variants(brand)
    try:
        for p in _repo.list_all():
            pb = (p.brand or "").lower()
            if any(v and (v in pb or pb in v) for v in variants):
                return True
    except Exception:
        pass
    return False


def _brand_siblings_for_subs(subs: list[str], excluded_brand: str | None) -> list[str]:
    """指定子类集合下、排除指定品牌后的其他品牌（取前 3）。"""
    if not subs:
        return []
    try:
        products = _repo.list_all()
    except Exception:
        return []
    excluded = _brand_variants(excluded_brand) if excluded_brand else set()
    seen: list[str] = []
    for p in products:
        if getattr(p, "sub_category", None) not in subs:
            continue
        b = (p.brand or "").strip()
        if not b:
            continue
        pb = b.lower()
        if any(v and (v in pb or pb in v) for v in excluded):
            continue
        if b not in seen:
            seen.append(b)
        if len(seen) >= 3:
            break
    return seen


def _parent_sub_categories(query: str) -> list[str]:
    q = query.lower()
    for term in sorted(PARENT_SUB_MAP, key=len, reverse=True):
        if term.lower() in q:
            return PARENT_SUB_MAP[term]
    return []


def _build_evidence(items: list[dict]) -> list[dict]:
    evidence: list[dict] = []
    for item in items:
        pid = item.get("product_id", "")
        seen = set()
        for c in (item.get("matched_chunks") or []):
            ctype = c.get("chunk_type", "")
            payload = c.get("payload") or {}
            text_c = (payload.get("text") or "")[:300]
            if not text_c:
                continue
            stype = _CHUNK_TO_SOURCE.get(ctype, "text_retrieval")
            if ctype == "rev":
                try:
                    rating = int(payload.get("rating") or 5)
                except (TypeError, ValueError):
                    rating = 5
                stype = "review_positive" if rating >= 4 else "review_risk"
            key = (pid, stype, text_c[:40])
            if key in seen:
                continue
            seen.add(key)
            evidence.append({
                "product_id": pid,
                "source_type": stype,
                "content": text_c,
                "score": c.get("score", 0),
            })

        # rag_knowledge 兜底（chunk 缺失时也能给证据）
        rk = item.get("rag_knowledge") or {}
        if isinstance(rk, dict):
            for faq in (rk.get("official_faq") or [])[:2]:
                if not isinstance(faq, dict):
                    continue
                evidence.append({
                    "product_id": pid,
                    "source_type": "policy_faq",
                    "content": f"Q:{faq.get('question', '')} A:{faq.get('answer', '')}"[:300],
                })
            for rev in (rk.get("user_reviews") or [])[:3]:
                if not isinstance(rev, dict):
                    continue
                rating = rev.get("rating", 5)
                stype = "review_positive" if rating >= 4 else "review_risk"
                evidence.append({
                    "product_id": pid,
                    "source_type": stype,
                    "content": (rev.get("content") or "")[:300],
                })
    return evidence


# ================================================================
# Reranker Node（含 D4 性价比混合）
# ================================================================

async def reranker_node(state: AgentState) -> AgentState:
    items = state.ranked_items
    if not items:
        return state
    query = state.rewritten_query or state.user_input

    if len(items) <= 1:
        for p in items:
            p["reranker_score"] = min(1.0, 0.68 + 0.38 * float(p.get("score") or 0.0))
            blend_rank_score(p, query)
        return state

    docs = [_doc_for(p) for p in items]
    try:
        ranked = await _gateway.rerank(query, docs, top_n=len(items))
        index_map = {
            r["index"]: min(1.0, 0.68 + 0.38 * float(r.get("relevance_score", 0)))
            for r in ranked
        }
    except Exception as e:
        logger.warning("Reranker unavailable, fallback to raw scores: %s", e)
        index_map = {
            i: min(1.0, 0.68 + 0.38 * float(p.get("score") or 0.5))
            for i, p in enumerate(items)
        }

    for i, p in enumerate(items):
        p["reranker_score"] = round(index_map.get(i, float(p.get("score") or 0.0)), 4)
        blend_rank_score(p, query)

    state.ranked_items = sorted(items, key=lambda p: p.get("rank_score", 0), reverse=True)
    # scene_search 发散增强：按子类轮转去重，Top-N 跨类目（不再清一色防晒）
    if state.intent == "scene_search":
        state.ranked_items = _diversify_by_subcategory(state.ranked_items)
    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "Reranker Node (D4 Blend)",
        "action": "semantic_rerank_with_value",
        "input_summary": f"{len(items)} candidates",
        "output_summary": f"top3 rank: {[round(p.get('rank_score', 0), 3) for p in state.ranked_items[:3]]}",
        "latency_ms": 0,
        "status": "success",
    })
    return state


def _diversify_by_subcategory(items: list[dict]) -> list[dict]:
    """按子类轮转去重：每个子类先取最优，再轮流补齐，保证跨类目发散。"""
    groups: dict[str, list[dict]] = {}
    for p in items:
        groups.setdefault(p.get("sub_category") or "其他", []).append(p)
    result: list[dict] = []
    total = len(items)
    while len(result) < total and groups:
        for sub in list(groups.keys()):
            if groups[sub]:
                result.append(groups[sub].pop(0))
            if not groups[sub]:
                del groups[sub]
            if len(result) >= total:
                break
    return result


def _doc_for(p: dict) -> str:
    parts = [p.get("title", ""), p.get("category", ""), p.get("sub_category", "")]
    desc = p.get("description", "")
    if desc:
        parts.append(str(desc)[:300])
    rk = p.get("rag_knowledge") or {}
    if isinstance(rk, dict):
        mkt = rk.get("marketing_description", "")
        if mkt:
            parts.append(str(mkt)[:300])
        for faq in (rk.get("official_faq") or [])[:2]:
            if isinstance(faq, dict):
                parts.append(f"Q:{faq.get('question', '')[:150]} A:{faq.get('answer', '')[:300]}")
        for rev in (rk.get("user_reviews") or [])[:2]:
            if isinstance(rev, dict):
                parts.append(f"用户评价: {str(rev.get('content', ''))[:200]}")
    return " ".join(str(x) for x in parts if x)


# ================================================================
# Evidence Check Node
# ================================================================

def evidence_check_node(state: AgentState) -> AgentState:
    types = {e.get("source_type") for e in state.evidence_list}
    required = _REQUIRED_EVIDENCE.get(state.intent, {"text_retrieval"})
    missing = sorted(required - types)
    state.evidence_sufficiency = not missing
    state.sufficiency_report = {
        "sufficient": state.evidence_sufficiency,
        "missing_types": missing,
        "total_evidence": len(state.evidence_list),
    }
    return state


# ================================================================
# Decision Node（D5：固定 Top-3）
# ================================================================

async def decision_node(state: AgentState) -> AgentState:
    items = state.ranked_items
    if not items:
        state.decision_results = []
        return state

    slots = state.slots
    query = state.rewritten_query or state.user_input
    preferred = (state.user_profile.brands if state.user_profile else None) or []
    results = []

    # P6: LLM 证据评估（精排后二次校验，默认关闭，动态读取配置便于运行时开关）
    llm_eval = {"evaluations": [], "overall_analysis": "", "user_warnings": []}
    from app.core.config import DECISION_LLM_TIMEOUT, ENABLE_DECISION_LLM
    if ENABLE_DECISION_LLM and items:
        try:
            llm_eval = await asyncio.wait_for(
                _llm_evaluator.evaluate(
                    query=query,
                    constraints={
                        "category": slots.category,
                        "sub_category": slots.sub_category,
                        "budget_max": slots.budget.max,
                        "budget_min": slots.budget.min,
                        "scenario": slots.scene,
                    },
                    candidates=items,
                    top_n=5,
                ),
                timeout=DECISION_LLM_TIMEOUT,
            )
        except Exception as e:
            logger.debug("LLM evaluator skipped: %s", e)
    eval_map = {ev.get("product_id", ""): ev for ev in llm_eval.get("evaluations", [])}
    state.llm_overall_analysis = llm_eval.get("overall_analysis", "")
    state.llm_user_warnings = llm_eval.get("user_warnings", [])

    for item in items:
        product = _to_product(item)
        if product is None:
            continue
        rel = float(item.get("rank_score") or item.get("score") or 0.0)
        ev = eval_map.get(item.get("product_id", ""), {})
        decision = _scorer.score_with_evidence(
            product=product,
            query=query,
            keyword_score=rel,
            budget_max=slots.budget.max,
            scenario=slots.scene,
            spec_keywords=slots.spec_keywords,
            preferred_brands=preferred,
            force_rag_relevance=rel,
            relevance_source="rerank_blend",
            llm_relevance=ev.get("relevance", 0.0),
            llm_reasoning=ev.get("reasoning", ""),
            llm_verdict=ev.get("verdict", ""),
            llm_strengths=ev.get("strengths", []),
            llm_risks=ev.get("risks", []),
            evidence_metrics=None,
            global_evidence_sufficient=state.evidence_sufficiency,
        )
        results.append(decision.model_dump())

    results.sort(key=lambda r: r["final_score"], reverse=True)
    state.decision_results = results[:3]  # D5: 固定推荐 Top-3
    state.decision_score = results[0]["final_score"] if results else 0.0

    # 7维定序：把 ranked_items 同步为 final_score 顺序（回复/展示与评分完全一致）
    order = {r["product_id"]: i for i, r in enumerate(results)}
    state.ranked_items = sorted(
        state.ranked_items,
        key=lambda p: order.get(p.get("product_id", ""), 999),
    )
    return state


def _to_product(item: dict) -> Product | None:
    try:
        return Product(
            product_id=item.get("product_id", ""),
            title=item.get("title", ""),
            brand=item.get("brand", ""),
            category=item.get("category", ""),
            sub_category=item.get("sub_category", ""),
            base_price=float(item.get("price", 0)),
            image_path=(item.get("image_urls") or [""])[0],
            skus=item.get("skus") or [],
            rag_knowledge=item.get("rag_knowledge") or {},
        )
    except Exception as e:
        logger.warning("Product reconstruction failed: %s", e)
        return None


# ================================================================
# Response Node（P7 / P8 + 模板兜底）
# ================================================================

async def response_node(state: AgentState) -> AgentState:
    if state.defer_response:
        return state
    if state.intent == "chitchat" and not state.ranked_items:
        state.final_response = await _chitchat_answer(state.user_input)
        return state
    direct_answer = _direct_response(state)
    if direct_answer:
        state.final_response = direct_answer
        return state

    top = state.ranked_items[:3]
    prompt = build_response_prompt(top, state.slots.spec_keywords)
    answer = ""
    try:
        answer = await asyncio.wait_for(_gateway.chat("chat_generation", prompt), timeout=6.0)
        answer = (answer or "").strip()
        if len(answer) < 10 or not _cites_products(answer, top):
            answer = _template_answer(top)
    except Exception:
        answer = _template_answer(top)

    state.final_response = answer
    # 库存不足提示：结果 <3 时如实说明，并给同类子类下的其他品牌名（不给商品信息）
    if 0 < len(top) < 3:
        hint = _low_stock_hint(top, state.slots.brand)
        if hint and "商品库" not in answer:
            state.final_response = f"{state.final_response}\n\n{hint}"
    return state


def _direct_response(state: AgentState) -> str:
    """无需模型生成的诚实回复，供普通与 SSE 回复链路共用。"""
    if state.brand_unavailable:
        bu = state.brand_unavailable
        brand = bu.get("brand") or ""
        subs = bu.get("sub_categories") or []
        siblings = bu.get("siblings") or []
        sub_text = "、".join(subs) if subs else "相关商品"
        if siblings:
            names = "、".join(siblings)
            return (
                f"抱歉，商品库里暂时没有 {brand} 的{sub_text}；"
                f"我们目前有 {names} 等品牌的{sub_text}，要不要看看其他品牌？"
            )
        return f"抱歉，商品库里暂时没有 {brand} 的{sub_text}，要不要换个品牌或品类试试？"
    if not state.ranked_items:
        return "抱歉，没有找到匹配的商品～要不要换个关键词，或者放宽预算试试？"
    return ""


async def stream_response(state: AgentState):
    """真实转发模型 token；结束后把完整回答写回 state。"""
    if state.intent == "chitchat" and not state.ranked_items:
        prompt = CHITCHAT_PROMPT.format(query=state.user_input)
        top: list[dict] = []
    else:
        direct_answer = _direct_response(state)
        if direct_answer:
            state.final_response = direct_answer
            yield direct_answer
            return
        top = state.ranked_items[:3]
        prompt = build_response_prompt(top, state.slots.spec_keywords)

    fragments: list[str] = []
    try:
        async for token in _gateway.chat_stream("chat_generation", prompt):
            fragments.append(token)
            yield token
    except Exception:
        state.final_response = _template_answer(top) if top else "抱歉，暂时无法回答你的问题。"
        return

    answer = "".join(fragments).strip()
    if top and (len(answer) < 10 or not _cites_products(answer, top)):
        answer = _template_answer(top)
    elif not answer:
        answer = "抱歉，暂时无法回答你的问题。"
    if 0 < len(top) < 3:
        hint = _low_stock_hint(top, state.slots.brand)
        if hint and "商品库" not in answer:
            answer = f"{answer}\n\n{hint}"
    state.final_response = answer


async def _chitchat_answer(query: str) -> str:
    try:
        answer = await asyncio.wait_for(
            _gateway.chat("chat_generation", CHITCHAT_PROMPT.format(query=query)),
            timeout=6.0,
        )
        if answer and len(answer.strip()) >= 5:
            return answer.strip()
    except Exception:
        pass
    return _chitchat_fallback(query)


def _chitchat_fallback(query: str) -> str:
    q = query.lower().strip()
    if any(w in q for w in ("你好", "嗨", "哈喽", "hello", "hi", "在吗")):
        return "嗨！我是豆仔，你的智能购物导购助手~ 想买什么？直接告诉我就好，还能拍照识图哦！"
    if any(w in q for w in ("你是谁", "你叫什么", "介绍你自己")):
        return "我是豆仔，字节跳动旗下的智能购物导购助手，豆包的弟弟！专精商品推荐、截图分析和对比评测～"
    if any(w in q for w in ("你能做什么", "你会什么", "功能")):
        return "我能帮你：\n🔍 根据需求推荐商品\n📊 对比分析\n🛒 直接加购下单\n想试试哪个？"
    if any(w in q for w in ("谢谢", "感谢", "多谢")):
        return "不客气~ 随时找我，购物愉快！"
    if any(w in q for w in ("拜拜", "再见", "晚安")):
        return "再见！逛累了随时来找我，豆仔随时在线~"
    return "嘿嘿，豆仔在呢！想买点什么？告诉我品类、预算或者使用场景都行～"


def _template_answer(top: list[dict]) -> str:
    lines = ["豆仔帮你挑好了～"]
    for i, p in enumerate(top, 1):
        price = p.get("price")
        price_str = f"¥{int(price)}" if price else "价格见详情"
        lines.append(
            f"{i}. {p.get('title', '')}（{p.get('brand', '')}）{price_str}："
            "适合你当前的选购需求，可作为重点对比对象。"
        )
    lines.append("想了解哪款的更多细节，或者换个条件，随时告诉我～")
    return "\n".join(lines)


def _cites_products(answer: str, top: list[dict]) -> bool:
    if not answer or not top:
        return False

    for p in top:
        title = str(p.get("title") or "").strip()
        price = p.get("price")
        if not title or title not in answer:
            return False
        if isinstance(price, (int, float)):
            price_tokens = {f"¥{price:g}", f"{price:g}元"}
            if not any(token in answer for token in price_tokens):
                return False
    return True


def _low_stock_hint(top: list[dict], current_brand: str | None) -> str:
    """库存不足提示：只给同类子类下的其他品牌名，不编造具体商品/价格。"""
    subs = sorted({p.get("sub_category") for p in top if p.get("sub_category")})
    n = len(top)
    if not subs:
        return f"目前商品库中符合条件的有 {n} 款，想看看其他选择也可以，告诉豆仔就行～"
    sub_text = "、".join(subs)
    siblings = _sibling_brands(top, current_brand)
    if siblings:
        names = "、".join(siblings)
        return (
            f"目前商品库中符合条件的有 {n} 款；我们还有 {names} 等品牌的{sub_text}，"
            "不过豆仔先不展开具体商品，想看其他品牌的话告诉我～"
        )
    return f"目前商品库中符合条件的有 {n} 款{sub_text}，想看看其他品牌也可以，告诉豆仔就行～"


def _sibling_brands(top: list[dict], current_brand: str | None) -> list[str]:
    """同子类下的其他品牌（按数据集实际品牌取前 3 个）。

    排除集合 = 已展示商品的品牌（含别名）+ 当前槽位 brand——提示里不能再带出已推荐过的品牌。
    """
    subs = {p.get("sub_category") for p in top if p.get("sub_category")}
    if not subs:
        return []
    try:
        products = _repo.list_all()
    except Exception:
        return []
    excluded: set[str] = set()
    for p in top:
        b = p.get("brand")
        if b:
            excluded |= _brand_variants(b)
    if current_brand:
        excluded |= _brand_variants(current_brand)
    seen: list[str] = []
    for p in products:
        if getattr(p, "sub_category", None) not in subs:
            continue
        b = (p.brand or "").strip()
        if not b:
            continue
        pb = b.lower()
        if any(v and (v in pb or pb in v) for v in excluded):
            continue
        if b not in seen:
            seen.append(b)
        if len(seen) >= 3:
            break
    return seen


# ================================================================
# Guard Node
# ================================================================

def guard_node(state: AgentState) -> AgentState:
    if not state.defer_response:
        _guard.check(state)
    return state
