import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column
from app.security import crypto


class Role(str, enum.Enum):
    NONE = "none"
    READONLY = "readonly"
    OPERATOR = "operator"
    ADMIN = "admin"


ROLE_ORDER = {Role.NONE: 0, Role.READONLY: 1, Role.OPERATOR: 2, Role.ADMIN: 3}


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    global_role: Mapped[Role] = enum_column(Role, "role", default=Role.NONE, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def totp_secret(self) -> str | None:
        return crypto.decrypt(self.totp_secret_encrypted) if self.totp_secret_encrypted else None

    @totp_secret.setter
    def totp_secret(self, value: str) -> None:
        self.totp_secret_encrypted = crypto.encrypt(value)


class UserOrgRole(Base):
    __tablename__ = "user_org_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = enum_column(Role, "role", nullable=False)
