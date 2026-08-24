# -*- coding: utf-8 -*-
"""v2.0 State Schema — LangGraph 强类型共享状态 全局内存（黑板）。
那么这份 AgentState 就是整个公司（LangGraph 工作流）所有部门（节点）共同传递的“公文包”。

依据《豆仔 v2.0 重构方案》核心数据契约，并吸收已定案决策：
  D1  预算：LLM 只出 raw + modifier，min/max 由规则提取，BudgetGovernor 做确定性算术
  D3  检索通道：text / review / policy 三通道
  D5  推荐：固定 Top-3
  D7  上下文：chat_history 直塞（≤30 轮），summary 仅极端兜底
  D8  画像：user_profile 由规则门 + 异步旁路更新
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

IntentType = Literal[
    "search", "narrow", "direct_answer", "scene_search", "shop_action", "chitchat"
]


class BudgetSchema(BaseModel):
    """预算槽位 — D1：LLM 只产出 raw + modifier，min/max 由规则层产出。"""

    min: Optional[float] = None
    max: Optional[float] = None
    raw: Optional[str] = None          # 用户原话片段："800左右" / "便宜一点"
    modifier: Optional[str] = None     # cheaper / pricier / same / double / half


class SlotSchema(BaseModel):
    """Governor 输出槽位（39 子类白名单 / 品牌归一化 / 预算治理后的最终槽位）。"""

    intent: IntentType = "search"  # 与 AgentState.intent 同步（budget_governor 规则覆盖需要）
    confidence: float = 0.0
    rewritten_query: str = ""
    budget_carryover: Literal["inherit", "reset"] = "inherit"

    category: Optional[str] = None
    sub_category: Optional[str] = None
    brand: Optional[str] = None
    budget: BudgetSchema = Field(default_factory=BudgetSchema)
    scene: Optional[str] = None
    skin_type: Optional[str] = None
    benefit: List[str] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)
    spec_keywords: List[str] = Field(default_factory=list)
    must_tags: List[str] = Field(default_factory=list)
    resolved_product_id: Optional[str] = None
    needs_clarification: bool = False

    # 规则层高置信度字段（供 BudgetGovernor / 路由守卫覆盖 LLM）
    rule_resolved_product_id: Optional[str] = None
    rule_budget_max: Optional[float] = None
    rule_budget_min: Optional[float] = None
    rule_budget_kind: Optional[str] = None  # fuzzy / absolute / max_only / min_only
    rule_budget_raw: Optional[str] = None
    rule_shop_action: bool = False
    acc_budget_max: Optional[float] = None  # 累积约束（来自 context_snapshot）
    acc_budget_min: Optional[float] = None  # 累积区间下界（决策 A：代词区间锁用）
    acc_anchor_price: Optional[float] = None  # 上一轮展示商品价（"比他贵"的"他"，决策 A）


class UserProfileSchema(BaseModel):
    """D8：用户长期画像（规则门触发 + 异步旁路更新）。"""

    categories: List[str] = Field(default_factory=list)
    brands: List[str] = Field(default_factory=list)
    skin_type: List[str] = Field(default_factory=list)
    must_tags: List[str] = Field(default_factory=list)
    avoid_tags: List[str] = Field(default_factory=list)
    scenarios: List[str] = Field(default_factory=list)


class AgentState(BaseModel):
    """LangGraph 全局 State — v2.0 核心数据契约。"""

    # ---- 基础交互字段 user_input（用户说了啥）、chat_history（历史对话）、image_url（有没有发图片） ----
    user_input: str = ""
    chat_history: List[Dict[str, str]] = Field(default_factory=list)  # 最近 N 轮（D7 直塞）
    conversation_id: str = ""
    session_id: str = ""
    user_id: str = ""
    image_url: Optional[str] = None

    # ---- Governor LLM 输出 rewritten_query（改写后的话）、intent（意图）、slots（GovernorSlots的字段） ----
    rewritten_query: str = ""
    intent: IntentType = "search"
    slots: SlotSchema = Field(default_factory=SlotSchema)

    # ---- 上下文（D7：直塞 + 极端兜底）----
    context_snapshot: Dict[str, Any] = Field(default_factory=dict)  # last_products/constraints/pending_question
    conversation_summary: str = ""
    context_hash: str = ""

    # ---- 画像（D8） user_profile（用户的长期偏好，比如喜欢极简风）----
    user_profile: Optional[UserProfileSchema] = None

    # ---- 检索与决策中间态  干活的中间产物  retrieval_channels：决定走哪几个通道（text/review/policy）。
    #   candidate_ids / ranked_items 检索和重排后捞出来的商品列表。
    # 追问与回复：needs_clarification（要不要追问）、clarification_question（追问的话术）、final_response（最终给用户的回复）。
    # ----
    retrieval_channels: List[str] = Field(
        default_factory=lambda: ["text", "review", "policy"]  # D3 三通道
    )
    candidate_ids: List[str] = Field(default_factory=list)      # narrow 候选集白名单
    ranked_items: List[Dict[str, Any]] = Field(default_factory=list)  # 精排后 Top-K
    evidence_list: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_sufficiency: bool = False
    sufficiency_report: Dict[str, Any] = Field(default_factory=dict)
    decision_score: float = 0.0
    decision_results: List[Dict[str, Any]] = Field(default_factory=list)  # D5: Top-3

    # ---- 追问（短路）----
    needs_clarification: bool = False
    clarification_question: str = ""
    clarification_options: List[Dict[str, Any]] = Field(default_factory=list)

    # ---- 最终回复 ----
    final_response: str = ""

    # ---- 可观测性  trace_steps  记录经过了哪些节点，方便排查 Bug----
    trace_steps: List[Dict[str, Any]] = Field(default_factory=list)
    timing: Dict[str, Any] = Field(default_factory=dict)
    harness_report: Dict[str, Any] = Field(default_factory=dict)
