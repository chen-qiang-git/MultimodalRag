# -*- coding: utf-8 -*-
"""shop_action 节点 — 对话内"加入购物车 / 结算下单"闭环。"""

import logging

from app.schemas.agent_state import AgentState
from app.schemas.cart import DEMO_USER_ID
from app.services.shop_service import add_to_cart, checkout

logger = logging.getLogger(__name__)

_CHECKOUT_WORDS = ("结算", "下单", "买下", "付款", "结账")


async def shop_action_node(state: AgentState) -> AgentState:
    slots = state.slots
    pid = (
        slots.resolved_product_id
        or slots.rule_resolved_product_id
        or (state.candidate_ids[0] if state.candidate_ids else None)
    )
    if not pid:
        last_products = (state.context_snapshot or {}).get("last_products") or []
        if last_products and isinstance(last_products[0], dict):
            pid = last_products[0].get("product_id")

    user_id = state.user_id or DEMO_USER_ID
    q = state.user_input or ""
    is_checkout = any(k in q for k in _CHECKOUT_WORDS)

    if is_checkout:
        res = checkout(user_id)
        if res["ok"]:
            state.final_response = (
                f"下单成功！订单号 {res['order_id']}，共 {res['count']} 件，"
                f"合计 ¥{res['total']:.2f}（模拟支付，未真实扣款）～"
            )
        else:
            state.final_response = res.get("message") or "购物车还是空的，先加购再结算吧～"
    elif not pid:
        state.final_response = "你想把哪一款加入购物车呀？告诉豆仔是第几个，或者直接说商品名～"
    else:
        res = add_to_cart(user_id, pid)
        if res["ok"]:
            state.final_response = (
                f"搞定～已把【{res['title']}】（¥{res['price']:.0f}）加入购物车，"
                f"当前共 {res['cart_count']} 件。要结算的话跟我说'结算'哦！"
            )
        else:
            state.final_response = "抱歉，豆仔没找到这款商品，换个说法试试～"

    state.trace_steps.append({
        "step_id": f"T{len(state.trace_steps) + 1:03d}",
        "agent_name": "Shop Action Node",
        "action": "checkout" if is_checkout else "add_to_cart",
        "input_summary": q[:60],
        "output_summary": state.final_response[:60],
        "latency_ms": 0,
        "status": "success",
    })
    return state
