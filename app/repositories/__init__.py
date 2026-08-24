"""Repositories 包 — 新仓库：商品/向量/会话/偏好仓库（原库原表直连）。"""

from app.repositories.product_repo import ProductRepository, get_product_repo
from app.repositories.base_product_repo import BaseProductRepository
from app.repositories.json_product_repo import JsonProductRepository
from app.repositories.pg_product_repo import PgProductRepository
from app.repositories.vector_repo import VectorRepository, get_vector_repo
from app.repositories.base_vector_repo import BaseVectorRepository
from app.repositories.stub_vector_repo import StubVectorRepository
from app.repositories.pg_vector_repo import PgVectorRepository
from app.repositories.conversation_repo import ConversationRepository, get_conversation_repo
from app.repositories.user_preference_repo import UserPreferenceRepository, get_user_preference_repo

__all__ = [
    "get_product_repo",
    "ProductRepository",
    "BaseProductRepository",
    "JsonProductRepository",
    "PgProductRepository",
    "get_vector_repo",
    "VectorRepository",
    "BaseVectorRepository",
    "StubVectorRepository",
    "PgVectorRepository",
    "get_conversation_repo",
    "ConversationRepository",
    "get_user_preference_repo",
    "UserPreferenceRepository",
]
