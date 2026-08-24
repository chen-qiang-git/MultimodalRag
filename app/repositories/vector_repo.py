"""向量仓库 — 工厂重导出。

根据 USE_PG_VECTOR 配置自动选择：
- True  → PgVectorRepository（pgvector 语义向量搜索）
- False → StubVectorRepository（降级，返回空结果）

保持向后兼容：`from app.repositories.vector_repo import VectorRepository` 仍然可用。
"""

from app.core.config import USE_PG_VECTOR
from app.repositories.base_vector_repo import BaseVectorRepository
from app.repositories.pg_vector_repo import PgVectorRepository
from app.repositories.stub_vector_repo import StubVectorRepository

if USE_PG_VECTOR:
    VectorRepository = PgVectorRepository()  # type: ignore[assignment]
else:
    VectorRepository = StubVectorRepository()  # type: ignore[assignment]


def get_vector_repo() -> BaseVectorRepository:
    """返回当前活动的向量仓库实例。"""
    if USE_PG_VECTOR:
        return PgVectorRepository()
    return StubVectorRepository()
