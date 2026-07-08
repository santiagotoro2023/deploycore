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

    access_token_expire_minutes: int = 12 * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
