import uuid

from pydantic import BaseModel


class OrgOverview(BaseModel):
    org_id: uuid.UUID
    org_name: str
    running: int
    completed: int
    failed: int
    hypervisors_ok: int
    hypervisors_total: int
