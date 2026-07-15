import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    app_assets,
    audit_log,
    auth,
    callback,
    dashboard,
    deployments,
    disk_layouts,
    hypervisors,
    iso_assets,
    managed_hosts,
    notifications,
    orgs,
    remote_agent,
    settings,
    setup,
    templates,
    users,
    webhooks,
)

# Nothing else in the app ever configures logging, and uvicorn's own
# default config (see its LOGGING_CONFIG) only attaches handlers to its
# own uvicorn/uvicorn.error/uvicorn.access loggers, not the root one. Every
# app-level logger.info/.warning (getLogger(__name__), the normal pattern
# throughout app/services/*) was silently going nowhere, only .error and
# above happened to surface at all via Python's WARNING-level "handler of
# last resort". This is what makes those actually show up in
# `docker compose logs api`.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger("deploycore")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort, fire-and-forget so a slow/absent GitHub release never delays
    # or blocks startup - the seed logs its own outcome and simply retries on
    # the next start if it couldn't complete (see remote_agent_seed).
    async def _seed() -> None:
        try:
            from app.db import SessionLocal
            from app.services.remote_agent_seed import ensure_agent_asset_seeded

            async with SessionLocal() as db:
                await ensure_agent_asset_seeded(db)
        except Exception:  # noqa: BLE001 - never let a startup side-task take the API down
            logger.exception("agent asset seed task failed")

    task = asyncio.create_task(_seed())
    yield
    task.cancel()


app = FastAPI(title="DeployCore API", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Without this, an unhandled exception falls through to Starlette's
    default plain-text "Internal Server Error" body, which is not JSON and
    breaks every frontend call site that expects one (they all parse the
    response as JSON). This keeps every error the API returns, expected or
    not, in the same {detail: ...} shape, and still logs the real
    traceback server-side for debugging."""
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "An unexpected server error occurred."})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(users.router)
app.include_router(hypervisors.router)
app.include_router(disk_layouts.router)
app.include_router(iso_assets.router)
app.include_router(app_assets.router)
app.include_router(templates.router)
app.include_router(deployments.router)
app.include_router(callback.router)
app.include_router(settings.router)
app.include_router(audit_log.router)
app.include_router(dashboard.router)
app.include_router(notifications.router)
app.include_router(notifications.prefs_router)
app.include_router(webhooks.router)
app.include_router(managed_hosts.router)
app.include_router(remote_agent.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
