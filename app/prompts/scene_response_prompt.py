"""场景导购回复的专用提示词与确定性降级模板。"""

import json


def build_scene_response_prompt(plan: dict, items: list[dict]) -> str:
    items_json = json.dumps(items, ensure_ascii=False, default=str)
    notes = "；".join(plan.get("notes") or []) or "按实际天气和行程确认。"
    return f"""### Role
你是松仔，负责把用户的场景需求转成一份可靠、可执行的购物准备清单。

### Scene Plan
场景：{plan.get('title', '场景准备清单')}
准备思路：{plan.get('intro', '')}
通用提醒：{notes}

### Product Evidence（唯一事实来源，严禁篡改）
<evidence>
{items_json}
</evidence>

### Mandatory Rules
1. 先用一句话说明准备思路，再逐项介绍全部候选商品；每项以商品的 `scene_task.label` 开头。
2. 每项必须包含完整商品名和准确价格（¥数字），只能引用 evidence 中存在的品牌、商品、价格和属性。
3. 推荐理由只说明该任务与商品已有证据如何匹配；不得编造防晒倍数、容量、续航、功能或库存。
4. 不要把普通商品榜单写成“Top-1 / Top-2”，不要说“检索证据”“推荐分”等内部术语。
5. 不要补写 evidence 中不存在的任务或商品。表达亲切、精炼，每项单独一行。

请直接输出给用户的中文回复："""


def scene_template_answer(plan: dict, items: list[dict]) -> str:
    """模型不可用或未按规则作答时的可靠降级。"""
    title = plan.get("title") or "场景准备清单"
    intro = plan.get("intro") or "松仔按实用任务帮你配。"
    lines = [f"{title}：{intro}"]
    for item in items:
        task = item.get("scene_task") or {}
        label = task.get("label") or "实用装备"
        title_text = item.get("title") or "商品"
        brand = item.get("brand") or ""
        price = item.get("price")
        price_text = f"¥{float(price):g}" if isinstance(price, (int, float)) else "价格见详情"
        evidence_text = _brief_evidence(item)
        brand_text = f"（{brand}）" if brand else ""
        lines.append(f"{label}：{title_text}{brand_text}{price_text}。{evidence_text}")
    lines.append("路线和天气不同，想把其中一类换成更轻便或更省预算的选择，告诉松仔就行～")
    return "\n".join(lines)


def _brief_evidence(item: dict) -> str:
    knowledge = item.get("rag_knowledge") or {}
    if isinstance(knowledge, dict):
        text = str(knowledge.get("marketing_description") or "").strip()
        if text:
            return text[:80]
    text = str(item.get("description") or "").strip()
    return text[:80] if text else "适合纳入这项准备。"
