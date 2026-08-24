"""pgvector 向量表 — 产品级与分块级 Embedding 存储。

与业务数据同库（PostgreSQL），替代独立的 Qdrant 服务：
- product_embeddings:        每件商品一个向量（产品级检索）
- product_chunk_embeddings:  每件商品拆成 summary/mkt/faq/rev 块（块级检索 + 产品聚合）

向量维度与 model_config.yaml 中 text_embedding 的 dimensions 保持一致（默认 1024）。
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, Numeric, String, Text
from pgvector.sqlalchemy import Vector

from app.core.config import EMBEDDING_DIMENSION
from app.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProductEmbeddingModel(Base):
    """产品级 Embedding — 对应原 Qdrant products collection。"""

    __tablename__ = "product_embeddings"

    product_id: str = Column(String(64), primary_key=True)
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=False)
    embedding_text: str = Column(Text, nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), default=_now)
    updated_at: datetime = Column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    __table_args__ = (
        Index(
            "ix_product_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self):
        return f"<ProductEmbedding {self.product_id}>"


class ProductChunkEmbeddingModel(Base):
    """分块级 Embedding — 对应原 Qdrant product_chunks collection。"""

    __tablename__ = "product_chunk_embeddings"

    chunk_id: str = Column(String(128), primary_key=True)
    product_id: str = Column(String(64), nullable=False, index=True)
    chunk_type: str = Column(String(16), nullable=False)
    chunk_index: int = Column(Integer, nullable=False, default=0)
    text: str = Column(Text, nullable=False)
    title: str | None = Column(Text, nullable=True)
    brand: str | None = Column(String(128), nullable=True)
    category: str | None = Column(String(64), nullable=True, index=True)
    sub_category: str | None = Column(String(64), nullable=True)
    price = Column(Numeric(10, 2), nullable=True)
    faq_question: str | None = Column(Text, nullable=True)
    review_rating: int | None = Column(Integer, nullable=True)
    review_nickname: str | None = Column(String(128), nullable=True)
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), default=_now)
    updated_at: datetime = Column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    __table_args__ = (
        Index(
            "ix_product_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self):
        return f"<ProductChunkEmbedding {self.chunk_id}>"
