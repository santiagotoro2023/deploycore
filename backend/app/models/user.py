import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column


class Role(str, enum.Enum):
    NONE = "none"
    READONLY = "readonly"
    OPERATOR = "operator"
    ADMIN = "admin"


ROLE_ORDER = {Role.NONE: 0, Role.READONLY: 1, Role.OPERATOR: 2, Role.ADMIN: 3}


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    global_role: Mapped[Role] = enum_column(Role, "role", default=Role.NONE, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserOrgRole(Base):
    __tablename__ = "user_org_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = enum_column(Role, "role", nullable=False)
