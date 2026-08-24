"""SQLAlchemy ORM models — 新仓库仅保留 Agent 核心所需模型（对齐原库原表，D6a）。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.product import ProductModel
from app.models.cart_item import CartItemModel
from app.models.order import OrderModel
from app.models.conversation import ConversationModel, ConversationMessageModel
from app.models.user_preference_entry import UserPreferenceEntry
from app.models.product_embedding import ProductEmbeddingModel, ProductChunkEmbeddingModel

__all__ = [
    "Base",
    "ProductModel",
    "CartItemModel",
    "OrderModel",
    "ConversationModel", "ConversationMessageModel",
    "UserPreferenceEntry",
    "ProductEmbeddingModel", "ProductChunkEmbeddingModel",
]
