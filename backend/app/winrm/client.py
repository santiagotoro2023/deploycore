import ipaddress
import json

import winrm

PS_HEADER = "$ProgressPreference = 'SilentlyContinue'; $ErrorActionPreference = 'Stop'; "

# Delimits the machine-readable summary line install_features appends
# after Install-WindowsFeature's own human-readable table output (which
# still gets shown to the operator as-is): distinct enough that nothing
# Windows itself would ever write to stdout could collide with it.
_FEATURE_RESULT_MARKER = "###DEPLOYCORE_FEATURE_RESULT###"


class WinRMResult:
    def __init__(self, status_code: int, stdout: str, stderr: str) -> None:
        self.status_code = status_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.status_code == 0


class FeatureInstallResult(WinRMResult):
    def __init__(self, status_code: int, stdout: str, stderr: str, restart_needed: bool) -> None:
        super().__init__(status_code, stdout, stderr)
        self.restart_needed = restart_needed


class VmwareToolsInstallResult(WinRMResult):
    def __init__(self, status_code: int, stdout: str, stderr: str, installed: bool) -> None:
        super().__init__(status_code, stdout, stderr)
        self.installed = installed


def netmask_to_prefix(netmask: str) -> int:
    return ipaddress.ip_network(f"0.0.0.0/{netmask}").prefixlen


def _ps_single_quote(value: str) -> str:
    """PowerShell's own escape convention for a single-quoted string
    literal is doubling the quote, not backslash-escaping it."""
    return "'" + value.replace("'", "''") + "'"


class WinRMClient:
    """Thin sync wrapper over pywinrm. Every method is blocking, worker
    tasks call these via asyncio.to_thread, same pattern as the ESXi
    driver's pyvmomi calls."""

    def __init__(self, host: str, username: str, password: str) -> None:
        self._session = winrm.Session(host, auth=(username, password), transport="ntlm")

    def run_ps(self, script: str) -> WinRMResult:
        result = self._session.run_ps(PS_HEADER + script)
        return WinRMResult(
            result.status_code,
            result.std_out.decode(errors="replace"),
            result.std_err.decode(errors="replace"),
        )

    def install_features(self, feature_names: list[str]) -> FeatureInstallResult:
        """One Install-WindowsFeature call for every requested feature
        together, not one call per feature: this is also what Server
        Manager's own "Add Roles and Features" wizard does (select
        several, click Install once), a single DISM/CBS transaction
        rather than N separate ones with their own per-call overhead,
        and it's what makes -IncludeManagementTools below actually mean
        something across the whole set. -IncludeManagementTools is
        always passed, not conditional on the edition having a GUI: on
        Server Core it installs whatever's applicable (PowerShell
        modules/CLI tools) and silently skips the GUI-only pieces that
        can't run there rather than failing, confirmed - it's what
        Server Manager's own wizard has checked by default either way.

        Install-WindowsFeature's own table output is still shown to the
        operator as-is (via the log line built from .stdout), but
        whether it actually succeeded and whether it needs a restart to
        finish weren't previously checked at all - $r.Success not being
        true doesn't necessarily raise a terminating error on its own,
        so a failed-but-non-throwing install could previously report
        .ok=True. Appends a machine-readable marker line after the human
        table (ConvertTo-Json -Compress, parsed back out below) to get
        both Success and RestartNeeded reliably, and explicitly fails
        the command (non-zero exit) when Success is false rather than
        relying on Install-WindowsFeature to raise on its own.

        Waits for TrustedInstaller (Windows Modules Installer) to go
        idle first: this is the actual root cause of the 0x80070020
        (ERROR_SHARING_VIOLATION) failures seen right after first boot,
        not something a bare retry could reliably outrun - TrustedInstaller
        is demand-start and still runs for a while right after a fresh
        image finishes applying, and Install-WindowsFeature can't touch
        the servicing store while it holds the lock. Bounded so a stuck
        service can't hang the deployment forever; if it's still Running
        past the deadline, Install-WindowsFeature is attempted anyway and
        surfaces its own error as before."""
        names_literal = ",".join(_ps_single_quote(name) for name in feature_names)
        script = f"""
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Service TrustedInstaller -ErrorAction SilentlyContinue).Status -eq 'Running' -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Seconds 5
}}
$r = Install-WindowsFeature -Name @({names_literal}) -IncludeManagementTools
$r
$summary = [PSCustomObject]@{{ Success = $r.Success; RestartNeeded = [string]$r.RestartNeeded }} | ConvertTo-Json -Compress
Write-Output "{_FEATURE_RESULT_MARKER}$summary"
if (-not $r.Success) {{ exit 1 }}
""".strip()
        result = self.run_ps(script)
        display_stdout = result.stdout
        restart_needed = False
        if _FEATURE_RESULT_MARKER in result.stdout:
            display_stdout, _, marker_line = result.stdout.partition(_FEATURE_RESULT_MARKER)
            try:
                payload = json.loads(marker_line.strip())
                restart_needed = str(payload.get("RestartNeeded", "No")).strip().lower() in ("yes", "maybe")
            except (ValueError, AttributeError):
                pass  # Same effect as RestartNeeded genuinely being "No": nothing to act on either way
        return FeatureInstallResult(result.status_code, display_stdout.strip(), result.stderr, restart_needed)

    def get_feature_install_status(self, feature_names: list[str]) -> dict[str, bool]:
        """Cheap Get-WindowsFeature poll, {name: installed}. Used to report
        per-role progress (see provision.py's install_features heartbeat)
        while the single batched Install-WindowsFeature call above is still
        running in the background - a real one-by-one rundown isn't
        possible (it's one DISM/CBS transaction, not N sequential ones),
        but polling which of the requested features have already flipped
        to Installed gets the same visibility without giving up the speed
        of the batched call. Returns {} on any failure (transient WinRM
        hiccup mid-install is expected, not worth surfacing - the caller
        just skips that tick's progress line)."""
        names_literal = ",".join(_ps_single_quote(name) for name in feature_names)
        script = f"""
$names = @({names_literal})
$names | ForEach-Object {{
    $f = Get-WindowsFeature -Name $_ -ErrorAction SilentlyContinue
    [PSCustomObject]@{{ Name = $_; Installed = [bool]($f -and $f.Installed) }}
}} | ConvertTo-Json -Compress
""".strip()
        result = self.run_ps(script)
        if not result.ok:
            return {}
        try:
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            return {item["Name"]: bool(item["Installed"]) for item in data}
        except (ValueError, KeyError, TypeError):
            return {}

    def verify_windows_features_installed(self, feature_names: list[str]) -> WinRMResult:
        """Explicit confirmation pass, run once after every requested
        feature's own Install-WindowsFeature call already reported
        success: catches the case (rare, but exactly why this exists)
        where a feature reports success but a later change - another
        feature's install, a restart pending from one of them - leaves
        it not actually in an Installed state by the time everything
        else in post_install is about to start depending on it."""
        names_literal = ",".join(_ps_single_quote(name) for name in feature_names)
        missing_check = (
            "-not (Get-WindowsFeature -Name $_ -ErrorAction SilentlyContinue).Installed"
        )
        script = f"""
$names = @({names_literal})
$missing = $names | Where-Object {{ {missing_check} }}
if ($missing) {{
    throw "not installed: $($missing -join ', ')"
}} else {{
    Write-Output 'all requested features verified installed'
}}
""".strip()
        return self.run_ps(script)

    def join_domain(
        self, domain_fqdn: str, username: str, password: str, ou: str | None = None
    ) -> WinRMResult:
        ou_clause = f" -OUPath '{ou}'" if ou else ""
        script = (
            f"$cred = New-Object System.Management.Automation.PSCredential("
            f"'{username}', (ConvertTo-SecureString '{password}' -AsPlainText -Force)); "
            f"Add-Computer -DomainName '{domain_fqdn}' -Credential $cred{ou_clause} -Force"
        )
        return self.run_ps(script)

    def install_app(self, download_url: str, remote_path: str, kind: str, install_args: str) -> WinRMResult:
        """Downloads an installer from DeployCore over the guest's own
        Invoke-WebRequest (not pushed by the worker, same reasoning as the
        Setup-complete callback: the guest already reaches DeployCore's
        API, so there's no need to chunk the file through WinRM itself)
        and runs it silently. kind "msi" goes through msiexec, anything
        else runs the downloaded file directly with install_args passed
        straight through, whatever that installer's own silent-install
        convention is. Exit code 3010 (success, reboot required) counts as
        success alongside 0, the same convention MSI and many EXE
        installers both use for "done, but you should reboot"."""
        url = _ps_single_quote(download_url)
        path = _ps_single_quote(remote_path)
        if kind == "msi":
            # A single raw argument-list string, not an array: msiexec (like
            # most Windows installers) does its own command-line parsing,
            # splitting install_args ("/qn /norestart") into an array would
            # pass it as one single quoted argument instead of two flags.
            arg_list = _ps_single_quote(f'/i "{remote_path}" {install_args}')
            run_line = f"$p = Start-Process msiexec.exe -ArgumentList {arg_list} -Wait -PassThru"
        else:
            arg_list = _ps_single_quote(install_args)
            run_line = f"$p = Start-Process -FilePath {path} -ArgumentList {arg_list} -Wait -PassThru"
        script = (
            f"Invoke-WebRequest -Uri {url} -OutFile {path} -UseBasicParsing; "
            f"{run_line}; "
            f"Remove-Item {path} -Force -ErrorAction SilentlyContinue; "
            "if ($p.ExitCode -eq 0 -or $p.ExitCode -eq 3010) { exit 0 } else { exit $p.ExitCode }"
        )
        return self.run_ps(script)

    def rename_computer(self, new_name: str) -> WinRMResult:
        return self.run_ps(f"Rename-Computer -NewName '{new_name}' -Force")

    def enable_rdp(self) -> WinRMResult:
        """fDenyTSConnections=0 alone doesn't open the firewall, and the
        built-in "Remote Desktop" rule group is disabled by default
        alongside it; both need doing, and neither needs a restart to
        take effect - Terminal Services picks up the registry value on
        the next connection attempt, not at boot.

        -Group '@FirewallAPI.dll,-28752', not -DisplayGroup 'Remote
        Desktop': DisplayGroup is the GUI-facing text, localized per system
        locale (e.g. "Remotedesktop" on a German image) - matching on it
        broke every non-English deployment with an ObjectNotFound error.
        Group is the underlying resource-string identifier the rule store
        actually keys on, same on every locale."""
        return self.run_ps(
            "Set-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' "
            "-Name fDenyTSConnections -Value 0; "
            "Enable-NetFirewallRule -Group '@FirewallAPI.dll,-28752'"
        )

    def install_vmware_tools(self) -> VmwareToolsInstallResult:
        """Best-effort install from whichever CD-ROM ESXi mounted the Tools
        installer ISO on at VM creation (see esxi.py's create_vm ->
        MountToolsInstaller() - no fixed drive letter to rely on, Windows
        Setup's own install media usually claims D:, so Tools typically
        lands on E: or F:).

        Runs via WinRM post-install, not during Windows Setup's specialize
        pass: an earlier specialize-pass version of this (a
        RunSynchronousCommand pair, since removed) crashed Setup outright
        on a real deployment ("the computer was unexpectedly restarted") -
        a WinRM call after Setup has already finished and the OS is fully
        up carries none of that risk, and reuses the same channel already
        proven for Install-WindowsFeature etc.

        REBOOT=ReallySuppress: a documented VMware Tools/VMXNET3
        interaction disconnects the network immediately if its driver
        update takes effect without a full restart. The caller (see
        provision.py's run_post_install) does a single controlled reboot
        right after, but only when .installed is true - nothing to settle
        if the ISO was never mounted (e.g. a non-VMware host)."""
        script = """
$installer = @('D:\\setup64.exe','E:\\setup64.exe','F:\\setup64.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($installer) {
    Start-Process -FilePath $installer -ArgumentList '/S','/v"/qn REBOOT=ReallySuppress"' -Wait
    Write-Output 'DEPLOYCORE_VMWARE_TOOLS_INSTALLED'
} else {
    Write-Output 'DEPLOYCORE_VMWARE_TOOLS_NOT_FOUND'
}
""".strip()
        result = self.run_ps(script)
        installed = "DEPLOYCORE_VMWARE_TOOLS_INSTALLED" in result.stdout
        return VmwareToolsInstallResult(result.status_code, result.stdout.strip(), result.stderr, installed)

    def reboot(self) -> WinRMResult:
        return self.run_ps("Restart-Computer -Force")

    def is_reachable(self) -> bool:
        try:
            return self.run_ps("Write-Output ok").ok
        except Exception:  # noqa: BLE001 - reachability probe, any failure means "not yet"
            return False
