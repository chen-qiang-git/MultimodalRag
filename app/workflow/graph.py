# -*- coding: utf-8 -*-
"""主图 — v2.0 主链路。

  START → Governor 子图 ──┬─ chitchat → response
                         ├─ needs_clarification → clarification → END
                         └─ 其余 → retrieval → reranker(D4) → evidence_check
                                  → {decision | response} → response → guard → END
"""

from langgraph.graph import END, StateGraph

from app.governor.nodes import build_governor, clarification_node
from app.schemas.agent_state import AgentState
from app.workflow.direct_answer import direct_answer_node
from app.workflow.shop_action import shop_action_node
from app.workflow.scene_plan import scene_plan_node
from app.workflow.nodes import (
    decision_node,
    evidence_check_node,
    guard_node,
    reranker_node,
    response_node,
    retrieval_node,
)


def _router(state: AgentState) -> str:
    if state.intent == "chitchat":
        return "response"
    if state.intent == "shop_action":
        return "shop_action"
    if state.intent == "direct_answer":
        return "direct_answer"
    if state.intent == "scene_search":
        return "scene_plan"
    if state.needs_clarification:
        return "clarification"
    return "retrieval"


def _has_results(state: AgentState) -> str:
    return "decision" if state.ranked_items else "response"


def build_workflow() -> StateGraph:
    wf = StateGraph(AgentState)
    wf.add_node("governor", build_governor())
    wf.add_node("clarification", clarification_node)
    wf.add_node("direct_answer", direct_answer_node)
    wf.add_node("shop_action", shop_action_node)
    wf.add_node("scene_plan", scene_plan_node)
    wf.add_node("retrieval", retrieval_node)
    wf.add_node("reranker", reranker_node)
    wf.add_node("evidence_check", evidence_check_node)
    wf.add_node("decision", decision_node)
    wf.add_node("response", response_node)
    wf.add_node("guard", guard_node)

    wf.set_entry_point("governor")
    wf.add_conditional_edges(
        "governor", _router,
        {
            "clarification": "clarification",
            "direct_answer": "direct_answer",
            "shop_action": "shop_action",
            "scene_plan": "scene_plan",
            "retrieval": "retrieval",
            "response": "response",
        },
    )
    wf.add_edge("clarification", END)
    wf.add_edge("direct_answer", END)
    wf.add_edge("shop_action", END)
    wf.add_edge("scene_plan", "retrieval")
    wf.add_edge("retrieval", "reranker")
    wf.add_edge("reranker", "evidence_check")
    wf.add_conditional_edges(
        "evidence_check", _has_results,
        {"decision": "decision", "response": "response"},
    )
    wf.add_edge("decision", "response")
    wf.add_edge("response", "guard")
    wf.add_edge("guard", END)
    return wf


_compiled = None


def get_workflow():
    global _compiled
    if _compiled is None:
        _compiled = build_workflow().compile()
    return _compiled


async def run_agent(state: AgentState) -> AgentState:
    result = await get_workflow().ainvoke(state)
    if isinstance(result, dict):
        return AgentState(**result)
    return result
