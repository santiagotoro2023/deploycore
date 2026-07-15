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

    # Remote Management (self-hosted RustDesk stack, see remote-agent/README.md
    # and services/remote_desktop.py). internal_url is what the DeployCore API
    # container uses to reach the rustdesk-api server over the compose network
    # (server-to-server); public_url is what the operator's browser uses to
    # load the embedded web client iframe, so it has to be reachable from
    # outside Docker (defaults to the same host APP_PUBLIC_URL is on, on the
    # rustdesk-api port). The admin credentials are the service account
    # DeployCore logs in as to mint per-session share links; they must match
    # the RUSTDESK_API admin account created in the rustdesk stack.
    rustdesk_api_internal_url: str = "http://rustdesk:21114"
    rustdesk_api_public_url: str = "http://localhost:21114"
    rustdesk_admin_username: str = "admin"
    rustdesk_admin_password: str = ""
    # The address remote agents and the browser use to reach the RustDesk
    # relay/rendezvous servers - shown in the setup banner's port-forwarding
    # guidance. Same value docker-compose passes to the rustdesk container.
    rustdesk_relay_host: str = "localhost"
    # The hbbs-generated public key the agent must trust to connect to this
    # instance's self-hosted server. Read from the rustdesk container's data
    # volume (mounted read-only into the api container in docker-compose) and
    # handed to agents at enrollment time so nothing has to be copied by hand.
    rustdesk_key_file: str = "/rustdesk-server/id_ed25519.pub"
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
