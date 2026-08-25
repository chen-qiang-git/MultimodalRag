"""基于实时商品目录生成旅行选品任务，不允许模型直接推荐商品。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from app.model_gateway.gateway import get_model_gateway
from app.schemas.agent_state import BudgetSchema
from app.travel.weather import TravelWeatherDay


class TravelShoppingTask(BaseModel):
    sub_category: str
    query: str
    weather_reason: str


class TravelShoppingPlan(BaseModel):
    destination: str
    weather_summary: str
    tasks: list[TravelShoppingTask]


def build_catalog_taxonomy(products: list[Any]) -> dict[str, list[str]]:
    taxonomy: dict[str, set[str]] = {}
    for product in products:
        category = _value(product, "category")
        sub_category = _value(product, "sub_category")
        if category and sub_category:
            taxonomy.setdefault(category, set()).add(sub_category)
    return {category: sorted(subs) for category, subs in sorted(taxonomy.items())}


def flatten_allowed_sub_categories(taxonomy: dict[str, list[str]]) -> set[str]:
    return {sub for subs in taxonomy.values() for sub in subs}


async def build_travel_shopping_plan(
    destination: str,
    weather_summary: str,
    days: list[TravelWeatherDay],
    taxonomy: dict[str, list[str]],
    budget: BudgetSchema,
    exclusions: list[str],
    preferences: list[str],
) -> TravelShoppingPlan:
    allowed = flatten_allowed_sub_categories(taxonomy)
    if not allowed:
        return TravelShoppingPlan(destination=destination, weather_summary=weather_summary, tasks=[])
    raw = ""
    try:
        raw = await get_model_gateway().chat(
            "intent_understanding",
            _planner_prompt(destination, weather_summary, days, taxonomy, budget, exclusions, preferences),
        )
    except Exception:
        raw = ""
    tasks = _validate_tasks(_extract_json_array(raw), allowed)
    return TravelShoppingPlan(
        destination=destination,
        weather_summary=weather_summary,
        tasks=_ensure_minimum_tasks(tasks, allowed, destination, weather_summary),
    )


def _planner_prompt(
    destination: str, weather_summary: str, days: list[TravelWeatherDay],
    taxonomy: dict[str, list[str]], budget: BudgetSchema,
    exclusions: list[str], preferences: list[str],
) -> str:
    return f"""你是松仔的旅行选品规划器，只负责生成检索任务，不负责推荐商品。
目的地：{destination}
已验证天气：{weather_summary}
逐日预报：{json.dumps([day.model_dump() for day in days], ensure_ascii=False)}
预算：{budget.model_dump_json()}
排除：{json.dumps(exclusions, ensure_ascii=False)}
偏好：{json.dumps(preferences, ensure_ascii=False)}
允许目录（唯一选品空间）：{json.dumps(taxonomy, ensure_ascii=False)}

只输出 JSON 数组，最多 5 项。每项严格为：
{{"sub_category":"允许目录中的真实子类","query":"用于检索的中文短句","weather_reason":"引用上述天气事实的简短理由"}}
不得输出商品名、品牌、价格、库外品类；不得把天气描述成商品属性。若目录允许，优先覆盖至少两种不同子类。"""


def _extract_json_array(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return []
    try:
        value = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _validate_tasks(raw_tasks: list[dict[str, Any]], allowed: set[str]) -> list[TravelShoppingTask]:
    result: list[TravelShoppingTask] = []
    seen: set[str] = set()
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            continue
        sub = str(raw.get("sub_category") or "").strip()
        query = str(raw.get("query") or "").strip()
        reason = str(raw.get("weather_reason") or "").strip()
        if sub not in allowed or not query or not reason or sub in seen:
            continue
        result.append(TravelShoppingTask(sub_category=sub, query=query[:120], weather_reason=reason[:120]))
        seen.add(sub)
        if len(result) == 5:
            break
    return result


def _ensure_minimum_tasks(
    tasks: list[TravelShoppingTask], allowed: set[str], destination: str, weather_summary: str
) -> list[TravelShoppingTask]:
    if len(allowed) < 2:
        return tasks[:1]
    used = {task.sub_category for task in tasks}
    keywords = ("防晒", "雨", "风", "保暖", "背包", "充电", "鞋", "帽", "饮料", "零食")
    ordered = sorted(allowed, key=lambda sub: (not any(word in sub for word in keywords), sub))
    for sub in ordered:
        if len(tasks) >= 2 or sub in used:
            continue
        tasks.append(TravelShoppingTask(
            sub_category=sub,
            query=f"{destination} 旅行 {sub}",
            weather_reason="按旅行通用准备补充；未将其当作天气事实。",
        ))
        used.add(sub)
    return tasks[:5]


def _value(product: Any, key: str) -> str:
    value = product.get(key) if isinstance(product, dict) else getattr(product, key, "")
    return str(value or "").strip()
