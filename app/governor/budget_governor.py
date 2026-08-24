# -*- coding: utf-8 -*-
"""BudgetGovernor — 确定性条件治理 (M2, 阶段 4).

职责: 把 LLM 的"语言条件"转成检索可用的"数字条件"。
**不允许 LLM 做算术** — 相对预算修饰词在此处做系数乘法。

依据 docs/dialogue-governor-design.md §4 阶段4:
  - BudgetGovernor 映射表 (可配置系数)
  - 规则覆盖校验 (D3): 高置信度字段冲突时规则覆盖 LLM, 记录日志
  - 槽位归一化
"""

import logging
import re
from typing import Optional

from app.core.config import BUDGET_FUZZ_RATIO_MIN, BUDGET_FUZZ_RATIO_MAX
from app.schemas.agent_state import SlotSchema

logger = logging.getLogger(__name__)

# ---- BudgetGovernor 映射表 (可配置) ----
# modifier -> (系数, 说明)
BUDGET_MODIFIERS: dict[str, tuple[float, str]] = {
    "cheaper": (0.8, "便宜一点"),
    "much_cheaper": (0.7, "再便宜点 / 更便宜"),
    "pricier": (1.2, "贵一点"),
    "same": (0.95, "差不多 / 别太贵"),
    "double": (2.0, "预算翻倍"),
    "half": (0.5, "减半"),
}

# 中文修饰词 -> modifier 标签 (LLM 可能直接给中文)
CN_MODIFIER_MAP: dict[str, str] = {
    "便宜一点": "cheaper", "稍微便宜": "cheaper", "便宜些": "cheaper",
    "便宜点": "cheaper", "低一点": "cheaper", "价格低": "cheaper",
    "预算不多": "cheaper", "少一点": "cheaper", "低些": "cheaper",
    "再便宜点": "much_cheaper", "更便宜": "much_cheaper", "再便宜": "much_cheaper",
    "贵一点": "pricier", "稍贵": "pricier", "贵些": "pricier",
    "差不多": "same", "别太贵": "same", "差不多就行": "same",
    "翻倍": "double", "预算翻倍": "double",
    "减半": "half", "便宜一半": "half",
}

# ---- 预算表达式正则 (P0-3: 区分 绝对区间/模糊区间/单边约束) ----
# 顺序敏感: 绝对区间 > 模糊区间 > 单边 > 放宽/预算触发词
_BUDGET_PATTERNS: list[tuple[str, str]] = [
    # 绝对区间: 300-500 / 300到500 / 300~500 / 300至500 (可带 之间/元)
    (r"(\d+)\s*(?:到|至|~|－|—|-)\s*(\d+)\s*元?(?:之间)?", "absolute"),
    # 模糊区间: 200左右 / 上下 / 前后
    (r"(\d+)\s*元?\s*(?:左右|上下|前后)", "fuzzy"),
    # 单边上限: X以内 / 以下 / 之内 / 内
    (r"(\d+)\s*元?\s*(?:以内|以下|之内|内)", "max_only"),
    # 单边下限: X以上 / 起 / 往上
    (r"(\d+)\s*元?\s*(?:以上|起|往上)", "min_only"),
    # 放宽/放到/预算: "放款到750" / "放宽到750" / "预算750"
    (r"(?:预算|放宽到|放款到|放到|提高到|升到)\s*(\d+)\s*元?", "max_only"),
    # 裸金额兜底: ¥750 / 750元
    (r"[¥￥]\s*(\d+)", "max_only"),
    (r"(\d+)\s*元(?![\d])", "max_only"),
]


def detect_budget_range(text: str) -> dict:
    """从查询文本提取预算表达式, 返回 {min, max, kind, raw, span}。

    kind:
      absolute — 绝对区间 (300-500) → min/max 均为规则值
      fuzzy    — 模糊区间 (200左右) → min=max*LO, max=max*HI (系数可配置)
      max_only — 单边上限 (200以内/放宽到750) → max 有值, min 置 None
      min_only — 单边下限 (500以上) → min 有值, max 置 None
      none     — 未命中
    """
    if not text:
        return {"min": None, "max": None, "kind": "none", "raw": "", "span": None}
    q = text.lower()
    for pattern, kind in _BUDGET_PATTERNS:
        m = re.search(pattern, q)
        if not m:
            continue
        raw = m.group(0)
        span = m.span()
        if kind == "absolute":
            lo, hi = float(m.group(1)), float(m.group(2))
            return {"min": lo, "max": hi, "kind": kind, "raw": raw, "span": span}
        if kind == "fuzzy":
            x = float(m.group(1))
            return {
                "min": round(x * BUDGET_FUZZ_RATIO_MIN, 2),
                "max": round(x * BUDGET_FUZZ_RATIO_MAX, 2),
                "kind": kind, "raw": raw, "span": span,
            }
        if kind == "max_only":
            return {"min": None, "max": float(m.group(1)), "kind": kind, "raw": raw, "span": span}
        if kind == "min_only":
            return {"min": float(m.group(1)), "max": None, "kind": kind, "raw": raw, "span": span}
    return {"min": None, "max": None, "kind": "none", "raw": "", "span": None}


def strip_budget_expr(text: str) -> str:
    """去掉文本中的预算表达式, 保留核心需求名词 (P0-2 条件替换用)。"""
    if not text:
        return ""
    info = detect_budget_range(text)
    span = info.get("span")
    core = text
    if span:
        core = (text[:span[0]] + text[span[1]:]).strip()
    # 循环清理引导词/前缀 (算了→我想要→一双), 直到稳定
    _lead = re.compile(
        r"^(?:价格|预算|价位|大概|大约|算了|那|嗯|好|我?想要|我?想买|我?想找|推荐|给我推荐|给我|找|来)"
        r"\s*(?:一|1)?\s*(?:款|双|个|只)?\s*[，,、]?\s*"
    )
    while True:
        stripped = _lead.sub("", core)
        if stripped == core:
            break
        core = stripped
    # "之间的耳机" 去掉区间后残留的 "的" (例: "200到500之间的耳机" → "的耳机")
    core = re.sub(r"^的\s*", "", core).strip()
    return core


def resolve_modifier(text: str) -> Optional[str]:
    """从中文修饰词文本解析 modifier 标签。"""
    if not text:
        return None
    for cn, mod in CN_MODIFIER_MAP.items():
        if cn in text:
            return mod
    return None


def apply_budget_modifier(slots: SlotSchema) -> SlotSchema:
    """对 slots.budget 做确定性算术 (阶段4 BudgetGovernor)。

    输入 = 修饰词 + 上一轮 budget (slots.acc_budget_max)
    输出 = budget.max = round(prev * coef)
    无上一轮预算且只有修饰词 -> needs_clarification 或保持无预算

    决策 A（2026-08-24）："更贵/更便宜" 是相对上一轮展示商品（acc_anchor_price）的边界移动，
    不是对上界乘系数：
      - pricier → 下界抬到锚点价，上界保持继承区间（不乘 1.2）
      - cheaper → 上界压到锚点价，下界保持
    "这个区间/这个范围" 等代词区间 → 直接锁死上轮区间，禁用系数。
    """
    modifier = slots.budget.modifier or resolve_modifier(slots.budget.raw or "")
    if not modifier:
        return slots

    # P0-3: 规则已产出绝对/模糊/单边预算时, 不做相对修饰词算术
    if slots.rule_budget_kind in ("fuzzy", "absolute", "max_only", "min_only"):
        return slots

    anchor = slots.acc_anchor_price
    raw_signal = (slots.budget.raw or "") + (slots.budget.modifier or "")

    # 代词区间引用："这个区间/这个范围" → 锁死上轮区间，再做锚点边界移动
    if re.search(r"(?:这个|那个|刚才|上轮)?(?:区间|范围)", raw_signal) and (
        slots.acc_budget_max is not None or slots.acc_budget_min is not None
    ):
        if slots.acc_budget_min is not None:
            slots.budget.min = slots.acc_budget_min
        if slots.acc_budget_max is not None:
            slots.budget.max = slots.acc_budget_max
        if modifier == "pricier" and anchor:
            slots.budget.min = max(slots.budget.min or 0.0, anchor)
        elif modifier == "cheaper" and anchor:
            slots.budget.max = min(slots.budget.max or anchor, anchor)
        slots.budget.modifier = None  # 区间已锁定，不再保留相对修饰词
        return slots

    # 决策 A：更贵 → 抬下界到锚点价，上界保持
    if modifier == "pricier" and anchor:
        floor = max(slots.budget.min or 0.0, anchor)
        slots.budget.min = round(floor, 2)
        if slots.budget.max is not None and slots.budget.max < floor:
            slots.budget.max = round(floor, 2)  # 防区间塌缩
        slots.budget.modifier = None
        return slots

    # 对称：更便宜 → 压上界到锚点价，下界保持
    if modifier == "cheaper" and anchor:
        ceiling = min(slots.budget.max if slots.budget.max is not None else anchor, anchor)
        slots.budget.max = round(ceiling, 2)
        if slots.budget.min is not None and slots.budget.min > ceiling:
            slots.budget.min = round(ceiling, 2)
        slots.budget.modifier = None
        return slots

    coef, _desc = BUDGET_MODIFIERS.get(modifier, (None, ""))
    if coef is None:
        return slots

    prev_max = slots.acc_budget_max
    if prev_max is None or prev_max <= 0:
        # 无上一轮预算, "便宜一点" 是语义缺失
        if modifier in ("cheaper", "much_cheaper", "pricier", "same", "half", "double"):
            slots.needs_clarification = True
        return slots

    new_max = round(prev_max * coef, 2)
    if new_max < 0:
        new_max = 0.0
    slots.budget.max = new_max
    # min 钳制: budget.min <= budget.max
    if slots.budget.min is not None and slots.budget.min > slots.budget.max:
        slots.budget.min = slots.budget.max
    return slots


def apply_rule_override(slots: SlotSchema) -> SlotSchema:
    """规则覆盖校验 (D3): 高置信度字段冲突时规则覆盖 LLM。

    高置信度字段:
      - 绝对预算 budget.max (正则提取)
      - resolved_product_id (序数/品牌匹配)
      - 显式购物意图
    冲突 -> 强制用规则值覆盖, 写结构化日志。
    """
    # 预算: 规则区间/单边 (P0-3) 覆盖 LLM, 消除"200左右"的 modifier 抖动
    kind = slots.rule_budget_kind
    if kind is None:
        # 兼容旧调用方: 只传 rule_budget_max/min 未传 kind 时按单边处理
        if slots.rule_budget_max is not None:
            kind = "max_only"
        elif slots.rule_budget_min is not None:
            kind = "min_only"
    if kind in ("fuzzy", "absolute", "max_only", "min_only"):
        rule_min = slots.rule_budget_min
        rule_max = slots.rule_budget_max
        llm_min = slots.budget.min
        llm_max = slots.budget.max
        if kind in ("fuzzy", "absolute") and rule_min is not None and rule_max is not None:
            if llm_min != rule_min or llm_max != rule_max:
                logger.info(
                    "rule_override budget range: kind=%s rule=[%s,%s] llm=[%s,%s]",
                    kind, rule_min, rule_max, llm_min, llm_max,
                )
            slots.budget.min = rule_min
            slots.budget.max = rule_max
            slots.budget.modifier = None  # 规则区间已定，不再保留相对修饰词
        elif kind == "max_only":
            if llm_max != rule_max:
                logger.info(
                    "rule_override budget max_only: rule=%s llm=%s",
                    rule_max, llm_max,
                )
            slots.budget.max = rule_max
            slots.budget.min = None  # 单边上限, 清对端防区间残留
            slots.budget.modifier = None  # "500以内" 不是相对修饰词
        elif kind == "min_only":
            if llm_min != rule_min:
                logger.info(
                    "rule_override budget min_only: rule=%s llm=%s",
                    rule_min, llm_min,
                )
            slots.budget.min = rule_min
            slots.budget.max = None
            slots.budget.modifier = None  # "500以上" 不是相对修饰词
        if not slots.budget.raw and slots.rule_budget_raw:
            slots.budget.raw = slots.rule_budget_raw

    # resolved_product_id: 规则 rule_resolved_product_id 覆盖 LLM
    if slots.rule_resolved_product_id:
        if slots.resolved_product_id and slots.resolved_product_id != slots.rule_resolved_product_id:
            logger.info(
                "rule_override resolved_product_id: rule=%s llm=%s",
                slots.rule_resolved_product_id, slots.resolved_product_id,
            )
        slots.resolved_product_id = slots.rule_resolved_product_id

    # 显式购物意图: 规则强信号不被 LLM 覆盖
    if slots.rule_shop_action:
        if slots.intent != "shop_action":
            logger.info(
                "rule_override intent: rule=shop_action llm=%s", slots.intent,
            )
        slots.intent = "shop_action"

    return slots


def normalize_slots(slots: SlotSchema) -> SlotSchema:
    """阶段4 完整治理: 规则覆盖 -> 相对预算算术。"""
    slots = apply_rule_override(slots)
    slots = apply_budget_modifier(slots)
    return slots
