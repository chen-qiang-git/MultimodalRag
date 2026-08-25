"""高德旅行天气客户端：MCP 优先，REST 为同 Key 降级路径。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from app.core.config import (
    AMAP_MAPS_API_KEY,
    AMAP_MCP_COMMAND,
    AMAP_MCP_ENABLED,
    AMAP_MCP_SERVER,
    AMAP_REST_BASE_URL,
    AMAP_WEATHER_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class TravelWeatherDay(BaseModel):
    date: str
    day_weather: str = ""
    night_weather: str = ""
    day_temp: str = ""
    night_temp: str = ""
    day_wind: str = ""
    day_power: str = ""


class TravelWeatherSnapshot(BaseModel):
    destination: str
    source: Literal["mcp", "rest"]
    days: list[TravelWeatherDay]
    summary: str


def select_requested_days(
    days: list[TravelWeatherDay], start_date: str | None, end_date: str | None
) -> list[TravelWeatherDay]:
    """未指定日期取未来四天；指定日期只返回该闭区间内预报。"""
    if not start_date and not end_date:
        return days[:4]
    lower = start_date or end_date
    upper = end_date or start_date
    return [day for day in days if lower <= day.date <= upper]


def summarize_weather(days: list[TravelWeatherDay]) -> str:
    """仅拼接 API 返回的逐日字段，避免把推断写成天气事实。"""
    parts: list[str] = []
    for day in days:
        weather = " / ".join(x for x in (day.day_weather, day.night_weather) if x)
        temp = "-".join(x for x in (day.night_temp, day.day_temp) if x)
        text = f"{day.date}：{weather or '天气信息有限'}"
        if temp:
            text += f"，{temp}℃"
        if day.day_wind or day.day_power:
            text += f"，{''.join((day.day_wind, day.day_power))}"
        parts.append(text)
    return "；".join(parts)


class AMapWeatherClient:
    """不保存密钥的高德天气客户端。"""

    async def forecast(
        self, destination: str, start_date: str | None, end_date: str | None
    ) -> tuple[TravelWeatherSnapshot | None, str]:
        if not AMAP_MAPS_API_KEY:
            return None, "unavailable"

        casts: list[dict[str, Any]] = []
        source: Literal["mcp", "rest"] | None = None
        if AMAP_MCP_ENABLED:
            try:
                casts = await asyncio.wait_for(
                    self._forecast_by_mcp(destination),
                    timeout=AMAP_WEATHER_TIMEOUT_SECONDS,
                )
                source = "mcp" if casts else None
            except Exception as exc:
                logger.warning("AMap MCP weather unavailable: %s", type(exc).__name__)

        if not casts:
            try:
                casts = await self._forecast_by_rest(destination)
                source = "rest" if casts else None
            except Exception as exc:
                logger.warning("AMap REST weather unavailable: %s", type(exc).__name__)

        if not casts or source is None:
            return None, "unavailable"
        days = select_requested_days([_cast_to_day(item) for item in casts], start_date, end_date)
        if not days:
            return None, "out_of_range"
        return TravelWeatherSnapshot(
            destination=destination,
            source=source,
            days=days,
            summary=summarize_weather(days),
        ), "available"

    async def _forecast_by_mcp(self, destination: str) -> list[dict[str, Any]]:
        """兼容 amap-mcp-server 的工具名称差异，只选择名称含 weather 的工具。"""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=AMAP_MCP_COMMAND,
            args=[AMAP_MCP_SERVER],
            env={**os.environ, "AMAP_MAPS_API_KEY": AMAP_MAPS_API_KEY},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                weather_tool = next(
                    (tool for tool in tools.tools if "weather" in tool.name.lower()), None
                )
                if weather_tool is None:
                    return []
                result = await session.call_tool(
                    weather_tool.name, _weather_tool_arguments(weather_tool.inputSchema, destination)
                )
        return _casts_from_mcp_result(result)

    async def _forecast_by_rest(self, destination: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                AMAP_REST_BASE_URL,
                params={
                    "key": AMAP_MAPS_API_KEY,
                    "city": destination,
                    "extensions": "all",
                    "output": "JSON",
                },
                timeout=AMAP_WEATHER_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        forecasts = payload.get("forecasts") or []
        casts = forecasts[0].get("casts") if payload.get("status") == "1" and forecasts else []
        return casts if isinstance(casts, list) else []


def _weather_tool_arguments(schema: dict[str, Any] | None, destination: str) -> dict[str, str]:
    properties = (schema or {}).get("properties") or {}
    for name in ("city", "district", "address", "location", "keyword"):
        if name in properties:
            return {name: destination}
    return {"city": destination}


def _casts_from_mcp_result(result: Any) -> list[dict[str, Any]]:
    content = getattr(result, "content", None) or []
    texts = [getattr(item, "text", "") for item in content if getattr(item, "text", "")]
    for text in texts:
        try:
            return _extract_casts(json.loads(text))
        except json.JSONDecodeError:
            continue
    return []


def _extract_casts(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    forecasts = payload.get("forecasts") or []
    if forecasts and isinstance(forecasts[0], dict):
        casts = forecasts[0].get("casts") or []
        return casts if isinstance(casts, list) else []
    casts = payload.get("casts") or []
    return casts if isinstance(casts, list) else []


def _cast_to_day(cast: dict[str, Any]) -> TravelWeatherDay:
    return TravelWeatherDay(
        date=str(cast.get("date") or ""),
        day_weather=str(cast.get("dayweather") or cast.get("day_weather") or ""),
        night_weather=str(cast.get("nightweather") or cast.get("night_weather") or ""),
        day_temp=str(cast.get("daytemp") or cast.get("day_temp") or ""),
        night_temp=str(cast.get("nighttemp") or cast.get("night_temp") or ""),
        day_wind=str(cast.get("daywind") or cast.get("day_wind") or ""),
        day_power=str(cast.get("daypower") or cast.get("day_power") or ""),
    )
