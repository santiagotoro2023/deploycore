from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret_key: str
    database_url: str
    redis_url: str
    app_public_url: str = "http://localhost:8000"
    iso_storage_path: str = "/data/isos"
    iso_build_tmp: str = "/data/iso_build_tmp"
    app_asset_storage_path: str = "/data/app_assets"
    app_asset_build_tmp: str = "/data/app_asset_build_tmp"
    backup_dir: str = "/data/backups"
    tls_certs_path: str = "/data/tls"

    access_token_expire_minutes: int = 12 * 60

    # Remote Management (native agent, see remote-agent/PROTOCOL.md and
    # services/remote_session.py - no RustDesk anywhere in this any more).
    # guacd is Apache Guacamole's daemon (Connect/RDP mode); internal-only,
    # no credentials of its own. coturn is STUN/TURN for Shadow's WebRTC path
    # when a host isn't on the same LAN as this server - static long-term
    # credentials (see docker-compose.yml's comment on why static, not the
    # rotating REST-API scheme coturn also supports).
    guacd_host: str = "guacd"
    guacd_port: int = 4822
    turn_host: str = "localhost"
    turn_username: str = "deploycore"
    turn_password: str = ""
    # On startup the api container fetches the agent .msi from here (built and
    # published by .github/workflows/build-agent-msi.yml) and seeds it as the
    # global "Remote Agent" App Asset, so the Remote Management download button
    # and auto-install-on-deploy work with no manual upload. Set empty to
    # disable (e.g. air-gapped installs that upload the .msi by hand instead).
    remote_agent_msi_url: str = (
        "https://github.com/santiagotoro2023/deploycore/releases/download/agent-latest/DeployCoreRemoteAgent.msi"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
