# -*- coding: utf-8 -*-
"""确定性预消解（规则层）— 移植自旧 dialogue_governor 阶段2。

职责：在 LLM 调用前用正则解决"可确定"的指代与条件，作为 LLM 提示 + 校验。
高置信度字段（绝对预算 / resolved_product_id / 显式购物意图）冲突时规则优先。
"""

import re

from app.decision.rules import BRAND_ALIASES, detect_category, detect_sub_category
from app.governor.budget_governor import detect_budget_range

# ---- 指代模式 ----
_ORDINAL_PATTERN = re.compile(r"第\s*([一二三四五六七八九十12345１２３４５])\s*[个款种]")
_CN_NUM = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4,
    "六": 5, "七": 6, "八": 7, "九": 8, "十": 9,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4,
    "１": 0, "２": 1, "３": 2, "４": 3, "５": 4,
    "第一": 0, "第二": 1, "第三": 2, "第四": 3, "第五": 4,
}
_LAST_REF_PATTERN = re.compile(
    r"(刚才|上次|上一).{0,3}[个款种些]|"
    r"[这那]个[东西]|"
    r"前面.{0,3}[个款种]|"
    r"^.{0,2}(它|这个|那个)"
)
_CART_PATTERN = re.compile(r"(加入购物车|加购|加进购物车|买了|下单|结算)")

# P0-C: 排除语义（不要X / 除了X）— 优先于指代消解
_EXCLUSION_PATTERN = re.compile(
    r"(?:不要|别要|别买|除了|不含|排除|不想买|不要买)\s*([^\s，。,！？!?]{1,12})"
)

# ---- 全新查询 / 条件更新 / 硬重置 判定词表 ----
_CONDITION_WORDS = (
    "以内", "以上", "以下", "之间", "左右", "上下", "前后", "预算",
    "便宜", "贵一点", "贵些", "放宽", "放款到", "放到", "提高", "升到",
    "换成", "对比", "别的", "另一个", "换个",
)
_CONTINUATION_WORDS = ("再", "又", "还有", "另外", "继续", "更", "点", "些")
_RESET_TRIGGERS = ("换个话题", "换话题", "重新推荐", "重新开始", "不想买", "不买了", "算了不")
_NEED_WORDS = ("推荐", "想要", "想买", "给我", "找", "来一个", "来款", "买")

_AFFIRMATIVE = {
    "要", "好", "行", "可以", "对", "是的", "嗯", "买", "要的",
    "好的", "行的", "对啊", "是", "要买", "想看", "想买", "看看吧",
}


def build_history_text(chat_history: list[dict], max_turns: int = 30) -> str:
    """D7：chat_history 原文直塞（最近 max_turns 轮）。"""
    if not chat_history:
        return ""
    recent = chat_history[-max_turns:]
    lines = []
    for i, turn in enumerate(recent, 1):
        u = (turn.get("user") or turn.get("query") or "").strip()
        a = (turn.get("assistant") or turn.get("answer") or "").strip()
        if u:
            lines.append(f"[{i}] 用户：{u}")
        if a:
            lines.append(f"[{i}] 豆仔：{a}")
    return "\n".join(lines)


def extract_exclusion(query: str) -> str | None:
    m = _EXCLUSION_PATTERN.search(query)
    if not m:
        return None
    return re.sub(r"[了的地吧]$", "", m.group(1).strip())


def is_hard_reset(query: str) -> bool:
    return any(t in query for t in _RESET_TRIGGERS)


def is_condition_update(query: str) -> bool:
    return any(w in query for w in _CONDITION_WORDS)


def is_continuation(query: str) -> bool:
    if re.match(r"^(再|又|还|另外|继续|再来)", query):
        return True
    return any(w in query for w in ("换个", "别的", "还有", "另一个"))


def _detect_noun(query: str) -> bool:
    if detect_category(query) or detect_sub_category(query):
        return True
    q = query.lower()
    return any(alias and alias.lower() in q for alias in BRAND_ALIASES)


def is_fresh_query(query: str) -> bool:
    """全新完整需求 → 不继承历史预算。"""
    if not query or is_condition_update(query) or is_continuation(query):
        return False
    if not any(w in query for w in _NEED_WORDS):
        return False
    return _detect_noun(query)


def rule_budget_carryover(query: str, snapshot: dict) -> str:
    """规则兜底预算继承：reset 触发词 > 显式价格 > 延续词 > 品类切换 > 默认 inherit。"""
    if is_hard_reset(query):
        return "reset"
    if detect_budget_range(query).get("kind") != "none":
        return "inherit"
    if is_continuation(query):
        return "inherit"
    cur_cat = detect_category(query)
    acc_cat = (snapshot.get("constraints") or {}).get("category")
    if cur_cat and acc_cat and cur_cat != acc_cat:
        return "reset"
    return "inherit"


def pre_resolve(query: str, snapshot: dict, fresh: bool = False) -> dict:
    """阶段2：确定性预消解，输出高置信度规则字段。"""
    pre = {
        "resolved_product_id": None,
        "budget_max": None,
        "budget_min": None,
        "budget_kind": None,
        "budget_raw": None,
        "exclude_hint": None,
        "shop_action": False,
        "route_hint": None,
    }

    last_products = snapshot.get("last_products") or []
    last_product_list = [p for p in last_products if isinstance(p, dict)]

    pre["exclude_hint"] = extract_exclusion(query)

    # P0-C: "不要耐克" 是排除，不是引用 → 命中排除则不做引用消解
    if not fresh and not pre["exclude_hint"]:
        m = _ORDINAL_PATTERN.search(query)
        if m and last_product_list:
            idx = _CN_NUM.get(m.group(1))
            if idx is not None and idx < len(last_product_list):
                pre["resolved_product_id"] = last_product_list[idx].get("product_id")

        if not pre["resolved_product_id"] and _LAST_REF_PATTERN.search(query) and last_product_list:
            pre["resolved_product_id"] = last_product_list[0].get("product_id")

        if not pre["resolved_product_id"] and last_product_list:
            ql = query.lower()
            for p in last_product_list:
                brand = (p.get("brand") or "").lower()
                title = (p.get("title") or "").lower()
                if brand and brand in ql:
                    pre["resolved_product_id"] = p.get("product_id")
                    break
                if title and len(title) >= 2 and title[:6] in ql:
                    pre["resolved_product_id"] = p.get("product_id")
                    break

    # 绝对/模糊/单边预算（规则覆盖 LLM）
    try:
        info = detect_budget_range(query)
        pre["budget_min"] = info.get("min")
        pre["budget_max"] = info.get("max")
        pre["budget_kind"] = info.get("kind")
        pre["budget_raw"] = info.get("raw")
    except Exception:
        pass

    if _CART_PATTERN.search(query):
        pre["shop_action"] = True
        pre["route_hint"] = "shop_action"

    return pre


def is_affirmative(query: str) -> bool:
    return query.strip() in _AFFIRMATIVE
