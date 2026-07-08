import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    audit_log,
    auth,
    callback,
    dashboard,
    deployments,
    disk_layouts,
    hypervisors,
    iso_assets,
    notifications,
    orgs,
    settings,
    setup,
    templates,
    users,
    webhooks,
)

logger = logging.getLogger("deploycore")

app = FastAPI(title="DeployCore API")


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
app.include_router(templates.router)
app.include_router(deployments.router)
app.include_router(callback.router)
app.include_router(settings.router)
app.include_router(audit_log.router)
app.include_router(dashboard.router)
app.include_router(notifications.router)
app.include_router(notifications.prefs_router)
app.include_router(webhooks.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
