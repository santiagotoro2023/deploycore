from pathlib import Path

import jinja2

from app.config import get_settings
from app.models.deployment import Deployment
from app.models.disk_layout import DiskLayout
from app.models.template import DeploymentTemplate

_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent.parent / "templates" / "xml"),
    autoescape=jinja2.select_autoescape(["xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_autounattend(
    deployment: Deployment, template: DeploymentTemplate, disk_layout: DiskLayout
) -> str:
    """The single rendering entry point — both the wizard's preview step and
    the actual ISO build call this, so what an operator reviews is
    byte-identical to what ships."""
    tmpl = _ENV.get_template("autounattend_base.xml.j2")
    return tmpl.render(
        deployment=deployment,
        template=template,
        disk_layout=disk_layout,
        callback_base_url=get_settings().app_public_url,
    )
