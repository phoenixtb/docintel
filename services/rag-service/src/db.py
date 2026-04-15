"""
Database module for conversation persistence.
Uses SQLAlchemy with psycopg2 for PostgreSQL access.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    desc,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from .config import get_settings as _get_settings
from .context import _tenant_ctx, _role_ctx

engine = create_engine(
    _get_settings().postgres_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@event.listens_for(engine, "begin")
def _set_rls_on_begin(conn):
    """Set PostgreSQL RLS session variables at the start of each transaction."""
    tenant_id = _tenant_ctx.get()
    role = _role_ctx.get()
    conn.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": tenant_id})
    conn.execute(text("SET LOCAL app.user_role = :role"), {"role": role})


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": "conversations"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False)
    user_id = Column(String(64))
    title = Column(String(500), nullable=False, default="New Conversation")
    session_summary = Column(Text, nullable=True)
    summary_upto_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = {"schema": "conversations"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    sources = Column(JSONB, nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")


# =============================================================================
# Repository functions
# =============================================================================

def get_db() -> Session:
    """Get a database session."""
    return SessionLocal()


def create_conversation(
    tenant_id: str,
    user_id: Optional[str] = None,
    title: str = "New Conversation",
) -> dict:
    """Create a new conversation."""
    with get_db() as db:
        conv = Conversation(tenant_id=tenant_id, user_id=user_id, title=title)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return _conv_to_dict(conv, include_messages=False)


def list_conversations(
    tenant_id: str,
    user_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List conversations for a tenant/user, most recent first."""
    with get_db() as db:
        q = db.query(Conversation).filter(Conversation.tenant_id == tenant_id)
        if user_id:
            q = q.filter(Conversation.user_id == user_id)
        convs = q.order_by(desc(Conversation.updated_at)).offset(offset).limit(limit).all()
        return [_conv_to_dict(c, include_messages=False) for c in convs]


def get_conversation(conversation_id: str, tenant_id: str) -> Optional[dict]:
    """Get a conversation with all its messages."""
    with get_db() as db:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            .first()
        )
        if not conv:
            return None
        return _conv_to_dict(conv, include_messages=True)


def delete_conversation(conversation_id: str, tenant_id: str) -> bool:
    """Delete a conversation and all its messages."""
    with get_db() as db:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            .first()
        )
        if not conv:
            return False
        db.delete(conv)
        db.commit()
        return True


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    tenant_id: Optional[str] = None,
    sources: Optional[list[dict]] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Add a message to a conversation. Also updates conversation title and updated_at."""
    with get_db() as db:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sources=sources,
            metadata_=metadata or {},
        )
        db.add(msg)

        # Enforce tenant ownership when tenant_id is provided
        query = db.query(Conversation).filter(Conversation.id == conversation_id)
        if tenant_id:
            query = query.filter(Conversation.tenant_id == tenant_id)
        conv = query.first()
        if conv:
            conv.updated_at = datetime.now(timezone.utc)
            # Auto-title from first user message
            if role == "user" and conv.title == "New Conversation":
                conv.title = content[:100] + ("..." if len(content) > 100 else "")

        db.commit()
        db.refresh(msg)
        return _msg_to_dict(msg)


def get_conversation_summary_state(conversation_id: str, tenant_id: str) -> dict:
    """
    Lightweight fetch: returns summary state + total user/assistant message count.
    Does NOT load all message content — avoids the N+1 problem on large conversations.
    """
    with get_db() as db:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            .first()
        )
        if not conv:
            return {"session_summary": None, "summary_upto_count": 0, "total_message_count": 0}

        total = (
            db.query(func.count(Message.id))
            .filter(
                Message.conversation_id == conv.id,
                Message.role.in_(["user", "assistant"]),
            )
            .scalar()
        ) or 0

        return {
            "session_summary": conv.session_summary,
            "summary_upto_count": conv.summary_upto_count,
            "total_message_count": total,
        }


def get_messages_slice(
    conversation_id: str,
    tenant_id: str,
    offset: int,
    limit: int,
) -> list[dict]:
    """Fetch a slice of user/assistant messages by position (oldest-first)."""
    with get_db() as db:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            .first()
        )
        if not conv:
            return []
        msgs = (
            db.query(Message)
            .filter(
                Message.conversation_id == conv.id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [{"role": m.role, "content": m.content} for m in msgs]


def update_conversation_summary(
    conversation_id: str,
    tenant_id: str,
    summary: str,
    upto_count: int,
) -> None:
    """Persist the updated rolling summary and the message count it covers."""
    with get_db() as db:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            .first()
        )
        if conv:
            conv.session_summary = summary
            conv.summary_upto_count = upto_count
            db.commit()


def get_recent_messages(
    conversation_id: str,
    tenant_id: str,
    limit: int,
) -> list[dict]:
    """Fetch the most recent N user/assistant messages (newest-last order)."""
    with get_db() as db:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            .first()
        )
        if not conv:
            return []
        msgs = (
            db.query(Message)
            .filter(
                Message.conversation_id == conv.id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{"role": m.role, "content": m.content} for m in reversed(msgs)]


def update_conversation_title(conversation_id: str, tenant_id: str, title: str) -> Optional[dict]:
    """Update a conversation's title."""
    with get_db() as db:
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
            .first()
        )
        if not conv:
            return None
        conv.title = title
        db.commit()
        db.refresh(conv)
        return _conv_to_dict(conv, include_messages=False)


# =============================================================================
# Serialization helpers
# =============================================================================

def _conv_to_dict(conv: Conversation, include_messages: bool = False) -> dict:
    d = {
        "id": str(conv.id),
        "tenant_id": conv.tenant_id,
        "user_id": conv.user_id,
        "title": conv.title,
        "summary_upto_count": conv.summary_upto_count,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }
    if include_messages:
        d["messages"] = [_msg_to_dict(m) for m in conv.messages]
    return d


def _msg_to_dict(msg: Message) -> dict:
    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "role": msg.role,
        "content": msg.content,
        "sources": msg.sources,
        "metadata": msg.metadata_,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
