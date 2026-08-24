# -*- coding: utf-8 -*-
"""D4：性价比混合分 — 让高性价比商品在检索后重排中拿到更高名次。"""

from app.core.config import RERANK_RELEVANCE_FLOOR, RERANK_VALUE_WEIGHT
from app.decision.scoring import CATEGORY_BENCHMARKS

PREMIUM_SIGNALS = (
    "旗舰", "顶配", "高端", "最强", "最贵", "最新", "性能最强",
    "豪华", "顶配版", "至尊",
)


def is_premium_intent(query: str) -> bool:
    """Premium 意图保护：命中旗舰/高端等信号时关闭性价比混合。"""
    q = (query or "").lower()
    return any(s in q for s in PREMIUM_SIGNALS)


def value_score_from_item(item: dict) -> float:
    """性价比分（与 DecisionScoring._calc_value_score 同公式，从检索 dict 计算）。"""
    try:
        price = float(item.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return 0.65

    sub = item.get("sub_category") or ""
    median, quality_mult = CATEGORY_BENCHMARKS.get(sub, (price, 1.0))

    rk = item.get("rag_knowledge") or {}
    reviews = rk.get("user_reviews") or [] if isinstance(rk, dict) else []
    ratings = [r.get("rating", 0) for r in reviews if isinstance(r, dict) and r.get("rating")]
    if ratings:
        quality = 0.55 + 0.45 * (sum(ratings) / len(ratings)) / 5.0
    else:
        quality = 0.65

    if price <= median * 0.5:
        price_score = 0.95
    elif price <= median * 0.8:
        price_score = 0.88
    elif price <= median:
        price_score = 0.82
    elif price <= median * 1.5:
        price_score = 0.72
    else:
        price_score = 0.58

    value = quality_mult * (0.5 * quality + 0.5 * price_score)
    return min(1.0, max(0.3, value))


def blend_rank_score(item: dict, query: str, w_value: float | None = None) -> float:
    """计算 rank_score = (1-w)×reranker_score + w×value_score，并写回 item。

    保护规则：
      - reranker_score < RERANK_RELEVANCE_FLOOR 不参与混合（防低相关便宜货上浮）
      - premium 意图 w=0（防便宜机型顶掉旗舰诉求）
    """
    rel = float(item.get("reranker_score") or 0.0)
    val = value_score_from_item(item)
    item["value_score"] = round(val, 4)

    w = 0.0 if is_premium_intent(query) else (RERANK_VALUE_WEIGHT if w_value is None else w_value)
    if rel >= RERANK_RELEVANCE_FLOOR and w > 0:
        rank = (1 - w) * rel + w * val
    else:
        rank = rel
    item["rank_score"] = round(rank, 4)
    return item["rank_score"]
