import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.deployment import Deployment
from app.models.disk_layout import DiskLayout
from app.models.iso_asset import IsoAsset
from app.models.template import DeploymentTemplate
from app.models.user import Role, User
from app.schemas.deployment import AutounattendPreview, DeploymentPreviewRequest
from app.schemas.template import (
    DeploymentTemplateCreate,
    DeploymentTemplateExport,
    DeploymentTemplateImport,
    DeploymentTemplateRead,
    DeploymentTemplateUpdate,
    TemplateExportDiskLayout,
    TemplateExportIsoHint,
)
from app.security.rbac import get_current_user, require_role
from app.services import audit
from app.services.template_render import render_autounattend

router = APIRouter(tags=["templates"])


@router.get(
    "/api/organizations/{org_id}/templates",
    response_model=list[DeploymentTemplateRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_templates(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[DeploymentTemplate]:
    result = await db.execute(
        select(DeploymentTemplate).where(
            or_(DeploymentTemplate.org_id == org_id, DeploymentTemplate.org_id.is_(None))
        )
    )
    return list(result.scalars().all())


@router.post(
    "/api/organizations/{org_id}/templates",
    response_model=DeploymentTemplateRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def create_template(
    org_id: uuid.UUID,
    body: DeploymentTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeploymentTemplate:
    data = body.model_dump()
    data.pop("local_admin_password")
    data.pop("domain_join_credential")
    data["post_install_scripts"] = [s.model_dump() for s in body.post_install_scripts]
    template = DeploymentTemplate(org_id=org_id, **data)
    template.local_admin_password = body.local_admin_password
    if body.domain_join_credential:
        template.domain_join_credential = body.domain_join_credential
    db.add(template)
    await db.flush()
    audit.record(
        db, action="template.create", target_type="template", org_id=org_id,
        user_id=current_user.id, target_id=template.id, detail={"name": template.name},
    )
    await db.commit()
    await db.refresh(template)
    return template


async def _get_org_owned_template(db: AsyncSession, org_id: uuid.UUID, template_id: uuid.UUID) -> DeploymentTemplate:
    template = await db.get(DeploymentTemplate, template_id)
    if template is None or template.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found in this organization")
    return template


async def _get_visible_template(db: AsyncSession, org_id: uuid.UUID, template_id: uuid.UUID) -> DeploymentTemplate:
    """Read access allows both this org's own templates and inherited
    global ones; only the owning org (or a global admin) can mutate."""
    template = await db.get(DeploymentTemplate, template_id)
    if template is None or (template.org_id is not None and template.org_id != org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found in this organization")
    return template


@router.patch(
    "/api/organizations/{org_id}/templates/{template_id}",
    response_model=DeploymentTemplateRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def update_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    body: DeploymentTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeploymentTemplate:
    template = await _get_org_owned_template(db, org_id, template_id)
    updates = body.model_dump(exclude_unset=True)
    if "post_install_scripts" in updates and updates["post_install_scripts"] is not None:
        updates["post_install_scripts"] = [s if isinstance(s, dict) else s.model_dump() for s in updates["post_install_scripts"]]
    local_admin_password = updates.pop("local_admin_password", None)
    domain_join_credential = updates.pop("domain_join_credential", None)
    for field, value in updates.items():
        setattr(template, field, value)
    if local_admin_password:
        template.local_admin_password = local_admin_password
    if domain_join_credential:
        template.domain_join_credential = domain_join_credential
    audit.record(
        db, action="template.update", target_type="template", org_id=org_id,
        user_id=current_user.id, target_id=template.id, detail={"fields": list(updates.keys())},
    )
    await db.commit()
    await db.refresh(template)
    return template


@router.delete(
    "/api/organizations/{org_id}/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def delete_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    template = await _get_org_owned_template(db, org_id, template_id)
    audit.record(
        db, action="template.delete", target_type="template", org_id=org_id,
        user_id=current_user.id, target_id=template.id, detail={"name": template.name},
    )
    await db.delete(template)
    await db.commit()


@router.post(
    "/api/organizations/{org_id}/templates/{template_id}/clone",
    response_model=DeploymentTemplateRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def clone_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeploymentTemplate:
    """Clones a visible template (this org's own, or an inherited global
    one) into a new org-scoped copy, the fastest path to a per-customer
    variant of a shared base template. Credential columns are copied as
    ciphertext bytes: same APP_SECRET_KEY, so no decrypt/re-encrypt needed."""
    source = await _get_visible_template(db, org_id, template_id)
    clone = DeploymentTemplate(
        org_id=org_id,
        name=f"{source.name} (copy)",
        iso_asset_id=source.iso_asset_id,
        disk_layout_id=source.disk_layout_id,
        cpu_count=source.cpu_count,
        ram_mb=source.ram_mb,
        disk_size_gb=source.disk_size_gb,
        network_name=source.network_name,
        vlan_id=source.vlan_id,
        locale=source.locale,
        timezone=source.timezone,
        keyboard_layout=source.keyboard_layout,
        local_admin_password_encrypted=source.local_admin_password_encrypted,
        domain_join_enabled=source.domain_join_enabled,
        domain_fqdn=source.domain_fqdn,
        domain_join_account=source.domain_join_account,
        domain_join_credential_encrypted=source.domain_join_credential_encrypted,
        domain_target_ou=source.domain_target_ou,
        domain_join_timing=source.domain_join_timing,
        windows_features=list(source.windows_features),
        post_install_scripts=list(source.post_install_scripts),
    )
    db.add(clone)
    await db.flush()
    audit.record(
        db, action="template.clone", target_type="template", org_id=org_id,
        user_id=current_user.id, target_id=clone.id, detail={"source_template_id": str(source.id)},
    )
    await db.commit()
    await db.refresh(clone)
    return clone


@router.get(
    "/api/organizations/{org_id}/templates/{template_id}/export",
    response_model=DeploymentTemplateExport,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def export_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeploymentTemplateExport:
    template = await _get_visible_template(db, org_id, template_id)
    disk_layout = await db.get(DiskLayout, template.disk_layout_id)
    if disk_layout is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template's disk layout not found")
    iso_asset = await db.get(IsoAsset, template.iso_asset_id) if template.iso_asset_id else None

    audit.record(
        db, action="template.export", target_type="template", org_id=org_id,
        user_id=current_user.id, target_id=template.id,
    )
    await db.commit()
    return DeploymentTemplateExport(
        name=template.name,
        disk_layout=TemplateExportDiskLayout(name=disk_layout.name, layout=disk_layout.layout_json),
        iso_hint=TemplateExportIsoHint(filename=iso_asset.filename, kind=iso_asset.kind.value) if iso_asset else None,
        cpu_count=template.cpu_count,
        ram_mb=template.ram_mb,
        disk_size_gb=template.disk_size_gb,
        network_name=template.network_name,
        vlan_id=template.vlan_id,
        locale=template.locale,
        timezone=template.timezone,
        keyboard_layout=template.keyboard_layout,
        domain_join_enabled=template.domain_join_enabled,
        domain_fqdn=template.domain_fqdn,
        domain_join_account=template.domain_join_account,
        domain_target_ou=template.domain_target_ou,
        domain_join_timing=template.domain_join_timing,
        windows_features=template.windows_features,
        post_install_scripts=template.post_install_scripts,
    )


@router.post(
    "/api/organizations/{org_id}/templates/import",
    response_model=DeploymentTemplateRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def import_template(
    org_id: uuid.UUID,
    body: DeploymentTemplateImport,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeploymentTemplate:
    """Recreates the disk layout inline (always a new org-scoped row, not
    matched against existing ones) and leaves iso_asset_id unset, an ISO
    isn't portable across environments so it must be reattached manually.
    The local admin password is a random placeholder: this template cannot
    deploy until an operator sets a real one."""
    disk_layout = DiskLayout(org_id=org_id, name=body.disk_layout.name, layout_json=body.disk_layout.layout.model_dump())
    db.add(disk_layout)
    await db.flush()

    template = DeploymentTemplate(
        org_id=org_id,
        name=body.name,
        iso_asset_id=None,
        disk_layout_id=disk_layout.id,
        cpu_count=body.cpu_count,
        ram_mb=body.ram_mb,
        disk_size_gb=body.disk_size_gb,
        network_name=body.network_name,
        vlan_id=body.vlan_id,
        locale=body.locale,
        timezone=body.timezone,
        keyboard_layout=body.keyboard_layout,
        domain_join_enabled=body.domain_join_enabled,
        domain_fqdn=body.domain_fqdn,
        domain_join_account=body.domain_join_account,
        domain_target_ou=body.domain_target_ou,
        domain_join_timing=body.domain_join_timing,
        windows_features=body.windows_features,
        post_install_scripts=[s.model_dump() for s in body.post_install_scripts],
    )
    template.local_admin_password = secrets.token_urlsafe(24)
    db.add(template)
    await db.flush()
    audit.record(
        db, action="template.import", target_type="template", org_id=org_id,
        user_id=current_user.id, target_id=template.id, detail={"name": template.name},
    )
    await db.commit()
    await db.refresh(template)
    return template


@router.post(
    "/api/organizations/{org_id}/templates/{template_id}/preview",
    response_model=AutounattendPreview,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def preview_template(
    org_id: uuid.UUID,
    template_id: uuid.UUID,
    body: DeploymentPreviewRequest,
    db: AsyncSession = Depends(get_db),
) -> AutounattendPreview:
    template = await _get_visible_template(db, org_id, template_id)
    disk_layout = await db.get(DiskLayout, template.disk_layout_id)
    if disk_layout is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template's disk layout not found")

    # Unsaved, in-memory only, this is exactly what the deployment wizard's
    # preview step renders, and exactly what the real ISO build renders from
    # once a Deployment row actually exists, via the same render_autounattend.
    draft = Deployment(
        org_id=org_id,
        template_id=template_id,
        hypervisor_host_id=uuid.uuid4(),
        hostname=body.hostname,
        ip_mode=body.ip_mode,
        static_ip=body.static_ip,
        static_netmask=body.static_netmask,
        static_gateway=body.static_gateway,
        static_dns=body.static_dns,
        callback_token=uuid.uuid4().hex,
        created_by_user_id=uuid.uuid4(),
    )
    xml = render_autounattend(draft, template, disk_layout)
    return AutounattendPreview(xml=xml)
