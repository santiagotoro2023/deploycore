import base64
import ipaddress
import json
import time
import uuid

import winrm

PS_HEADER = "$ProgressPreference = 'SilentlyContinue'; $ErrorActionPreference = 'Stop'; "

# pywinrm's run_ps base64-encodes the whole script for a single
# `powershell -EncodedCommand <blob>` invocation, which travels through
# WinRS as one command-line argument - real limit is roughly 8191
# characters there, and UTF-16LE + base64 expands the source by ~2.67x
# before it even hits that ceiling. A ~5000-character post-install
# script (this project's own recovery-relocation script) hit it for
# real: "Die Befehlszeile ist zu lang" / "the command line is too
# long", which _run_post_install_scripts then reports as a script
# failure with no other explanation. Kept well under the actual math
# (~3071 raw chars) for margin against overhead this isn't modeling
# exactly (pywinrm/WinRS flags, protocol framing).
_LONG_SCRIPT_THRESHOLD = 2000
# Same margin logic applied to each chunk-append command sent while
# writing a long script to a remote temp file (see _run_long_ps): fixed
# wrapper text around the chunk adds runs a similar 2.67x expansion.
_SCRIPT_CHUNK_SIZE = 1800

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
        # pywinrm's defaults (operation_timeout_sec=20, read_timeout_sec=30)
        # leave only a 10s margin between them - WinRM's Receive operation
        # can legitimately block server-side for the full operation timeout
        # before replying, and under real load (e.g. a guest mid-Windows-
        # Update) that round trip occasionally exceeds a 30s hard client
        # read-timeout, surfacing as a spurious requests.ReadTimeout that
        # has nothing to do with the guest actually being unreachable.
        # Widened with a large, safe margin between the two.
        self._session = winrm.Session(
            host, auth=(username, password), transport="ntlm",
            operation_timeout_sec=60, read_timeout_sec=90,
        )

    def run_ps(self, script: str) -> WinRMResult:
        full_script = PS_HEADER + script
        if len(full_script) <= _LONG_SCRIPT_THRESHOLD:
            return self._run_ps_direct(full_script)
        return self._run_long_ps(full_script)

    def _run_ps_direct(self, full_script: str) -> WinRMResult:
        result = self._session.run_ps(full_script)
        return WinRMResult(
            result.status_code,
            result.std_out.decode(errors="replace"),
            result.std_err.decode(errors="replace"),
        )

    def _run_long_ps(self, full_script: str) -> WinRMResult:
        """Writes the script to a remote temp file across several
        safely-sized append commands, then runs it from there in one
        short final command - see _LONG_SCRIPT_THRESHOLD for why this is
        needed at all."""
        remote_path = f"C:\\Windows\\Temp\\deploycore_script_{uuid.uuid4().hex}.ps1"
        write_result = self._write_remote_script(full_script, remote_path, wrap_header=False)
        if not write_result.ok:
            return write_result
        final_result = self._run_ps_direct(f"& '{remote_path}'")
        self._run_ps_direct(f"Remove-Item -Path '{remote_path}' -Force -ErrorAction SilentlyContinue")
        return final_result

    def _write_remote_script(self, content: str, remote_path: str, wrap_header: bool = True) -> WinRMResult:
        """Writes arbitrary script content to a remote file across
        several safely-sized base64 chunks, same technique as
        _run_long_ps, but without immediately executing it - used for
        scripts meant to run some other way (e.g. install_windows_updates'
        SYSTEM-context scheduled task, not directly over this WinRM
        session). wrap_header=False when `content` is itself already a
        full, self-contained script that shouldn't get PS_HEADER prepended
        (it'll run in its own separate process, not this session)."""
        full_content = (PS_HEADER + content) if wrap_header else content
        setup_result = self._run_ps_direct(f"Set-Content -Path '{remote_path}' -Value '' -Encoding UTF8 -Force")
        if not setup_result.ok:
            return setup_result

        encoded = base64.b64encode(full_content.encode("utf-8")).decode("ascii")
        for i in range(0, len(encoded), _SCRIPT_CHUNK_SIZE):
            chunk = encoded[i : i + _SCRIPT_CHUNK_SIZE]
            append_cmd = (
                "$ErrorActionPreference = 'Stop'; "
                f"[System.IO.File]::AppendAllText('{remote_path}', "
                f"[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{chunk}')))"
            )
            result = self._run_ps_direct(append_cmd)
            if not result.ok:
                return result
        return WinRMResult(0, "", "")

    def _run_as_system_task(self, script_body: str, task_name: str, max_attempts: int = 240, poll_seconds: int = 10) -> WinRMResult:
        """Writes script_body to a remote temp file and runs it via a
        one-shot SYSTEM-context Scheduled Task, polling for completion
        and returning the parsed result - shared by install_app and
        install_windows_updates, both of which need to escape this WinRM
        session's own restrictions (a network-logon token WUA's COM API
        refuses for real downloads/installs; NSIS/installer
        per-machine-vs-per-user ambiguity that isn't a concern once
        nothing needs to negotiate UAC elevation, because it's already
        running as the most privileged account there is; see each
        method's own docstring for the specifics and research behind
        each). script_body must write its own outcome to the literal
        placeholder path {RESULT_PATH} (substituted here with the actual
        temp path used) as 'OK:<message>' or 'FAIL:<message>' - there's
        no way for a Scheduled Task to hand its stdout back to this
        session directly."""
        script_path = f"C:\\Windows\\Temp\\deploycore_{task_name}.ps1"
        result_path = f"C:\\Windows\\Temp\\deploycore_{task_name}_result.txt"
        full_script = script_body.replace("{RESULT_PATH}", result_path)

        write_result = self._write_remote_script(full_script, script_path, wrap_header=False)
        if not write_result.ok:
            return write_result

        register_result = self._run_ps_direct(
            PS_HEADER
            + f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue; "
            + "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "
            + f"'-NoProfile -ExecutionPolicy Bypass -File \"{script_path}\"'; "
            + "$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date); "
            + f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger "
            + "-User 'SYSTEM' -RunLevel Highest -Force | Out-Null; "
            + f"Start-ScheduledTask -TaskName '{task_name}'"
        )
        if not register_result.ok:
            return register_result

        # Everything from here on must leave no trace on the guest
        # regardless of how this ends - timed out, a transient WinRM
        # exception mid-poll (pywinrm's own WinRMOperationTimeoutError has
        # been observed escaping _run_ps_direct for real, elsewhere in
        # this same pipeline), or a clean finish. try/finally, not just a
        # cleanup call at the end of the happy path: that left a
        # registered (and possibly still-running) task, its script, and
        # its result file behind on every non-happy-path exit - a real
        # gap, not hypothetical, given how long these polling loops run
        # and how flaky WinRM has already shown itself to be after a
        # reboot in this same codebase.
        timed_out = False
        try:
            for _ in range(max_attempts):
                try:
                    state_result = self._run_ps_direct(
                        PS_HEADER + f"(Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue).State"
                    )
                except Exception:  # noqa: BLE001 - a transient WinRM hiccup mid-poll (confirmed for
                    # real: requests.ReadTimeout during a long-running task) isn't proof the task or the
                    # guest is dead, just that this one check didn't land - the loop's own max_attempts
                    # ceiling is what still bounds how long a genuinely stuck/unreachable guest gets tolerated.
                    time.sleep(poll_seconds)
                    continue
                if state_result.ok and state_result.stdout.strip() not in ("Running", ""):
                    break
                time.sleep(poll_seconds)
            else:
                timed_out = True

            if timed_out:
                return WinRMResult(1, "", f"scheduled task {task_name} did not finish within the expected time.")

            for attempt in range(3):
                try:
                    result = self._run_ps_direct(
                        PS_HEADER + f"Get-Content -Path '{result_path}' -ErrorAction SilentlyContinue"
                    )
                    break
                except Exception:  # noqa: BLE001 - same transient-timeout reasoning as the poll loop above
                    if attempt == 2:
                        raise
                    time.sleep(poll_seconds)
            output = result.stdout.strip()
            if output.startswith("OK:"):
                return WinRMResult(0, output[3:], "")
            if output.startswith("FAIL:"):
                return WinRMResult(1, "", output[5:])
            return WinRMResult(1, "", output or f"scheduled task {task_name} produced no result.")
        finally:
            try:
                # Stop-ScheduledTask, not just Unregister: unregistering
                # alone deletes the task *definition* but doesn't kill an
                # instance still actually running (relevant on the
                # timeout path specifically) - both together guarantee
                # nothing keeps running and nothing keeps existing.
                self._run_ps_direct(
                    PS_HEADER
                    + f"Stop-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
                    + f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue; "
                    + f"Remove-Item -Path '{script_path}','{result_path}' -Force -ErrorAction SilentlyContinue"
                )
            except Exception:  # noqa: BLE001 - best-effort cleanup; never mask whatever the try block already decided
                pass

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
        and runs it silently, then verifies it actually finished by
        polling the registry Uninstall key set for a new entry - the same
        approach Chocolatey (Get-UninstallRegistryKey) and the PowerShell
        App Deployment Toolkit both use for exactly this, since there's
        no universal "did this arbitrary EXE/MSI actually finish" API.
        Exit code 3010 (success, reboot required) counts as success
        alongside 0, the same convention MSI and many EXE installers use.

        Runs via a one-shot SYSTEM-context Scheduled Task (see
        _run_as_system_task, shared with install_windows_updates), not
        directly over this WinRM session - three concrete, researched
        reasons, not just consistency with the Windows Update fix:

        1) A WinRM/NTLM session is a network logon - already confirmed
        for WUA's CreateUpdateDownloader() (E_ACCESSDENIED), and several
        installer frameworks are known to behave differently or fail
        silently outside a normal interactive/service logon.

        2) NSIS-based installers (Firefox's own installer among them)
        have a documented bug class around per-machine-vs-per-user
        install-mode detection in silent mode specifically: a fresh
        silent (/S) install can pick a mode it doesn't actually have
        clean privileges for, without prompting or erroring - it just
        silently doesn't do what a real elevated interactive run would
        (electron-builder issue #9644, NSIS forums). Running as SYSTEM
        sidesteps this entirely: there's no UAC negotiation to get wrong
        when the process is already at the ceiling privilege level, ever.

        3) Confirmed independently for winget: installs launched under
        the SYSTEM account default to machine-wide (Program Files, the
        global Start Menu) the same way --scope machine does, since
        there's no ambiguous "current interactive user" profile to
        install into instead - this is the closest thing to a universal
        "force machine-wide" lever that generalizes across installer
        frameworks, MSI's own ALLUSERS=1 (still forced below) aside.

        Firefox specifically: contrary to an earlier assumption here,
        Mozilla's own docs don't actually document any /AllUsers-style
        switch on either their stub or full .exe installer - their own
        enterprise/automation guidance is to use the official MSI
        package instead (mozilla.org/firefox/enterprise), precisely
        because the EXE installers carry exactly this kind of ambiguity.
        Worth switching that AppAsset to the MSI if these apps keep
        being a problem; DeployCore's own kind="msi" path already forces
        ALLUSERS=1 and doesn't depend on any installer-specific behavior.

        Checks HKLM + WOW6432Node primarily (SYSTEM-context installs
        should land there); HKCU is still checked too as cheap defense
        in depth, though it's no longer the primary expected signal now
        that nothing installs "as a particular interactive user" here."""
        url = _ps_single_quote(download_url)
        path = _ps_single_quote(remote_path)
        if kind == "msi":
            if "allusers" not in install_args.lower():
                install_args = f"{install_args} ALLUSERS=1".strip()
            # A single raw argument-list string, not an array: msiexec (like
            # most Windows installers) does its own command-line parsing,
            # splitting install_args ("/qn /norestart") into an array would
            # pass it as one single quoted argument instead of two flags.
            arg_list = _ps_single_quote(f'/i "{remote_path}" {install_args}')
            run_line = f"$p = Start-Process msiexec.exe -ArgumentList {arg_list} -Wait -PassThru"
        else:
            arg_list = _ps_single_quote(install_args)
            run_line = f"$p = Start-Process -FilePath {path} -ArgumentList {arg_list} -Wait -PassThru"
        task_name = f"AppInstall_{uuid.uuid4().hex[:12]}"
        script = f"""
function Get-UninstallEntries {{
    Get-ItemProperty @(
        'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
        'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
        'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'
    ) -ErrorAction SilentlyContinue |
        Where-Object {{ $_.DisplayName }} | ForEach-Object {{ "$($_.PSDrive.Name):$($_.PSChildName)" }}
}}
try {{
    $before = @(Get-UninstallEntries)
    Invoke-WebRequest -Uri {url} -OutFile {path} -UseBasicParsing
    {run_line}
    Remove-Item {path} -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {{
        "FAIL:installer exited with code $($p.ExitCode)" | Set-Content -Path '{{RESULT_PATH}}' -Force
    }} else {{
        $confirmed = $false
        for ($i = 0; $i -lt 120; $i++) {{
            $after = @(Get-UninstallEntries)
            if (@($after | Where-Object {{ $before -notcontains $_ }}).Count -gt 0) {{ $confirmed = $true; break }}
            Start-Sleep -Seconds 5
        }}
        if ($confirmed) {{
            "OK:installed (exit $($p.ExitCode)$(if ($p.ExitCode -eq 3010) {{ ', reboot required' }}))" | Set-Content -Path '{{RESULT_PATH}}' -Force
        }} else {{
            "FAIL:installer exited with code $($p.ExitCode) but no new registry Uninstall entry appeared within 10 minutes afterward (self-relaunching stub, or an installer that genuinely didn't complete)" | Set-Content -Path '{{RESULT_PATH}}' -Force
        }}
    }}
}} catch {{
    "FAIL:$($_.Exception.Message)" | Set-Content -Path '{{RESULT_PATH}}' -Force
}}
""".strip()
        return self._run_as_system_task(script, task_name, max_attempts=150)

    def rename_computer(self, new_name: str) -> WinRMResult:
        return self.run_ps(f"Rename-Computer -NewName '{new_name}' -Force")

    def disable_builtin_administrator(self) -> WinRMResult:
        """Only meaningful when a custom admin account exists to use
        instead - see run_post_install, which only calls this when
        template.custom_admin_enabled. Safe to run over this same
        session at any point in post-install: it authenticates as the
        custom account (template.local_admin_username), never
        "Administrator", so disabling that account here doesn't cut off
        the connection running the command."""
        return self.run_ps("Disable-LocalUser -Name 'Administrator'")

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

    # Fixed paths, not $env:TEMP: get_feature_install_status-style progress
    # polling (see provision.py) runs over a *separate* WinRM session/
    # process than the one running install_windows_updates itself (same
    # NTLM-session-corruption reason as the feature-install progress
    # check), so the two need a location that resolves the same way
    # regardless of which session touches it - a per-user %TEMP% would be
    # consistent here too (both sessions authenticate as the same local
    # admin account), but Windows\Temp is unambiguous and needs no
    # profile to already be loaded.
    _WINDOWS_UPDATE_STATUS_PATH = r"C:\Windows\Temp\deploycore_wu_status.txt"
    _WINDOWS_UPDATE_TASK_NAME = "DeployCoreWindowsUpdate"
    # Explicit tradeoff, not a default anyone should assume: skips the
    # monthly Cumulative Update (which on Server 2019+ is also that
    # month's security fix, no smaller one exists) for a much faster
    # pass that still gets drivers/definitions/small individual patches
    # - see install_windows_updates' own docstring.
    _WINDOWS_UPDATE_MAX_UPDATE_SIZE_BYTES = 150 * 1024 * 1024

    def install_windows_updates(self) -> WinRMResult:
        """Best-effort: searches Windows Update via the built-in WUA COM
        API (Microsoft.Update.Session) - no PSWindowsUpdate module or
        PSGallery/internet access needed beyond what Windows Update
        itself already requires - for every applicable, non-hidden
        update, not filtered down to only critical/security ones, so
        this also picks up optional/recommended updates (including
        drivers) the interactive Settings app would list separately.
        Downloads and installs whatever it finds. A VM built from a
        months-old ISO can be meaningfully behind day one; this catches
        it up in the same pass rather than leaving it to whenever
        Windows' own automatic update schedule gets to it - the caller
        (see provision.py) logs a failure here as a WARN and continues
        the rest of post-install regardless, never worth failing an
        otherwise-successful deployment over an update server hiccup.

        Runs via _run_as_system_task (a one-shot SYSTEM-context Scheduled
        Task, shared with install_app), not directly over this WinRM
        session: confirmed on a real deployment that
        $Session.CreateUpdateDownloader() throws UnauthorizedAccessException
        (E_ACCESSDENIED) when called from a WinRM/network-logon session -
        CreateUpdateSearcher() (read-only) works fine there, but WUA
        refuses to let a remote/network token drive an actual download or
        install. Running the same script under SYSTEM via Task Scheduler
        sidesteps that restriction entirely - this WinRM session only
        registers/starts the task and polls for it to finish, never runs
        the WUA calls itself.

        Clears ExcludeWUDriversInQualityUpdate first (best-effort, never
        fatal if it can't): some Windows Server images have "Do not
        include drivers with Windows Updates" set via local/group
        policy, which makes the WUA search itself silently exclude every
        Type='Driver' result - confirmed Microsoft-documented behavior
        of that specific policy, not just a Settings-app-UI filter - so
        a driver-inclusive search needs that value gone first or it
        never sees drivers to begin with, no Type filter on the search
        criteria itself would fix that.

        Skips anything over _WINDOWS_UPDATE_MAX_UPDATE_SIZE_BYTES
        (MaxDownloadSize, known before downloading) - deliberate,
        explicitly requested trade-off: on Windows Server 2019+ there's
        no separate small "security-only" patch anymore, the monthly
        Cumulative Update (usually the single largest item, often
        500MB-1.5GB) *is* that month's security fix, merged into one
        package - skipping it for speed means skipping that month's OS
        security patches too, not just bloat, in exchange for a much
        faster pass that still gets drivers, definition updates, and
        any other smaller individual patches. Sized instead of matched
        by classification: modern cumulative updates are classified as
        plain "Security Updates" the same as everything else worth
        keeping, there's no classification GUID that means "cumulative"
        specifically to exclude just that.

        Writes its current stage to a fixed status file throughout
        (search/download/install), read back by get_windows_update_progress
        for the deployment log's heartbeat - the scheduled task has no
        direct way to report progress back to this session otherwise."""
        max_size = self._WINDOWS_UPDATE_MAX_UPDATE_SIZE_BYTES
        wua_script = f"""
$marker = '{self._WINDOWS_UPDATE_STATUS_PATH}'
$result = '{{RESULT_PATH}}'
"searching for updates" | Set-Content -Path $marker -Force
try {{
    Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU' -Name 'ExcludeWUDriversInQualityUpdate' -ErrorAction SilentlyContinue
}} catch {{}}
try {{
    $Session = New-Object -ComObject Microsoft.Update.Session
    $Searcher = $Session.CreateUpdateSearcher()
    $SearchResult = $Searcher.Search("IsInstalled=0 and IsHidden=0")
    $Wanted = @($SearchResult.Updates | Where-Object {{ $_.MaxDownloadSize -le {max_size} }})
    $SkippedLarge = @($SearchResult.Updates | Where-Object {{ $_.MaxDownloadSize -gt {max_size} }})
    if ($SkippedLarge.Count -gt 0) {{
        $skippedNames = ($SkippedLarge | ForEach-Object {{ "$($_.Title) ($([math]::Round($_.MaxDownloadSize/1MB)) MB)" }}) -join '; '
        Add-Content -Path $marker -Value "`nskipped $($SkippedLarge.Count) update(s) over the size limit: $skippedNames" -Force
    }}
    if ($Wanted.Count -eq 0) {{
        "OK:No applicable updates under the size limit found." | Set-Content -Path $result -Force
    }} else {{
        "downloading $($Wanted.Count) update(s)" | Set-Content -Path $marker -Force
        $ToDownload = New-Object -ComObject Microsoft.Update.UpdateColl
        foreach ($u in $Wanted) {{ $ToDownload.Add($u) | Out-Null }}
        $Downloader = $Session.CreateUpdateDownloader()
        $Downloader.Updates = $ToDownload
        $Downloader.Download() | Out-Null
        $ToInstall = New-Object -ComObject Microsoft.Update.UpdateColl
        foreach ($u in $Wanted) {{ if ($u.IsDownloaded) {{ $ToInstall.Add($u) | Out-Null }} }}
        if ($ToInstall.Count -eq 0) {{
            "FAIL:Found $($Wanted.Count) update(s) but none downloaded successfully." | Set-Content -Path $result -Force
        }} else {{
            "installing $($ToInstall.Count) update(s)" | Set-Content -Path $marker -Force
            $Installer = $Session.CreateUpdateInstaller()
            $Installer.Updates = $ToInstall
            $InstallResult = $Installer.Install()
            $msg = "Installed $($ToInstall.Count) of $($Wanted.Count) applicable update(s) ($($SkippedLarge.Count) skipped for size), result code $($InstallResult.ResultCode), reboot required: $($InstallResult.RebootRequired)."
            if ($InstallResult.ResultCode -eq 2 -or $InstallResult.ResultCode -eq 3) {{
                "OK:$msg" | Set-Content -Path $result -Force
            }} else {{
                "FAIL:$msg" | Set-Content -Path $result -Force
            }}
        }}
    }}
}} catch {{
    "FAIL:$($_.Exception.Message)" | Set-Content -Path $result -Force
}}
Remove-Item -Path $marker -Force -ErrorAction SilentlyContinue
""".strip()
        # Windows Update can genuinely take a long time (a big cumulative
        # update, a slow update server) - poll generously rather than
        # timing out on anything realistic; run_post_install's own arq
        # job timeout is the real outer ceiling if this never finishes.
        return self._run_as_system_task(wua_script, self._WINDOWS_UPDATE_TASK_NAME, max_attempts=240)

    def get_windows_update_progress(self) -> str:
        """Best-effort progress read for install_windows_updates, run
        over a separate WinRM session while the main install call is
        still in flight (see the module-level note on
        _WINDOWS_UPDATE_STATUS_PATH for why it has to be a separate
        session). Empty string once the marker is gone - either the
        install hasn't started yet or it already finished - the caller
        treats that as "nothing new to report" rather than an error."""
        result = self.run_ps(
            f"Get-Content -Path '{self._WINDOWS_UPDATE_STATUS_PATH}' -ErrorAction SilentlyContinue"
        )
        return result.stdout.strip() if result.ok else ""

    def reboot(self) -> WinRMResult:
        """shutdown.exe, not the Restart-Computer cmdlet: confirmed on a
        real deployment that Restart-Computer -Force over this WinRM
        remote-shell execution model can complete "successfully" without
        ever actually restarting the guest - a known WinRS limitation,
        the spawned process doesn't get the shutdown privilege enabled
        even for an administrator account, unlike shutdown.exe itself
        which reliably triggers a real restart in this same context."""
        return self.run_ps("shutdown.exe /r /t 0 /f")

    def is_reachable(self) -> bool:
        try:
            return self.run_ps("Write-Output ok").ok
        except Exception:  # noqa: BLE001 - reachability probe, any failure means "not yet"
            return False
