# -*- coding: utf-8 -*-
"""购物车/结算服务 — 供对话内 shop_action 节点与 API 复用（同步接口）。"""

import logging
import uuid
from datetime import datetime, timezone

from app.core.database import get_session_sync
from app.models.order import OrderModel
from app.repositories.pg_cart_repo import get_cart_repo
from app.repositories.product_repo import get_product_repo
from app.schemas.cart import CartItemCreate, DEMO_USER_ID

logger = logging.getLogger(__name__)


def add_to_cart(user_id: str, product_id: str, quantity: int = 1) -> dict:
    """加购：返回 {ok, cart_item, cart_count, title, price}。"""
    repo = get_product_repo()
    product = repo.get_by_id(product_id)
    if not product:
        return {"ok": False, "message": "product not found"}

    cart_repo = get_cart_repo()
    cart_item = cart_repo.add_item(
        CartItemCreate(product_id=product_id, quantity=quantity),
        user_id=user_id or DEMO_USER_ID,
        title=product.title,
        brand=product.brand,
        price=product.base_price,
        image_url=repo.resolve_image_url(product.product_id),
        sku_label="",
    )
    cart = cart_repo.get_cart(user_id or DEMO_USER_ID)
    return {
        "ok": cart_item is not None,
        "cart_item": cart_item,
        "cart_count": len(cart.items),
        "title": product.title,
        "price": product.base_price,
        "product_id": product_id,
    }


def checkout(user_id: str, item_ids: list[str] | None = None) -> dict:
    """结算：创建模拟订单并清空已结算项。返回 {ok, order_id, total, count, message}。"""
    uid = user_id or DEMO_USER_ID
    cart_repo = get_cart_repo()
    cart = cart_repo.get_cart(uid)
    selected = [
        i for i in cart.items
        if i.selected and (not item_ids or i.cart_item_id in item_ids)
    ]
    if not selected:
        return {"ok": False, "order_id": "", "total": 0.0, "count": 0,
                "message": "购物车还是空的，先加购再结算吧～"}

    total = sum(i.price * i.quantity for i in selected)
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    try:
        factory = get_session_sync()
        if factory is not None:
            async def _save():
                async with factory() as session:
                    order = OrderModel(
                        order_id=order_id,
                        user_id=uid,
                        items=[i.model_dump() for i in selected],
                        total_price=total,
                        status="pending",
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(order)
                    await session.commit()
            import asyncio
            asyncio.run(_save())
    except Exception as e:
        logger.warning("Order persist failed: %s", e)

    cart_repo.batch_remove([i.cart_item_id for i in selected], uid)
    return {
        "ok": True,
        "order_id": order_id,
        "total": total,
        "count": len(selected),
        "message": f"模拟结算成功！订单号 {order_id}，共 {len(selected)} 件，合计 ¥{total:.2f}（未执行真实支付）",
    }
