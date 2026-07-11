from sqlalchemy import Boolean, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.security import crypto


class TeamsConfig(UUIDPKMixin, TimestampMixin, Base):
    """Singleton row (instance-wide, like M365Config), a separate app
    registration/permission grant even though it's commonly the same
    physical Entra app as M365Config's: sending mail (Mail.Send) and
    notifying Teams (TeamsActivity.Send, TeamsAppInstallation.
    ReadWriteForUser.All) are unrelated Graph application permissions,
    kept as independent on/off/test config so one can be enabled without
    the other. See services/teams.py for the real prerequisites this
    implies beyond just an app registration."""

    __tablename__ = "teams_config"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The catalog app ID of the Teams app DeployCore's notifications are
    # sent "as" - Graph's Activity Feed Notification API requires the
    # sending identity be a real Teams app installed for the target user,
    # not just an Entra app registration, so this has to reference one
    # already published to the org's app catalog (see services/teams.py).
    teams_app_id: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def client_secret(self) -> str:
        return crypto.decrypt(self.client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: str) -> None:
        self.client_secret_encrypted = crypto.encrypt(value)
