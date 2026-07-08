import secrets
import uuid

from redis.asyncio import Redis

from app.config import get_settings

SESSION_KEY_PREFIX = "session:"
USER_SESSIONS_KEY_PREFIX = "user_sessions:"


async def create_session(redis: Redis, user_id: uuid.UUID) -> str:
    """Issues a new session id, recording it in Redis so it can be revoked
    before its JWT would otherwise expire. Also tracked in a per-user set so
    "log out everywhere" / force-logout can find every live session."""
    session_id = secrets.token_urlsafe(16)
    ttl_seconds = get_settings().access_token_expire_minutes * 60
    await redis.set(f"{SESSION_KEY_PREFIX}{session_id}", str(user_id), ex=ttl_seconds)
    await redis.sadd(f"{USER_SESSIONS_KEY_PREFIX}{user_id}", session_id)
    return session_id


async def revoke_session(redis: Redis, session_id: str) -> None:
    await redis.delete(f"{SESSION_KEY_PREFIX}{session_id}")


async def revoke_all_sessions(redis: Redis, user_id: uuid.UUID) -> None:
    key = f"{USER_SESSIONS_KEY_PREFIX}{user_id}"
    session_ids = await redis.smembers(key)
    if session_ids:
        await redis.delete(*(f"{SESSION_KEY_PREFIX}{sid}" for sid in session_ids))
    await redis.delete(key)
