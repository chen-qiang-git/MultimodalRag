"""Conversation & ConversationMessage SQLAlchemy ORM models.
基于 SQLAlchemy（Python 中最流行的 ORM 框架之一）编写的数据库模型定义文件。它专门用于在关系型数据库（从代码中可以看出是 PostgreSQL）中构建和管理一个 AI 对话系统 的核心数据表。
具体来说，它定义了两个核心的数据表模型：
1. ConversationModel (对话/会话模型)
这个模型对应数据库中的 conversations 表，用于记录用户与 AI 之间的每一次“会话”或“聊天窗口”的元数据。
2. ConversationMessageModel (对话消息模型)
这个模型对应数据库中的 conversation_messages 表，用于记录对话中的每一条具体消息。
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class ConversationModel(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _new_id("CONV"))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")  # active / archived
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_snapshot: Mapped[dict] = mapped_column("context_snapshot", JSONB, default=dict)
    last_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ConversationMessageModel(Base):
    __tablename__ = "conversation_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _new_id("MSG"))
    conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user / assistant / system
    content: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_refs: Mapped[list] = mapped_column(JSONB, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list)
    memory_refs: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    extra_data: Mapped[dict] = mapped_column("extra_data", JSONB, default=dict)

    __table_args__ = (
        Index("ix_cmessages_conv_id_created", "conversation_id", "created_at"),
    )
