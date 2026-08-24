"""pgvector 向量仓库 — 使用 PostgreSQL 存储与检索 Embedding。

替代原 QdrantVectorRepository：向量与业务数据同库，天然一致。
- search_similar:    HNSW 余弦近邻搜索（product_embeddings 表）
- store_embeddings:  批量写入（upsert）
- health_check:      连通性检查
"""

import logging

from sqlalchemy import text

from app.core.config import PRODUCT_VECTOR_TABLE
from app.core.database import get_session_sync, run_async
from app.repositories.base_vector_repo import BaseVectorRepository

logger = logging.getLogger(__name__)


class PgVectorRepository(BaseVectorRepository):
    """PostgreSQL (pgvector) 向量仓库。"""

    def __init__(self):
        self._table = PRODUCT_VECTOR_TABLE

    async def _asearch_similar(
        self, query_vector: list[float], top_k: int = 10
    ) -> list[dict]:
        factory = get_session_sync()
        if factory is None:
            return []
        vec_literal = "[" + ",".join(str(x) for x in query_vector) + "]"
        stmt = text(
            f"""
            SELECT product_id, embedding_text,
                   1 - (embedding <=> CAST(:qvec AS vector)) AS score
            FROM {self._table}
            ORDER BY embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        async with factory() as session:
            result = await session.execute(stmt, {"qvec": vec_literal, "top_k": top_k})
            rows = result.fetchall()
        return [
            {
                "product_id": row.product_id,
                "score": float(row.score),
                "payload": {"product_id": row.product_id, "text": row.embedding_text},
            }
            for row in rows
        ]

    async def _astore_embeddings(
        self, texts: list[str], embeddings: list[list[float]]
    ):
        factory = get_session_sync()
        if factory is None:
            return
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.models.product_embedding import ProductEmbeddingModel

        rows = []
        for text_, vector in zip(texts, embeddings):
            parts = text_.split(" | ", 1)
            pid = parts[0] if parts else text_
            rows.append(
                {
                    "product_id": pid,
                    "embedding": vector,
                    "embedding_text": parts[1] if len(parts) > 1 else text_,
                }
            )
        async with factory() as session:
            for row in rows:
                stmt = pg_insert(ProductEmbeddingModel).values(**row)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ProductEmbeddingModel.product_id],
                    set_={
                        "embedding": stmt.excluded.embedding,
                        "embedding_text": stmt.excluded.embedding_text,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                await session.execute(stmt)
            await session.commit()

    async def _ahealth_check(self) -> bool:
        factory = get_session_sync()
        if factory is None:
            return False
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.warning(f"pgvector health check failed: {e}")
            return False

    # ---- 同步接口（与 BaseVectorRepository 约定一致）----

    def search_similar(self, query_vector: list[float], top_k: int = 10) -> list[dict]:
        return run_async(self._asearch_similar(query_vector, top_k))

    def store_embeddings(self, texts: list[str], embeddings: list[list[float]]):
        return run_async(self._astore_embeddings(texts, embeddings))

    def health_check(self) -> bool:
        return run_async(self._ahealth_check())
