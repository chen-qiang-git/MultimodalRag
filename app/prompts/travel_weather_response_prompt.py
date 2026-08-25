"""旅行天气导购的证据绑定回复。"""

from __future__ import annotations

import json


def build_travel_weather_response_prompt(weather: dict, plan: dict, items: list[dict]) -> str:
    return f"""你是松仔，正在为旅行准备商品。只使用以下已验证天气和商品证据。

### 已验证天气
{json.dumps(weather, ensure_ascii=False, default=str)}

### 旅行任务
{json.dumps(plan, ensure_ascii=False, default=str)}

### 商品证据（唯一商品事实来源）
{json.dumps(items, ensure_ascii=False, default=str)}

### 输出规则
1. 开头用一两句概述 `summary` 中的天气，不能补充未提供的天气事实。
2. 逐项介绍全部商品。每项必须包含 `travel_task.sub_category`、完整商品名和证据中的准确价格。
3. 每项理由必须对应它自己的 `travel_task.weather_reason`；不得把天气写成商品性能或属性。
4. 不得产生证据外的商品、品牌、价格、库存或规格；不要展示内部字段名。
5. 中文、亲切、简洁，直接输出用户可见答复。"""


def travel_weather_template_answer(weather: dict, plan: dict, items: list[dict]) -> str:
    summary = str(weather.get("summary") or plan.get("weather_summary") or "已获取旅行天气预报。")
    destination = str(weather.get("destination") or plan.get("destination") or "目的地")
    lines = [f"{destination}出行天气：{summary}", "松仔按这份预报从商品库里准备了这些："]
    for item in items:
        task = item.get("travel_task") or {}
        sub = task.get("sub_category") or (item.get("scene_task") or {}).get("label") or "旅行准备"
        reason = task.get("weather_reason") or "适合纳入旅行准备。"
        price = item.get("price")
        price_text = f"¥{float(price):g}" if isinstance(price, (int, float)) else "价格见详情"
        brand = f"（{item.get('brand')}）" if item.get("brand") else ""
        lines.append(f"{sub}：{item.get('title') or '商品'}{brand}{price_text}。{reason}")
    lines.append("如果你有出行日期、预算或不想带的东西，告诉松仔我再帮你收窄～")
    return "\n".join(lines)
