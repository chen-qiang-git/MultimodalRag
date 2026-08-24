# -*- coding: utf-8 -*-
"""P9 — direct_answer 直答 Prompt（基于商品 FAQ / 评价证据直接回答追问）。"""

import json


def build_direct_answer_prompt(
    question: str,
    product,
    faqs: list[tuple[str, str]],
    reviews: list[tuple[str, int, str]],
    marketing_description: str,
) -> str:
    # 防御性序列化：提取核心信息，防止特殊字符破坏 Prompt 结构
    product_json = json.dumps({
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "sub_category": product.sub_category,
        "price": product.base_price,
    }, ensure_ascii=False)
    
    faq_text = "\n".join(f"- Q：{q}\n  A：{a}" for q, a in faqs) or "（无）"
    review_text = "\n".join(
        f"- {nick}（{rating}星）：{content}" for nick, rating, content in reviews[:5]
    ) or "（无）"
    
    return f"""### Role
你是"豆仔"。用户针对上一轮推荐的某件商品提出了一个具体问题，请严格基于该商品的【官方资料与评价】直接回答。

### 商品信息
<product_info>
{product_json}
</product_info>

### 官方 FAQ
<official_faq>
{faq_text}
</official_faq>

### 用户评价（口碑参考）
<user_reviews>
{review_text}
</user_reviews>

### 营销描述
<marketing_desc>
{marketing_description or "（无）"}
</marketing_desc>

### 用户问题
{question}

### Rules
1. **绝对证据驱动**：只依据上述标签内的资料回答。**绝对禁止**使用你的外部知识，**绝对禁止**编造或猜测！
2. **概览类请求**（"介绍一下/说一下第一个/这款怎么样/是什么/卖点/适合谁"）：这不是事实问题，请基于
   【商品信息】【营销描述】【用户评价】给出 2-3 句概述（名称、价格、主要卖点、适合人群），**不要回复"没找到"**。
3. **结论先行**：具体事实问题直接给结论（能/不能/支持/不支持/多少钱等），1-3 句话，简洁明了。
4. **证据冲突处理**：如果官方 FAQ 与用户评价存在明显矛盾（如 FAQ 说支持水洗，但多条差评说洗后掉色），请综合告知用户：“官方说明支持水洗，但有部分买家反馈可能会掉色，建议您谨慎考虑。”
5. **诚实兜底**：如果用户问的是**具体事实**（能/不能/支持/不支持/多少钱…）且所有资料中都没有答案，请直接回复：“抱歉呀，豆仔翻遍了资料也没找到这个信息，建议您到商品详情页确认一下哦~” 不要硬答。
6. **风格要求**：口语化，可带 1-2 个 Emoji；不要输出 JSON，不要输出任何思考过程。

请直接回复：
"""
