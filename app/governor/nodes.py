# -*- coding: utf-8 -*-
"""Governor 子图节点 — 前置"大脑"。

子图：
  START → compile（P1：改写 + 意图 + 槽位，一次 LLM）→ validate（规则层校验/治理）→ END

澄清（P3）不在此子图内：由主图路由到 clarification_node（写入 final_response 后直达 END）。
"""

import asyncio
import json
import logging
import re

from langgraph.graph import END, StateGraph

from app.core.cache import cached, make_key
from app.core.config import REDIS_CACHE_TTL_REWRITE
from app.decision.rules import (
    BRAND_ALIASES,
    detect_category,
    detect_scenario,
    detect_sub_category,
    get_canonical_sub_categories,
)
from app.governor import preresolve
from app.governor.budget_governor import (
    detect_budget_range,
    normalize_slots,
    strip_budget_expr,
)
from app.governor.profile import maybe_extract_profile
from app.model_gateway.gateway import get_model_gateway
from app.prompts.clarification_prompt import build_clarification_prompt
from app.prompts.governor_prompt import build_governor_prompt
from app.repositories import get_product_repo
from app.repositories.user_preference_repo import get_user_preference_repo
from app.schemas.agent_state import AgentState, BudgetSchema, SlotSchema, UserProfileSchema
from app.services.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

_pending_profile_tasks: set = set()

_VALID_CATEGORIES = {"数码电子", "美妆护肤", "服饰运动", "食品饮料"}
_SCENE_SPEC_KEYWORDS = {
    "travel": ["防晒", "旅行", "帽子", "短袖T恤", "运动短裤", "背包", "移动电源", "真无线耳机", "跑步鞋", "零食"],
    "outdoor": ["防晒", "户外", "徒步鞋", "背包", "帽子", "运动短裤", "速干T恤", "功能饮料", "跑步鞋"],
    "flight": ["便携", "移动电源", "充电器", "真无线耳机", "背包"],
    "business_trip": ["便携", "笔记本电脑", "真无线耳机", "充电器", "平板电脑", "咖啡"],
    "commute": ["便携", "真无线耳机", "移动电源", "跑步鞋", "平板电脑"],
    "running": ["跑步鞋", "速干T恤", "运动短裤", "运动长裤", "功能饮料", "真无线耳机"],
    "fitness": ["速干T恤", "运动短裤", "瑜伽裤", "运动长裤", "功能饮料"],
    "sport": ["速干T恤", "运动短裤", "运动长裤", "跑步鞋", "功能饮料"],
    "desk": ["真无线耳机", "笔记本电脑", "平板电脑", "充电器", "咖啡", "坚果/零食"],
    "gaming": ["真无线耳机", "智能手机", "充电器", "功能饮料", "坚果/零食"],
    "music": ["真无线耳机"],
    "gift": ["精华", "面霜", "面膜", "唇釉", "粉底液", "化妆水"],
    "skincare": ["面霜", "精华", "化妆水", "面膜", "防晒"],
}


# ================================================================
# 阶段3：槽位编译（P1 一次 LLM + 规则兜底）
# ================================================================

async def rewrite_extract_node(state: AgentState) -> AgentState:
    """compile 节点：上下文直塞 → 预消解 → P1 编译 → 确定性治理。"""
    snapshot = dict(state.context_snapshot or {})
    user_query = state.user_input

    # P11：加载用户长期画像（懒加载，供本轮回填避雷/偏好）
    if state.user_id and state.user_profile is None:
        try:
            state.user_profile = await _load_user_profile(state.user_id)
        except Exception as e:
            logger.debug("profile load skipped: %s", e)

    # 硬重置 → 清空历史约束
    if preresolve.is_hard_reset(user_query):
        snapshot = {}

    fresh = preresolve.is_fresh_query(user_query)

    # pending_question 问答链拦截（D5 决策记录，沿用旧设计）
    pending_q = snapshot.get("pending_question") or ""
    query_for_resolve = user_query
    if pending_q and preresolve.is_affirmative(user_query):
        query_for_resolve = pending_q

    # 阶段2：确定性预消解
    pre = preresolve.pre_resolve(query_for_resolve, snapshot, fresh=fresh)

    # 阶段3：P1 槽位编译（一次 LLM）
    slots = await _compile_slots(state, query_for_resolve, user_query, snapshot, pre, fresh)

    # P0-B: sub_category 确定性兜底（LLM 缺失/非规范 → 规则映射 → 父级词转 spec_keyword）
    sub, parent_kw = _resolve_sub_category(user_query, slots.category, slots.sub_category)
    slots.sub_category = sub
    if parent_kw and parent_kw not in slots.spec_keywords:
        slots.spec_keywords.append(parent_kw)

    # 累积预算注入 + 继承策略（D1/D6）
    acc = snapshot.get("constraints") or {}
    carry = slots.budget_carryover or "inherit"
    slots.acc_budget_max = None if carry == "reset" else acc.get("budget_max")
    slots.acc_budget_min = None if carry == "reset" else acc.get("budget_min")
    if (
        carry == "inherit"
        and pre.get("budget_kind") in (None, "none")
        and slots.budget.max is None
        and slots.acc_budget_max
    ):
        slots.budget.max = slots.acc_budget_max
        acc_min = acc.get("budget_min")
        if acc_min is not None:
            slots.budget.min = acc_min

    # 条件更新/排除消息 → 强制继承上一轮上下文（防"不要小米"丢失品类/预算/品牌）
    is_condition_msg = (
        (not fresh)
        and (
            bool(pre.get("exclude_hint"))
            or preresolve.is_condition_update(user_query)
            or preresolve.is_continuation(user_query)
        )
    )
    if is_condition_msg:
        slots.budget_carryover = "inherit"
        slots.acc_budget_max = acc.get("budget_max")
        if slots.budget.max is None and slots.acc_budget_max:
            slots.budget.max = slots.acc_budget_max
            if acc.get("budget_min") is not None:
                slots.budget.min = acc.get("budget_min")
        if not slots.category:
            slots.category = acc.get("category")
        if not slots.sub_category:
            slots.sub_category = acc.get("sub_category")
        acc_brand = acc.get("brand")
        if pre.get("exclude_hint"):
            # 三分支："不要耐克，我要阿迪达斯"（显式新品牌）> "不要小米"（继承）> null
            explicit_brand = _detect_explicit_brand(user_query, pre["exclude_hint"])
            if explicit_brand:
                if slots.brand != explicit_brand:
                    logger.info("exclusion msg: brand -> %s", explicit_brand)
                slots.brand = explicit_brand
            else:
                if slots.brand and slots.brand != acc_brand:
                    logger.info("exclusion msg: clear invented brand %r", slots.brand)
                slots.brand = acc_brand
            # 重写补全："不要小米" → "不要小米的蓝牙耳机"；有显式新品牌则前置
            if slots.rewritten_query == user_query:
                last_query = snapshot.get("last_query") or ""
                base = strip_budget_expr(last_query) if last_query else ""
                rebuilt = ""
                if explicit_brand:
                    rebuilt = explicit_brand + (f"的{base}" if base else "")
                    rebuilt += f"，不要{pre['exclude_hint']}"
                elif base:
                    rebuilt = f"不要{pre['exclude_hint']}的{base}"
                budget_txt = _acc_budget_text(acc)
                if rebuilt and budget_txt:
                    rebuilt = f"{rebuilt}，{budget_txt}"
                if rebuilt:
                    slots.rewritten_query = rebuilt

    # 相对修饰词锚点价：上一轮展示的首个商品价（"更贵/更便宜"的参照物）
    try:
        last_products = snapshot.get("last_products") or []
        if last_products and isinstance(last_products[0], dict):
            anchor = float(last_products[0].get("price") or 0)
            slots.acc_anchor_price = anchor if anchor > 0 else None
    except Exception:
        slots.acc_anchor_price = None

    # 阶段4：条件治理（BudgetGovernor — D1 不允许 LLM 做算术）
    slots = normalize_slots(slots)

    # 全新完整需求 → 强制 search + 回填规则品类
    if fresh:
        slots.intent = "search"
        if not slots.category:
            slots.category = detect_category(user_query)

    # 阶段5：意图路由 + 守卫
    slots = _route_intent(slots, pre)

    # scene_search 跨品类发散
    if slots.intent == "scene_search":
        slots = _apply_scene_search(slots)

    # 澄清确定性计算（规则覆盖 LLM 的过度谨慎/自信）
    slots = _resolve_clarification(slots)

    # 隐性指代兜底：direct_answer 无显式指代时，默认指上一轮讨论的商品（last_products[0]）
    if (
        slots.intent == "direct_answer"
        and not slots.resolved_product_id
        and not slots.rule_resolved_product_id
    ):
        last_products = snapshot.get("last_products") or []
        if last_products and isinstance(last_products[0], dict):
            slots.rule_resolved_product_id = last_products[0].get("product_id")

    # ---- 写入 State ----
    # P11：长期避雷并入排除过滤（从画像加载/合并后生效）
    if state.user_profile and state.user_profile.avoid_tags:
        for tag in state.user_profile.avoid_tags:
            if tag not in slots.exclusions:
                slots.exclusions.append(tag)
    state.slots = slots
    state.intent = slots.intent
    state.rewritten_query = slots.rewritten_query or user_query
    state.needs_clarification = slots.needs_clarification

    # narrow / direct_answer 候选集锁定
    if slots.intent == "narrow" and slots.resolved_product_id:
        state.candidate_ids = _narrow_candidate_ids(slots, snapshot)
    elif slots.intent == "direct_answer" and slots.resolved_product_id:
        state.candidate_ids = [slots.resolved_product_id]
    elif slots.intent == "direct_answer" and slots.rule_resolved_product_id:
        state.candidate_ids = [slots.rule_resolved_product_id]

    # D8/P11：画像规则门（零 LLM 成本）→ 立即合并规则抽取结果
    profile = maybe_extract_profile(user_query, len(state.chat_history))
    if profile:
        state.user_profile = _merge_profile(state.user_profile, profile)
    # P11：长期信号 → 异步 LLM 抽取 + 落库（不阻塞主链路）
    if state.user_id:
        svc = UserProfileService()
        if svc.has_long_term_signal(user_query) or len(state.chat_history) > 5:
            _spawn_profile_task(_persist_profile_async(state.user_id, user_query))

    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "DialogueGovernor",
        "action": "rewrite_and_extract",
        "input_summary": user_query[:60],
        "output_summary": (
            f"intent={slots.intent}, cat={slots.category or '-'}, "
            f"sub={slots.sub_category or '-'}, budget={slots.budget.max or '-'}"
        ),
        "latency_ms": 0,
        "status": "success",
    })
    return state


def validate_node(state: AgentState) -> AgentState:
    """validate 节点：白名单强校验 + 品牌归一化（防幻觉核心，代码层兜底）。"""
    slots = state.slots
    canonical = get_canonical_sub_categories()
    if slots.sub_category and slots.sub_category not in canonical:
        logger.info("sub_category 白名单校验失败，置空: %s", slots.sub_category)
        slots.sub_category = None
    if slots.brand:
        slots.brand = _normalize_brand(slots.brand)
    # 预算数值合法性：min <= max
    if slots.budget.min is not None and slots.budget.max is not None:
        if slots.budget.min > slots.budget.max:
            slots.budget.min = slots.budget.max
    state.slots = slots
    state.intent = slots.intent
    state.rewritten_query = slots.rewritten_query or state.rewritten_query
    return state


# ================================================================
# P3：澄清节点（数据库驱动，D2）
# ================================================================

async def clarification_node(state: AgentState) -> AgentState:
    """追问节点：读 PG 真实子类列表 → P3 生成豆仔风格选择题 → 写入 final_response。"""
    category = state.slots.category or detect_category(state.user_input)
    subs: list[str] = []
    if category:
        try:
            subs = get_product_repo().get_sub_categories(category)
        except Exception as e:
            logger.warning("get_sub_categories failed: %s", e)
            subs = []

    if not subs:
        answer = "你想找哪类商品呢？告诉我品类或品牌，我帮你精准推荐～"
    else:
        prompt = build_clarification_prompt(category, subs)
        try:
            gw = get_model_gateway()
            raw = (await gw.chat("chat_generation", prompt) or "").strip()
            answer = raw if len(raw) >= 5 else _clarification_fallback(category, subs)
        except Exception:
            answer = _clarification_fallback(category, subs)

    state.clarification_question = answer
    state.final_response = answer
    state.needs_clarification = True
    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "Clarification Node",
        "action": "ask_choice",
        "input_summary": category or "unknown",
        "output_summary": f"options={len(subs)}",
        "latency_ms": 0,
        "status": "success",
    })
    return state


def build_governor():
    """编译 Governor 子图。"""
    g = StateGraph(AgentState)
    g.add_node("compile", rewrite_extract_node)
    g.add_node("validate", validate_node)
    g.set_entry_point("compile")
    g.add_edge("compile", "validate")
    g.add_edge("validate", END)
    return g.compile()


# ================================================================
# 内部工具
# ================================================================

async def _compile_slots(
    state: AgentState, query: str, original_query: str,
    snapshot: dict, pre: dict, fresh: bool,
) -> SlotSchema:
    history_text = preresolve.build_history_text(state.chat_history)
    valid_subs = sorted(get_canonical_sub_categories())
    prompt = build_governor_prompt(history_text, query, pre, valid_subs)
    llm_result: dict = {}
    try:
        gw = get_model_gateway()
        raw = await gw.chat("intent_understanding", prompt)
        llm_result = _parse_llm_json(raw)
    except Exception as e:
        logger.warning("Governor LLM failed, rule fallback: %s", e)
        llm_result = {}
    return _slots_from_llm(llm_result, original_query, pre, snapshot)


def _parse_llm_json(raw: str) -> dict:
    if not raw:
        return {}
    raw = raw.strip()
    if "```" in raw:
        block = raw.split("```")[1] if len(raw.split("```")) > 1 else ""
        if block.startswith("json"):
            block = block[4:]
        raw = block.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, IndexError):
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                return {}
    return {}


def _slots_from_llm(llm: dict, query: str, pre: dict, snapshot: dict) -> SlotSchema:
    if not llm:
        return _rule_fallback_slots(query, pre, snapshot)
    budget_raw = llm.get("budget") or {}
    carry = llm.get("budget_carryover")
    if carry not in ("inherit", "reset"):
        carry = "inherit"
    return SlotSchema(
        intent=llm.get("intent") or "search",
        confidence=float(llm.get("confidence") or 0.0),
        rewritten_query=llm.get("rewritten_query") or query,
        budget_carryover=carry,
        category=_valid_category(llm.get("category")),
        sub_category=llm.get("sub_category"),
        budget=BudgetSchema(
            min=budget_raw.get("min"),
            max=budget_raw.get("max"),
            raw=budget_raw.get("raw"),
            modifier=budget_raw.get("modifier"),
        ),
        scene=llm.get("scene"),
        brand=_normalize_brand(llm.get("brand")),
        exclusions=llm.get("exclusions") or [],
        spec_keywords=llm.get("spec_keywords") or [],
        must_tags=llm.get("must_tags") or [],
        needs_clarification=bool(llm.get("needs_clarification")),
        rule_resolved_product_id=pre.get("resolved_product_id"),
        rule_budget_max=pre.get("budget_max"),
        rule_budget_min=pre.get("budget_min"),
        rule_budget_kind=pre.get("budget_kind"),
        rule_budget_raw=pre.get("budget_raw"),
        rule_shop_action=pre.get("shop_action", False),
    )


def _rule_fallback_slots(query: str, pre: dict, snapshot: dict) -> SlotSchema:
    """LLM 不可用时的规则兜底。"""
    cat = detect_category(query)
    sub = detect_sub_category(query, cat)
    budget = detect_budget_range(query)
    exclusions = []
    excl = preresolve.extract_exclusion(query)
    if excl:
        exclusions.append(excl)
    return SlotSchema(
        intent="search",
        confidence=0.5,
        rewritten_query=query,
        budget_carryover=preresolve.rule_budget_carryover(query, snapshot),
        category=cat,
        sub_category=sub,
        budget=BudgetSchema(
            min=pre.get("budget_min") or budget.get("min"),
            max=pre.get("budget_max") or budget.get("max"),
            raw=pre.get("budget_raw") or budget.get("raw"),
        ),
        scene=detect_scenario(query),
        exclusions=exclusions,
        rule_budget_max=pre.get("budget_max") or budget.get("max"),
        rule_budget_min=pre.get("budget_min") or budget.get("min"),
        rule_budget_kind=pre.get("budget_kind") or budget.get("kind"),
        rule_budget_raw=pre.get("budget_raw") or budget.get("raw"),
        rule_shop_action=pre.get("shop_action", False),
    )


def _valid_category(val) -> str | None:
    if not val:
        return None
    s = str(val)
    if s.lower() in ("null", "none", ""):
        return None
    return s if s in _VALID_CATEGORIES else None


def _normalize_brand(brand) -> str | None:
    """品牌归一化：中文别名 → 标准英文名（耐克→Nike、阿迪达斯→Adidas）。"""
    if not brand:
        return None
    s = str(brand).strip()
    if not s:
        return None
    if s.isascii():
        return s.title() if s.islower() else s
    alias = BRAND_ALIASES.get(s.lower())
    if alias and alias.isascii():
        return alias.title() if alias.islower() else alias
    return s


def _detect_explicit_brand(query: str, exclude_hint: str | None) -> str | None:
    """剔除"不要X"片段后，检测 query 中显式声明的新品牌（如"我要阿迪达斯"）。

    返回归一化品牌名；无显式品牌返回 None。防止把排除目标误当新品牌。
    """
    q = query
    if exclude_hint:
        q = re.sub(
            r"(?:不要|别要|别买|除了|不含|排除|不想买|不要买)\s*" + re.escape(exclude_hint),
            "",
            q,
        )
    for alias in sorted(BRAND_ALIASES, key=len, reverse=True):
        if alias and alias.lower() in q.lower():
            return _normalize_brand(alias)
    return None


def _resolve_sub_category(
    query: str, category: str | None, llm_sub: str | None,
) -> tuple[str | None, str | None]:
    """P0-B: sub_category 确定性兜底 + 规范词表对齐。"""
    canonical = get_canonical_sub_categories()
    if llm_sub and llm_sub in canonical:
        return llm_sub, None
    rule_sub = detect_sub_category(query, category) if query else None
    if rule_sub and rule_sub in canonical:
        return rule_sub, None
    if rule_sub:
        return None, rule_sub
    if llm_sub:
        return None, llm_sub
    return None, None


def _route_intent(slots: SlotSchema, pre: dict) -> SlotSchema:
    """路由守卫：规则强信号覆盖 LLM。"""
    if slots.confidence < 0.6 and slots.intent not in ("shop_action", "chitchat", "scene_search"):
        slots.intent = "search"
    if slots.intent == "narrow" and not slots.resolved_product_id:
        slots.intent = "search"
    # resolved_product_id 只把 search/narrow 升级为 narrow，保留 direct_answer
    if slots.resolved_product_id and slots.intent in ("search", "narrow"):
        slots.intent = "narrow"
    if pre.get("shop_action"):
        slots.intent = "shop_action"
    return slots


def _resolve_clarification(slots: SlotSchema) -> SlotSchema:
    """追问触发规则（对齐 v2.0 router）：

    仅 intent == "search" 时可能追问；缺 sub_category 且无 brand 强约束 → 追问。
      - category 已知 → P3 出子类选择题
      - category 未知 → 通用追问大类
    narrow / scene_search / direct_answer / shop_action / chitchat 一律不追问。
    """
    if slots.intent != "search":
        slots.needs_clarification = False
        return slots
    slots.needs_clarification = not slots.sub_category and not slots.brand
    return slots


def _apply_scene_search(slots: SlotSchema) -> SlotSchema:
    """scene_search 跨品类发散：清品类/预算，注入轻量场景关键词。"""
    slots.category = None
    slots.sub_category = None
    slots.budget = BudgetSchema()
    scene = slots.scene or ""
    scene_kws = _SCENE_SPEC_KEYWORDS.get(scene, [])
    if not scene_kws:
        # LLM 可能给中文场景值（"三亚旅行"），按 SCENARIO_MAP 归一到英文键
        try:
            from app.decision.rules import SCENARIO_MAP
            for cn, en in SCENARIO_MAP.items():
                if cn and cn in scene:
                    scene_kws = _SCENE_SPEC_KEYWORDS.get(en, [])
                    break
        except Exception:
            pass
    for kw in scene_kws:
        if kw not in slots.spec_keywords:
            slots.spec_keywords.append(kw)
    if scene_kws and slots.rewritten_query:
        extra = " ".join(k for k in scene_kws if k not in slots.rewritten_query)
        if extra:
            slots.rewritten_query = f"{slots.rewritten_query} {extra}"
    return slots


def _narrow_candidate_ids(slots: SlotSchema, snapshot: dict) -> list[str]:
    last_products = snapshot.get("last_products") or []
    exclusions = [str(e).lower() for e in (slots.exclusions or [])]
    ids: list[str] = []
    for p in last_products:
        if not isinstance(p, dict):
            continue
        pid = p.get("product_id")
        if not pid:
            continue
        brand = str(p.get("brand") or "").lower()
        if exclusions and any(e and (e in brand or brand in e) for e in exclusions):
            continue
        ids.append(pid)
    if slots.resolved_product_id in ids:
        ids = [slots.resolved_product_id] + [c for c in ids if c != slots.resolved_product_id]
    return ids[:5]


def _merge_profile(current, profile: dict):
    cur = current.model_dump() if current else {}
    for k, v in profile.items():
        if not v:
            continue
        cur.setdefault(k, [])
        for x in v:
            if x not in cur[k]:
                cur[k].append(x)
    return UserProfileSchema(**cur)


async def _load_user_profile(user_id: str) -> UserProfileSchema:
    """从 user_preference_entries 聚合用户长期画像。"""
    repo = get_user_preference_repo()
    entries = await repo.alist_by_category(user_id)
    profile = UserProfileSchema()
    for e in entries:
        for b in (e.brands or []):
            if b not in profile.brands:
                profile.brands.append(b)
        for a in (e.avoid_tags or []):
            if a not in profile.avoid_tags:
                profile.avoid_tags.append(a)
        for m in (e.must_tags or []):
            if m not in profile.must_tags:
                profile.must_tags.append(m)
        for s in (e.scenarios or []):
            if s not in profile.scenarios:
                profile.scenarios.append(s)
        if e.category and e.category not in profile.categories:
            profile.categories.append(e.category)
    return profile


async def _persist_profile_async(user_id: str, raw_text: str) -> None:
    """LLM 抽取偏好并落库（异步旁路，失败不影响主链路）。"""
    try:
        svc = UserProfileService()
        await svc.parse_and_save(user_id, raw_text)
    except Exception as e:
        logger.debug("profile persist skipped: %s", e)


def _spawn_profile_task(coro) -> None:
    task = asyncio.create_task(coro)
    _pending_profile_tasks.add(task)
    task.add_done_callback(_pending_profile_tasks.discard)


def _clarification_fallback(category: str, subs: list[str]) -> str:
    top = subs[:4]
    return (
        f"嘿嘿，豆仔发现您想买{category}的好物呢！🎁 "
        f"请问您更想看【{'】、【'.join(top)}】中的哪一类呀？"
    )


def _acc_budget_text(acc: dict) -> str:
    lo, hi = acc.get("budget_min"), acc.get("budget_max")
    if lo is not None and hi is not None:
        return f"预算{lo:.0f}-{hi:.0f}之间"
    if hi is not None:
        return f"预算{hi:.0f}以内"
    if lo is not None:
        return f"预算{lo:.0f}以上"
    return ""
