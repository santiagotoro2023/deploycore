from pathlib import Path

import jinja2

from app.config import get_settings
from app.models.deployment import Deployment
from app.models.disk_layout import DiskLayout
from app.models.template import DeploymentTemplate

# Windows' Setup UI only accepts InputLocale as either a bare locale tag
# (which picks *a* default keyboard for that locale, not necessarily the
# one implied by the tag - "de-CH" alone lands on the plain German
# keyboard, not Swiss German) or an explicit "LCID:KLID" pair. Every
# locale below maps to the LCID:KLID pair Microsoft's own sample answer
# files use, which for a locale's *named* keyboard is always its LCID hex
# with "0000" prefixed as the KLID (confirmed against Microsoft's
# [MS-LCID] reference and real-world unattend.xml examples, e.g. de-CH ->
# "0807:00000807" is the documented way to force the Swiss German layout).
_LOCALE_LCID_HEX = {
    "da-DK": "0406",
    "de-AT": "0c07",
    "de-CH": "0807",
    "de-DE": "0407",
    "en-GB": "0809",
    "en-US": "0409",
    "es-ES": "0c0a",
    "fi-FI": "040b",
    "fr-BE": "080c",
    "fr-CH": "100c",
    "fr-FR": "040c",
    "it-CH": "0810",
    "it-IT": "0410",
    "nb-NO": "0414",
    "nl-BE": "0813",
    "nl-NL": "0413",
    "pl-PL": "0415",
    "pt-PT": "0816",
    "sv-SE": "041d",
}


def _resolve_input_locale(keyboard_layout: str) -> str:
    """Someone may already supply a raw "LCID:KLID" pair (needed for any
    locale not in the table above, or for a non-default keyboard on a
    supported one) - pass those through untouched rather than guessing."""
    if ":" in keyboard_layout:
        return keyboard_layout
    lcid = _LOCALE_LCID_HEX.get(keyboard_layout)
    if lcid is None:
        return keyboard_layout
    return f"{lcid}:0000{lcid}"


_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent.parent / "templates" / "xml"),
    # select_autoescape(["xml"]) looks for a ".xml" filename suffix, but
    # every template here is named "*.xml.j2" (so editors still recognize
    # it as XML), which ends in ".j2" instead, so that selector never
    # actually matched and autoescaping was silently off: any field with
    # &, <, or > (a password, an OU path, ...) would corrupt the XML into
    # something Setup can't parse and silently falls back to interactive
    # install for, no visible error. Every template in this directory is
    # XML, so there's no need for a conditional selector at all.
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_autounattend(
    deployment: Deployment, template: DeploymentTemplate, disk_layout: DiskLayout
) -> str:
    """The single rendering entry point, both the wizard's preview step and
    the actual ISO build call this, so what an operator reviews is
    byte-identical to what ships."""
    tmpl = _ENV.get_template("autounattend_base.xml.j2")
    return tmpl.render(
        deployment=deployment,
        template=template,
        disk_layout=disk_layout,
        callback_base_url=get_settings().app_public_url,
        input_locale=_resolve_input_locale(template.keyboard_layout),
    )
