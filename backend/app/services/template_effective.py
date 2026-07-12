import uuid as _uuid

from app.models.template import DiskProvisioning, DomainJoinTiming, NetworkAdapterType

# Raw override values come back from JSON storage as plain
# str/int/bool/list - restores the handful of fields that need a real
# enum/UUID instance because a caller does more than just read them back
# out (e.g. template.disk_provisioning.value, or
# db.get(DiskLayout, template.disk_layout_id)).
_FIELD_COERCION = {
    "disk_provisioning": DiskProvisioning,
    "network_adapter_type": NetworkAdapterType,
    "domain_join_timing": DomainJoinTiming,
    "disk_layout_id": _uuid.UUID,
    "iso_asset_id": _uuid.UUID,
}


class EffectiveTemplate:
    """Read-only view of a DeploymentTemplate with per-deployment
    overrides (Deployment.overrides) layered on top, never persisted
    back to the template itself - the "Customize installation" wizard
    step. __getattr__ falls through to the real template for anything
    not overridden, including its computed properties
    (local_admin_password etc.), so every existing template.* call site
    (template_render.py, provision.py) works unchanged whether or not a
    given deployment has any overrides at all - this is a wrapper, not a
    copy, deliberately: it never needs updating when DeploymentTemplate
    itself grows a new field, only fields someone actually chose to
    override are ever intercepted."""

    def __init__(self, template, overrides: dict | None):
        self._template = template
        self._overrides = overrides or {}

    def __getattr__(self, name):
        if name in self._overrides:
            value = self._overrides[name]
            coerce = _FIELD_COERCION.get(name)
            return coerce(value) if coerce is not None and value is not None else value
        return getattr(self._template, name)


def resolve_template(template, overrides: dict | None):
    """No-op passthrough when there's nothing to override - avoids
    wrapping every deployment's template for the common case where
    "Customize installation" was never used."""
    return EffectiveTemplate(template, overrides) if overrides else template
