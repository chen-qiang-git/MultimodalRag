"""把宽泛场景需求转换为可追踪的商品任务槽位。"""

from app.scene.plan import build_task_query, get_scene_plan
from app.schemas.agent_state import AgentState


async def scene_plan_node(state: AgentState) -> AgentState:
    # TravelWeatherAgent 已构建动态任务时，保留其天气依据与真实品类计划。
    if state.travel_plan and state.scene_task_queries:
        return state
    plan = get_scene_plan(state.slots.scene)
    if plan is None:
        state.trace_steps.append({
            "step_id": f"T{len(state.trace_steps) + 1:03d}",
            "agent_name": "Scene Plan Node",
            "action": "build_scene_plan",
            "input_summary": state.slots.scene or "unknown",
            "output_summary": "no dedicated plan; use existing scene retrieval",
            "latency_ms": 0,
            "status": "skipped",
        })
        return state

    state.scene_plan = plan.to_payload()
    state.scene_task_queries = [
        {
            "key": task.key,
            "label": task.label,
            "query": build_task_query(state.user_input, task),
            "sub_categories": list(task.sub_categories),
        }
        for task in plan.tasks
    ]
    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "Scene Plan Node",
        "action": "build_scene_plan",
        "input_summary": state.user_input[:60],
        "output_summary": f"plan={plan.scene}, tasks={len(plan.tasks)}",
        "latency_ms": 0,
        "status": "success",
    })
    return state
