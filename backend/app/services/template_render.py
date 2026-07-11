import subprocess
from pathlib import Path

import jinja2

from app.config import get_settings
from app.models.deployment import Deployment, IpMode
from app.models.disk_layout import DiskLayout
from app.models.template import DeploymentTemplate
from app.winrm.client import netmask_to_prefix

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

_WINLOGON_KEY = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"


def _autologon_reg_commands(username: str, password: str) -> list[str]:
    """reg.exe, not PowerShell: RunSynchronousCommand runs this during
    Setup's specialize pass, before WMI/CIM and other subsystems
    PowerShell itself can depend on are guaranteed to be initialized - a
    real, documented failure mode (a PowerShell cmdlet there crashing with
    WBEM_E_CRITICAL_ERROR because a WMI class hadn't been populated yet,
    per a Microsoft Q&A thread on the exact same RunSynchronousCommand/
    specialize combination), and the actual, confirmed cause the one time
    this used powershell.exe here: Setup crashed outright with "the
    computer was unexpectedly restarted" immediately after landing.
    reg.exe has none of PowerShell's startup dependencies, it's one of
    the small set of tools MDT/Packer-style specialize-pass automation
    reaches for specifically because of this fragility. Four separate
    commands, not one semicolon-chained line, because RunSynchronousCommand
    takes exactly one process invocation per Path. list2cmdline applies
    the same argv-quoting CreateProcess itself expects (Path launches the
    exe directly, no cmd.exe involved, so none of cmd's %/^/&
    metacharacter handling applies, just standard C-runtime quote/
    backslash escaping) for whatever ends up in the account name/
    password."""
    return [
        subprocess.list2cmdline(
            ["reg.exe", "add", _WINLOGON_KEY, "/v", "AutoAdminLogon", "/t", "REG_SZ", "/d", "1", "/f"]
        ),
        subprocess.list2cmdline(
            ["reg.exe", "add", _WINLOGON_KEY, "/v", "DefaultUserName", "/t", "REG_SZ", "/d", username, "/f"]
        ),
        subprocess.list2cmdline(
            ["reg.exe", "add", _WINLOGON_KEY, "/v", "DefaultPassword", "/t", "REG_SZ", "/d", password, "/f"]
        ),
        subprocess.list2cmdline(
            ["reg.exe", "add", _WINLOGON_KEY, "/v", "AutoLogonCount", "/t", "REG_DWORD", "/d", "1", "/f"]
        ),
    ]


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
        autologon_commands=_autologon_reg_commands(
            template.local_admin_username, template.local_admin_password
        ),
        # Only meaningful (and only computed) for a static deployment: CIDR
        # prefix length Windows' own TCP/IP unattend component wants
        # (UnicastIpAddresses takes "<ip>/<prefix>", not a dotted netmask).
        static_prefix=netmask_to_prefix(deployment.static_netmask)
        if deployment.ip_mode == IpMode.STATIC and deployment.static_netmask
        else None,
    )
