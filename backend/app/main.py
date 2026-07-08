from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    audit_log,
    auth,
    callback,
    dashboard,
    deployments,
    disk_layouts,
    hypervisors,
    iso_assets,
    orgs,
    settings,
    setup,
    templates,
    users,
)

app = FastAPI(title="DeployCore API")

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


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
