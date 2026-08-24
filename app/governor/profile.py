# -*- coding: utf-8 -*-
"""D8：画像规则门 + 轻量规则抽取（零 LLM 成本；完整 LLM 抽取 P11 后续接入）。"""

import re

_PREFERENCE_PATTERNS = [
    re.compile(r"我(?:的)?(?:皮肤|发质|头发|口味|肤质|脸)[^。，！？]{0,8}(?:敏感|油|干|混|痘|爱出油|中性|受损)"),
    re.compile(r"(?:敏感肌|油皮|干皮|混油皮|混干皮|痘痘肌)"),
    re.compile(r"(?:不喜欢|讨厌|不要|怕|过敏|忌口|避免)\s*[^。，！？]{0,10}"),
    re.compile(r"(?:偏好|喜欢|爱买|常用)\s*[^。，！？]{0,12}(?:风|牌|款|类|口)"),
]

_MIN_TURNS = 5


def should_extract(user_input: str, turn_count: int) -> bool:
    """规则门：轮次 >5 或显式偏好表达才触发（D8）。"""
    if turn_count > _MIN_TURNS:
        return True
    return any(p.search(user_input) for p in _PREFERENCE_PATTERNS)


def rule_extract(user_input: str) -> dict:
    """轻量规则抽取：肤质 / 避雷标签。"""
    profile: dict = {
        "categories": [], "brands": [], "skin_type": [],
        "must_tags": [], "avoid_tags": [], "scenarios": [],
    }
    skin = re.search(r"(敏感肌|油皮|干皮|混油皮|混干皮|痘痘肌|油性|干性|敏感性)", user_input)
    if skin:
        profile["skin_type"].append(skin.group(1))
    avoid = re.search(r"(?:不喜欢|讨厌|不要|怕|过敏|忌口|避免)\s*([^，。！？]{1,10})", user_input)
    if avoid:
        tag = avoid.group(1).strip().rstrip("的")
        if tag:
            profile["avoid_tags"].append(tag)
    return profile


def maybe_extract_profile(user_input: str, turn_count: int) -> dict | None:
    if not should_extract(user_input, turn_count):
        return None
    return rule_extract(user_input)
