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

    def set_static_network(self, ip: str, netmask_prefix: int, gateway: str, dns: list[str]) -> WinRMResult:
        dns_list = ",".join(f"'{d}'" for d in dns)
        script = (
            "$adapter = Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object -First 1; "
            f"New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress '{ip}' "
            f"-PrefixLength {netmask_prefix} -DefaultGateway '{gateway}'; "
            f"Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses ({dns_list})"
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
