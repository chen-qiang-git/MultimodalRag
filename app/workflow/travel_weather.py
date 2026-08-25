"""LangGraph 内的旅行天气导购节点。"""

from __future__ import annotations

from app.repositories import get_product_repo
from app.schemas.agent_state import AgentState
from app.travel.planner import build_catalog_taxonomy, build_travel_shopping_plan
from app.travel.weather import AMapWeatherClient


def should_use_travel_weather(state: AgentState) -> bool:
    return (
        state.intent == "scene_search"
        and state.slots.scene == "travel"
        and bool(state.slots.travel_destination)
    )


async def travel_weather_node(state: AgentState) -> AgentState:
    destination = state.slots.travel_destination
    if not destination:
        state.travel_weather_status = "not_requested"
        return state

    _trace(state, "resolve_travel_request", destination, _date_range(state), "success")
    snapshot, status = await AMapWeatherClient().forecast(
        destination, state.slots.travel_start_date, state.slots.travel_end_date
    )
    state.travel_weather_status = status
    if snapshot is None:
        _trace(state, "fetch_travel_weather", destination, f"status={status}", "degraded")
        _trace(state, "fallback_travel_scene", destination, "use normal travel scene plan", "success")
        return state

    state.travel_weather = snapshot.model_dump()
    _trace(
        state,
        "fetch_travel_weather",
        destination,
        f"source={snapshot.source}, days={len(snapshot.days)}",
        "success",
    )
    taxonomy = build_catalog_taxonomy(get_product_repo().list_all())
    plan = await build_travel_shopping_plan(
        destination=destination,
        weather_summary=snapshot.summary,
        days=snapshot.days,
        taxonomy=taxonomy,
        budget=state.slots.budget,
        exclusions=state.slots.exclusions,
        preferences=state.slots.spec_keywords + state.slots.must_tags,
    )
    state.travel_plan = plan.model_dump()
    state.scene_plan = {
        "scene": "travel",
        "title": f"{destination}旅行准备",
        "intro": snapshot.summary,
        "notes": ["天气来自高德预报；商品仅来自当前商品库"],
    }
    state.scene_task_queries = [
        {
            "key": task.sub_category,
            "label": task.sub_category,
            "sub_categories": [task.sub_category],
            **task.model_dump(),
        }
        for task in plan.tasks
    ]
    _trace(
        state,
        "build_travel_plan",
        f"catalog_categories={len(taxonomy)}",
        f"tasks={len(plan.tasks)}",
        "success" if plan.tasks else "empty",
    )
    return state


def _date_range(state: AgentState) -> str:
    start, end = state.slots.travel_start_date, state.slots.travel_end_date
    return f"{start or 'future-4-days'}~{end or 'future-4-days'}"


def _trace(state: AgentState, action: str, input_summary: str, output_summary: str, status: str) -> None:
    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "TravelWeatherAgent",
        "action": action,
        "input_summary": input_summary[:120],
        "output_summary": output_summary[:160],
        "latency_ms": 0,
        "status": status,
    })
