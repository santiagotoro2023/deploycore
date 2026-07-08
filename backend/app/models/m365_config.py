from sqlalchemy import Boolean, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.security import crypto


class M365Config(UUIDPKMixin, TimestampMixin, Base):
    """Singleton row (instance-wide, like the MSP logo/instance_name): one
    M365/Entra app registration used to send every outbound email
    notification, not per-organization."""

    __tablename__ = "m365_config"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sender_upn: Mapped[str] = mapped_column(String(320), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def client_secret(self) -> str:
        return crypto.decrypt(self.client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: str) -> None:
        self.client_secret_encrypted = crypto.encrypt(value)
