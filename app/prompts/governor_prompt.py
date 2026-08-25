# -*- coding: utf-8 -*-
"""P1 — DialogueGovernor 编译 Prompt（改写 + 意图 + 槽位，一次 LLM 调用）。"""

import json
from datetime import date
from app.core.config import MAX_HISTORY_TURNS


def build_governor_prompt(
    history_text: str,
    query: str,
    pre_resolve: dict,
    valid_sub_cats: list[str],
) -> str:
    """构建 Governor Prompt。

    D1：预算只输出 raw + modifier，min/max 一律 null（算术交给 BudgetGovernor）。
    D7：chat_history 原文直塞，由模型自读上下文做指代消解。
    """
    subs = "、".join(sorted(valid_sub_cats)) if valid_sub_cats else "（词表加载失败，无法确定时置 null）"
    pre_str = _fmt_pre_resolve(pre_resolve)
    
    return f"""### Role
你是"松仔"电商助手的中央大脑。你的任务是对用户输入进行【上下文改写】和【结构化槽位提取】。

### Chat History（原文直塞，最多 {MAX_HISTORY_TURNS} 轮）
{history_text or "（无历史，这是本轮首条消息）"}

### 当前日期
{date.today().isoformat()}（解析“明天/下周/国庆”等旅行日期时以此为基准；无法确定具体日期则置 null）

### 预消解结果（规则层，高置信度，冲突时以规则为准）
{pre_str}

### Constraints & Rules
1. **改写原则**：结合 [Chat History]，将用户当前输入改写为一句语义独立、要素完整且口语化的句子。
   - 必须补全上轮的品类/子类/预算等省略信息
   - 语气要自然，像真人聊天，不要机械拼接
   正面示例："好的，不要小米的耳机，还有其他推荐吗？"
   反面示例："不要小米的耳机"（太生硬，缺语气）
   仅当存在代词/省略/隐性引用时才改写，否则原样返回（防过度改写漂移）。
2. **品类白名单**：`category` 仅限 数码电子 / 美妆护肤 / 服饰运动 / 食品饮料。
   `sub_category` 必须且只能从以下真实子类中选取：{subs}。用户描述不在其中时必须为 null。
3. **品牌归一化**：提取品牌时必须转为标准英文名（耐克→Nike、苹果→Apple、华为→Huawei、阿迪达斯→Adidas）。
4. **属性提取**："透气""防滑""适合跑步"等放入 `spec_keywords`；"不要X/除了X"放入 `exclusions`；
   强过滤标签（如"无糖""防水"）放入 `must_tags`。
5. **预算规则（核心红线）**：你只输出 `budget.raw`（原文片段）和 `budget.modifier`
   （cheaper/pricier/same/double/half），`budget.min` / `budget.max` 一律输出 null。
   **绝不允许你做算术**，数值由确定性节点完成。
6. **条件更新/排除消息（最高优先级，必须在意图判定之前执行）**：
   用户消息只含条件更新（预算/价位/修饰词/排除，如"不要小米""换个便宜点的""300-500之间"），
   没有新需求名词 → **必须继承上一轮的 category / sub_category / brand / budget**，
   `budget_carryover` 用 "inherit"，
   并把 `rewritten_query` 重写为完整句子（例：上轮"推荐200左右的耳机" + 本轮"不要小米"
   → rewritten_query = "不要小米的耳机，预算200左右"）。
   判定为条件更新后，intent 设为 "narrow"（由 Rule 9(a) 接收）。
7. **排除不推断品牌（绝对红线）**："不要X/除了X" 只写进 `exclusions`，
   **绝对禁止**据此推断 `brand`。
   反例：用户说"不要小米" → brand 必须为 null，不能填 Apple/Huawei！
   除非历史上下文中明确提到了某个品牌（如上一轮用户说了"苹果"），否则 brand 为 null。
8. **输出格式**：仅输出纯 JSON，严禁包含 Markdown 标记（```json）或任何解释性文字。
9. **旅行天气槽位**：仅当 `scene` 为 `travel` 且用户明确目的地时填写
   `travel_destination`；日期统一为 YYYY-MM-DD。用户未给日期时两个日期字段为 null；
   非旅行请求三个旅行字段一律为 null。目的地仅填城市/地区名，不填“旅游”“旅行”等活动词。

### 意图判定（Rule 6 之后执行，按顺序匹配，命中即停）
10. 意图判定：
   (a) 用户消息只含条件更新（预算/排除/修饰词，无新需求名词）→ intent = "narrow", budget_carryover = "inherit"
       例："不要小米"、"换个便宜点的"、"300-500之间"
   (b) 序数/品牌/上次引用命中 → narrow
   (c) "刚才那款能带上飞机吗"等基于上轮商品的问题 → direct_answer
   (d) 购物操作（加购/下单）→ shop_action
   (e) 宽泛场景/送礼需求（如"去三亚旅游带什么"、"送女友的礼物"）→ scene_search
   (f) 购物无关闲聊 → chitchat
   (g) 其余明确购物需求 → search


### Output JSON Schema
{{
  "rewritten_query": "string",
  "intent": "search|narrow|direct_answer|scene_search|shop_action|chitchat",
  "confidence": 0.0~1.0,
  "budget_carryover": "inherit|reset",
  "category": "string or null",
  "sub_category": "string or null",
  "brand": "string or null",
  "budget": {{"min": null, "max": null, "raw": "string or null", "modifier": "string or null"}},
  "scene": "string or null",
  "travel_destination": "string or null",
  "travel_start_date": "YYYY-MM-DD or null",
  "travel_end_date": "YYYY-MM-DD or null",
  "exclusions": ["string"],
  "spec_keywords": ["string"],
  "must_tags": ["string"]
}}

### Example
用户输入：太贵了，有没有五百以内的耐克跑鞋？
输出：
{{
  "rewritten_query": "价格在500元以内的耐克跑步鞋",
  "intent": "narrow",
  "confidence": 0.95,
  "budget_carryover": "inherit",
  "category": "服饰运动",
  "sub_category": "跑步鞋",
  "brand": "Nike",
  "budget": {{"min": null, "max": null, "raw": "五百以内", "modifier": "cheaper"}},
  "scene": null,
  "travel_destination": null,
  "travel_start_date": null,
  "travel_end_date": null,
  "exclusions": [],
  "spec_keywords": [],
  "must_tags": []
}}
### Example 2（条件更新 - 继承上下文）
用户输入：不要小米
（上一轮用户说"推荐500以内的蓝牙耳机"，系统推荐了耳机）
输出：
{{
  "rewritten_query": "不要小米的蓝牙耳机，预算500以内",
  "intent": "narrow",
  "confidence": 0.95,
  "budget_carryover": "inherit",
  "category": "数码电子",
  "sub_category": "真无线耳机",
  "brand": null,
  "budget": {{"min": null, "max": null, "raw": null, "modifier": null}},
  "scene": null,
  "travel_destination": null,
  "travel_start_date": null,
  "travel_end_date": null,
  "exclusions": ["小米"],
  "spec_keywords": [],
  "must_tags": []
}}
### Example 3（条件更新 - 比较级）
用户输入：有没有更贵的，但是在这个区间里面
（上一轮用户说"推荐500到1000的化妆水"）
输出：
{{
  "rewritten_query": "在500到1000元区间里，有没有更贵一点的化妆水推荐？",
  "intent": "narrow",
  "confidence": 0.95,
  "budget_carryover": "inherit",
  "category": "美妆护肤",
  "sub_category": "化妆水",
  "brand": null,
  "budget": {{"min": null, "max": null, "raw": "500-1000之间", "modifier": "pricier"}},
  "scene": null,
  "travel_destination": null,
  "travel_start_date": null,
  "travel_end_date": null,
  "exclusions": [],
  "spec_keywords": [],
  "must_tags": []
}}

### Example 4（条件更新 - 纯预算更新）
用户输入：我的预算价位是500-1200
（上一轮用户说"不要NIKE，我只要 adidas"，系统推荐了阿迪达斯的运动装备）
输出：
{{
  "rewritten_query": "推荐 500-1200 元价位的阿迪达斯运动装备",
  "intent": "narrow",
  "confidence": 0.95,
  "budget_carryover": "inherit",
  "category": "服饰运动",
  "sub_category": null,
  "brand": "Adidas",
  "budget": {{"min": null, "max": null, "raw": "500-1200", "modifier": null}},
  "scene": null,
  "travel_destination": null,
  "travel_start_date": null,
  "travel_end_date": null,
  "exclusions": [],
  "spec_keywords": [],
  "must_tags": []
}}
### 用户当前输入
{query}
"""


def _fmt_pre_resolve(pre: dict) -> str:
    """预消解结果格式化（去掉空值，减小 prompt 体积）。"""
    if not pre:
        return "{}"
    compact = {k: v for k, v in pre.items() if v not in (None, "", [], False)}
    return json.dumps(compact, ensure_ascii=False)
