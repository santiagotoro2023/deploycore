import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_column(enum_cls, name: str, **kwargs):
    """SQLAlchemy's Enum type persists member .name by default; every enum
    column in this app instead stores lowercase .value strings, matching
    what the Alembic migrations declare as the native postgres type."""
    return mapped_column(
        SAEnum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e]),
        **kwargs,
    )


class Base(DeclarativeBase):
    pass


class UUIDPKMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
