# -*- coding: utf-8 -*-
"""shop_action 节点 — 对话内购物车完整闭环。

子动作（按优先级）：
  clear（清空） > remove（删除） > update_qty（改数量） > add（加购） > checkout（结算） > view（查看）
"""

import logging
import re

from app.governor import preresolve
from app.repositories.pg_cart_repo import get_cart_repo
from app.schemas.agent_state import AgentState
from app.schemas.cart import CartItemUpdate, DEMO_USER_ID
from app.services.shop_service import add_to_cart, checkout

logger = logging.getLogger(__name__)

_CLEAR_WORDS = ("清空", "全部删", "全部移", "都删")
_REMOVE_WORDS = ("删", "去掉", "移除", "不要了", "不想要")
_QTY_WORDS = ("数量", "加一件", "减一件", "再加", "改成")
_ADD_WORDS = ("加入购物车", "加购", "加进购物车", "放购物车", "买了")
_CHECKOUT_WORDS = ("结算", "下单", "付款", "结账", "支付")
_VIEW_WORDS = ("看看", "查看", "有什么", "有哪些", "多少钱", "总价", "合计", "几件", "购物车", "我的车")


def _detect_action(q: str) -> str:
    if any(k in q for k in _CLEAR_WORDS):
        return "clear"
    if any(k in q for k in _REMOVE_WORDS):
        return "remove"
    if any(k in q for k in _QTY_WORDS):
        return "update_qty"
    if any(k in q for k in _ADD_WORDS):
        return "add"
    if any(k in q for k in _CHECKOUT_WORDS):
        return "checkout"
    if any(k in q for k in _VIEW_WORDS):
        return "view"
    return "add"  # 默认加购（原行为）


async def shop_action_node(state: AgentState) -> AgentState:
    q = state.user_input or ""
    action = _detect_action(q)
    user_id = state.user_id or DEMO_USER_ID
    cart_repo = get_cart_repo()

    if action == "view":
        state.final_response = _view_cart(cart_repo, user_id, q)
    elif action == "clear":
        cart_repo.clear_cart(user_id)
        state.final_response = "已帮你清空购物车～有想买的随时告诉我！"
    elif action == "remove":
        state.final_response = _remove_cart_item(state, cart_repo, user_id, q)
    elif action == "update_qty":
        state.final_response = _update_quantity(state, cart_repo, user_id, q)
    elif action == "checkout":
        res = checkout(user_id)
        if res["ok"]:
            state.final_response = (
                f"下单成功！订单号 {res['order_id']}，共 {res['count']} 件，"
                f"合计 ¥{res['total']:.2f}（模拟支付，未真实扣款）～"
            )
        else:
            state.final_response = res.get("message") or "购物车还是空的，先加购再结算吧～"
    else:  # add
        pid = _resolve_add_target(state)
        if not pid:
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
        "action": action,
        "input_summary": q[:60],
        "output_summary": state.final_response[:60],
        "latency_ms": 0,
        "status": "success",
    })
    return state


# ================================================================
# 子动作实现
# ================================================================

def _view_cart(cart_repo, user_id: str, q: str) -> str:
    cart = cart_repo.get_cart(user_id)
    items = cart.items
    if not items:
        return "你的购物车还是空的～想买什么跟我说，加购后随时来看～"
    if any(k in q for k in ("多少钱", "总价", "合计")):
        selected = [i for i in items if i.selected]
        total = sum(i.price * i.quantity for i in selected)
        return f"购物车合计 ¥{total:.2f}（已勾选 {len(selected)} 件）～"
    if any(k in q for k in ("几件", "多少件")):
        return f"购物车共有 {len(items)} 件商品～"
    lines = [f"你的购物车有 {len(items)} 件商品："]
    for idx, i in enumerate(items, 1):
        mark = "✅" if i.selected else "☑️"
        lines.append(f"{mark} {idx}. {i.title[:32]}（{i.brand}）¥{i.price:.0f} ×{i.quantity}")
    total = sum(i.price * i.quantity for i in items if i.selected)
    lines.append(f"合计（已勾选）¥{total:.2f}～要结算、删除或改数量都可以告诉我～")
    return "\n".join(lines)


def _remove_cart_item(state, cart_repo, user_id: str, q: str) -> str:
    cart = cart_repo.get_cart(user_id)
    target = _resolve_cart_target(state, cart.items, q)
    if not target:
        return "想删掉哪一件呢？告诉豆仔是第几个，或者直接说商品名～"
    cart_repo.remove_item(target.cart_item_id, user_id)
    remain = len(cart_repo.get_cart(user_id).items)
    return f"已帮你把【{target.title[:32]}】移出购物车，剩余 {remain} 件～"


def _update_quantity(state, cart_repo, user_id: str, q: str) -> str:
    cart = cart_repo.get_cart(user_id)
    target = _resolve_cart_target(state, cart.items, q)
    if not target:
        return "想改哪一件的数量呢？告诉豆仔是第几个，或者直接说商品名～"
    new_qty = _parse_new_quantity(q, target.quantity)
    if new_qty is None:
        return f"【{target.title[:32]}】现在是 {target.quantity} 件，想改成几件呀？"
    cart_repo.update_item(target.cart_item_id, CartItemUpdate(quantity=new_qty), user_id)
    return f"已把【{target.title[:32]}】数量改成 {new_qty} 件～"


def _resolve_add_target(state) -> str | None:
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
    return pid


def _resolve_cart_target(state, items, q: str):
    """定位购物车条目：序数 > 指代商品ID > 标题/品牌子串。"""
    if not items:
        return None
    m = preresolve._ORDINAL_PATTERN.search(q)
    if m:
        idx = preresolve._CN_NUM.get(m.group(1))
        if idx is not None and idx < len(items):
            return items[idx]
    pid = state.slots.resolved_product_id or state.slots.rule_resolved_product_id
    if pid:
        for i in items:
            if i.product_id == pid:
                return i
    ql = q.lower()
    for i in items:
        if i.title and len(i.title) >= 2 and i.title[:6].lower() in ql:
            return i
        if i.brand and i.brand.lower() in ql:
            return i
    return None


def _parse_new_quantity(q: str, current: int) -> int | None:
    m = re.search(r"改成\s*(\d+)", q)
    if m:
        return max(1, int(m.group(1)))
    if "加一件" in q or "再加" in q:
        return current + 1
    if "减一件" in q:
        return max(1, current - 1)
    m = re.search(r"数量\s*(\d+)", q)
    if m:
        return max(1, int(m.group(1)))
    return None
