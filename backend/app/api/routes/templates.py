import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.hypervisors.defaults import generate_mac_address
from app.models.app_asset import AppAsset
from app.models.deployment import Deployment
from app.models.disk_layout import DiskLayout
from app.models.iso_asset import IsoAsset
from app.models.template import DeploymentTemplate
from app.models.user import Role, User
from app.schemas.deployment import AutounattendPreview, DeploymentPreviewRequest
from app.schemas.template import (
    RESERVED_LOCAL_ACCOUNT_NAMES,
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
from app.services.template_effective import resolve_template
from app.services.template_render import render_autounattend

router = APIRouter(tags=["templates"])


def _resolve_local_admin_username(custom_admin_enabled: bool, username: str | None) -> str:
    """The built-in Administrator account is used unless the custom-admin
    toggle is on, in which case a fresh local account is created and the
    built-in one disabled during Setup (see autounattend_base.xml.j2 and
    _first_logon_commands.xml.j2). Forcing that here keeps
    DeploymentTemplate.local_admin_username the single source of truth for
    "which account should WinRM authenticate as" everywhere else in the
    codebase, downstream code never has to branch on custom_admin_enabled
    itself."""
    if not custom_admin_enabled:
        return "Administrator"
    username = (username or "").strip()
    if not username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "local admin username cannot be blank when custom admin is enabled")
    if username.lower() in RESERVED_LOCAL_ACCOUNT_NAMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'"{username}" is a reserved Windows account name, choose a different local admin username',
        )
    return username


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
    data["app_installs"] = [a.model_dump(mode="json") for a in body.app_installs]
    data["local_admin_username"] = _resolve_local_admin_username(data["custom_admin_enabled"], data["local_admin_username"])
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
    if "app_installs" in updates and updates["app_installs"] is not None:
        # model_dump() already flattens AppInstallEntry to plain dicts, but
        # leaves app_asset_id as a uuid.UUID object (JSON-unsafe); JSONB
        # storage needs it stringified, same reason create_template uses
        # model_dump(mode="json") for this field instead.
        updates["app_installs"] = [{**entry, "app_asset_id": str(entry["app_asset_id"])} for entry in updates["app_installs"]]
    local_admin_password = updates.pop("local_admin_password", None)
    domain_join_credential = updates.pop("domain_join_credential", None)
    for field, value in updates.items():
        setattr(template, field, value)
    if local_admin_password:
        template.local_admin_password = local_admin_password
    if domain_join_credential:
        template.domain_join_credential = domain_join_credential
    # Evaluated against the FINAL merged state (this patch's values where
    # provided, the existing row's otherwise), not just what was submitted:
    # e.g. toggling custom_admin_enabled on in a patch that doesn't also
    # touch local_admin_username must still validate/normalize against
    # whatever username the row already has.
    template.local_admin_username = _resolve_local_admin_username(
        template.custom_admin_enabled, template.local_admin_username
    )
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
        image_index=source.image_index,
        disk_layout_id=source.disk_layout_id,
        cpu_count=source.cpu_count,
        cores_per_socket=source.cores_per_socket,
        ram_mb=source.ram_mb,
        disk_size_gb=source.disk_size_gb,
        disk_provisioning=source.disk_provisioning,
        network_name=source.network_name,
        network_adapter_type=source.network_adapter_type,
        preferred_datastore=source.preferred_datastore,
        locale=source.locale,
        timezone=source.timezone,
        keyboard_layout=source.keyboard_layout,
        custom_admin_enabled=source.custom_admin_enabled,
        local_admin_username=source.local_admin_username,
        local_admin_password_encrypted=source.local_admin_password_encrypted,
        domain_join_enabled=source.domain_join_enabled,
        domain_fqdn=source.domain_fqdn,
        domain_join_account=source.domain_join_account,
        domain_join_credential_encrypted=source.domain_join_credential_encrypted,
        domain_target_ou=source.domain_target_ou,
        domain_join_timing=source.domain_join_timing,
        enable_rdp=source.enable_rdp,
        install_windows_updates=source.install_windows_updates,
        windows_features=list(source.windows_features),
        post_install_scripts=list(source.post_install_scripts),
        app_installs=list(source.app_installs),
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
    app_install_hints = []
    for entry in template.app_installs:
        app_asset = await db.get(AppAsset, uuid.UUID(entry["app_asset_id"]))
        app_install_hints.append(app_asset.name if app_asset else "(deleted app asset)")

    audit.record(
        db, action="template.export", target_type="template", org_id=org_id,
        user_id=current_user.id, target_id=template.id,
    )
    await db.commit()
    return DeploymentTemplateExport(
        name=template.name,
        disk_layout=TemplateExportDiskLayout(name=disk_layout.name, layout=disk_layout.layout_json),
        iso_hint=TemplateExportIsoHint(filename=iso_asset.filename, kind=iso_asset.kind.value) if iso_asset else None,
        app_install_hints=app_install_hints,
        cpu_count=template.cpu_count,
        cores_per_socket=template.cores_per_socket,
        ram_mb=template.ram_mb,
        disk_size_gb=template.disk_size_gb,
        disk_provisioning=template.disk_provisioning,
        network_name=template.network_name,
        network_adapter_type=template.network_adapter_type,
        locale=template.locale,
        timezone=template.timezone,
        keyboard_layout=template.keyboard_layout,
        custom_admin_enabled=template.custom_admin_enabled,
        local_admin_username=template.local_admin_username,
        domain_join_enabled=template.domain_join_enabled,
        domain_fqdn=template.domain_fqdn,
        domain_join_account=template.domain_join_account,
        domain_target_ou=template.domain_target_ou,
        domain_join_timing=template.domain_join_timing,
        enable_rdp=template.enable_rdp,
        install_windows_updates=template.install_windows_updates,
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
        cores_per_socket=body.cores_per_socket,
        ram_mb=body.ram_mb,
        disk_size_gb=body.disk_size_gb,
        disk_provisioning=body.disk_provisioning,
        network_name=body.network_name,
        network_adapter_type=body.network_adapter_type,
        locale=body.locale,
        timezone=body.timezone,
        keyboard_layout=body.keyboard_layout,
        custom_admin_enabled=body.custom_admin_enabled,
        local_admin_username=_resolve_local_admin_username(body.custom_admin_enabled, body.local_admin_username),
        domain_join_enabled=body.domain_join_enabled,
        domain_fqdn=body.domain_fqdn,
        domain_join_account=body.domain_join_account,
        domain_target_ou=body.domain_target_ou,
        domain_join_timing=body.domain_join_timing,
        enable_rdp=body.enable_rdp,
        install_windows_updates=body.install_windows_updates,
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
    template = resolve_template(template, body.overrides)
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
        # Same shape as the real one (deployments.py's secrets.token_hex(8)),
        # so a preview looks like what actually ships.
        callback_token=secrets.token_hex(8),
        created_by_user_id=uuid.uuid4(),
    )
    # No real VM/NIC exists for a preview (hypervisor_host_id above is a
    # throwaway placeholder), so this is illustrative only, same as
    # callback_token above - the actual deployment generates and assigns
    # its own value at creation time (see provision.py's run_deployment).
    xml = render_autounattend(draft, template, disk_layout, generate_mac_address("esxi"))
    return AutounattendPreview(xml=xml)
