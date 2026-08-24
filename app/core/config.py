import os
from dotenv import load_dotenv

# 确保 .env 在读取配置前加载（无论是直接运行还是被导入）
load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ---- 服务 ----
SERVICE_NAME: str = _env("OMNICART_SERVICE_NAME", "omnicart-agent")
SERVICE_VERSION: str = _env("OMNICART_VERSION", "2.0.0")
HOST: str = _env("OMNICART_HOST", "127.0.0.1")
PORT: int = int(_env("OMNICART_PORT", "8006"))

# ---- Qwen API 密钥（模型名在 model_gateway/model_config.yaml 中管理）----
QWEN_API_KEY: str = _env("QWEN_API_KEY", "")
QWEN_BASE_URL: str = _env("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")

# ---- 检索 ----
DEFAULT_TOP_K: int = int(_env("OMNICART_DEFAULT_TOP_K", "10"))

# ---- Mock Mode ----
MOCK_MODE: bool = _env("OMNICART_MOCK_MODE", "true").lower() == "true"
DEMO_DATA_DIR: str = _env("OMNICART_DEMO_DATA_DIR", "data")

# ---- PostgreSQL ----
DATABASE_URL: str = _env("DATABASE_URL", "")
USE_POSTGRES: bool = bool(DATABASE_URL)

# ---- 向量检索 (pgvector — 与 PostgreSQL 业务数据同库) ----
EMBEDDING_DIMENSION: int = int(_env("EMBEDDING_DIMENSION", "1024"))
PG_VECTOR_ENABLED: bool = _env("OMNICART_USE_PG_VECTOR", "true").lower() == "true"
USE_PG_VECTOR: bool = USE_POSTGRES and PG_VECTOR_ENABLED
PRODUCT_VECTOR_TABLE: str = _env("OMNICART_PRODUCT_VECTOR_TABLE", "product_embeddings")
CHUNK_VECTOR_TABLE: str = _env("OMNICART_CHUNK_VECTOR_TABLE", "product_chunk_embeddings")

# ---- 预算模糊区间系数 (P0-3: "200左右" → min=max*LO, max=max*HI) ----
BUDGET_FUZZ_RATIO_MIN: float = float(_env("OMNICART_BUDGET_FUZZ_MIN", "0.8"))
BUDGET_FUZZ_RATIO_MAX: float = float(_env("OMNICART_BUDGET_FUZZ_MAX", "1.2"))

# ---- D4: 重排混合分（相关度 + 性价比）----
RERANK_VALUE_WEIGHT: float = float(_env("OMNICART_RERANK_VALUE_WEIGHT", "0.25"))
RERANK_RELEVANCE_FLOOR: float = float(_env("OMNICART_RERANK_RELEVANCE_FLOOR", "0.50"))

# ---- P5: Multi-Query 多路变体召回 ----
ENABLE_MULTI_QUERY: bool = _env("OMNICART_ENABLE_MULTI_QUERY", "true").lower() == "true"

# ---- D7: 上下文直塞阈值（直塞 ≤ MAX_HISTORY_TURNS，超过走 P10 兜底）----
MAX_HISTORY_TURNS: int = int(_env("OMNICART_MAX_HISTORY_TURNS", "30"))
MAX_HISTORY_TOKENS: int = int(_env("OMNICART_MAX_HISTORY_TOKENS", "30000"))
COMPRESS_TAIL_TURNS: int = int(_env("OMNICART_COMPRESS_TAIL_TURNS", "10"))

# ---- Chunked Index ----
USE_CHUNKED_INDEX: bool = _env("OMNICART_USE_CHUNKED_INDEX", "true").lower() == "true"

# ---- Fast Mode ----
FAST_MODE: bool = _env("OMNICART_FAST_MODE", "false").lower() == "true"

# ---- Decision Agent: RAG证据评分 ----
ENABLE_DECISION_LLM: bool = _env("OMNICART_ENABLE_DECISION_LLM", "false").lower() == "true"
DECISION_LLM_TIMEOUT: float = float(_env("OMNICART_DECISION_LLM_TIMEOUT", "15.0"))
ENABLE_EVIDENCE_SCORING: bool = _env("OMNICART_ENABLE_EVIDENCE_SCORING", "true").lower() == "true"
SCORE_VERSION: str = _env("OMNICART_SCORE_VERSION", "evidence_scoring_v1")

# ---- Redis ----
REDIS_URL: str = _env("REDIS_URL", "redis://localhost:6379/0")
REDIS_CACHE_TTL_VISUAL: int = int(_env("REDIS_CACHE_TTL_VISUAL", "3600"))
REDIS_CACHE_TTL_SEARCH: int = int(_env("REDIS_CACHE_TTL_SEARCH", "300"))
REDIS_CACHE_TTL_REWRITE: int = int(_env("REDIS_CACHE_TTL_REWRITE", "1800"))
REDIS_CACHE_TTL_WORKFLOW: int = int(_env("REDIS_CACHE_TTL_WORKFLOW", "300"))
USE_REDIS: bool = bool(_env("REDIS_URL", "redis://localhost:6379/0").strip())
