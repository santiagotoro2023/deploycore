import ipaddress

import winrm

PS_HEADER = "$ProgressPreference = 'SilentlyContinue'; $ErrorActionPreference = 'Stop'; "


class WinRMResult:
    def __init__(self, status_code: int, stdout: str, stderr: str) -> None:
        self.status_code = status_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.status_code == 0


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

    def install_feature(self, feature_name: str) -> WinRMResult:
        return self.run_ps(f"Install-WindowsFeature -Name {feature_name}")

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

    def reboot(self) -> WinRMResult:
        return self.run_ps("Restart-Computer -Force")

    def is_reachable(self) -> bool:
        try:
            return self.run_ps("Write-Output ok").ok
        except Exception:  # noqa: BLE001 - reachability probe, any failure means "not yet"
            return False
