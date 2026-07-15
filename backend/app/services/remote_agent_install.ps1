#Requires -Version 5.1
<#
    DeployCore Remote Management Agent installer.

    Installs a stock RustDesk client as a headless Windows service, points it
    at this DeployCore instance's self-hosted server, and enrolls the machine
    so it shows up under Remote Management. No RustDesk branding is shown on
    the machine (the service runs with hide-tray); DeployCore's own branded
    tray companion (installed separately by the .msi) is the only local UI.

    This is the single source of truth for the install logic. It is:
      * served as-is by  GET /api/remote/install-script  (with the server URL
        baked in) for the one-line install shown on the Remote Management tab, and
      * bundled into the .msi (remote-agent/wix/) whose custom action runs it.

    Run standalone (as Administrator):
      $env:DC_TOKEN = "<enroll-token>"; iwr <server>/api/remote/install-script | iex
    Or with explicit args:
      .\Install-DeployCoreAgent.ps1 -ServerUrl https://deploycore.example.com -EnrollToken <token>

    ponytail: all in PowerShell (no Python/other runtime on the target) and
    driven by RustDesk's own documented CLI flags - nothing here reverse-
    engineers RustDesk internals, so a RustDesk point release won't silently
    break it.
#>
[CmdletBinding()]
param(
    # Falls back to the server this script was served from (the route replaces
    # the placeholder) and then to the DC_SERVER/DC_TOKEN env vars the one-line
    # installer sets, so the piped `| iex` form needs no args.
    [string]$ServerUrl  = $env:DC_SERVER,
    [string]$EnrollToken = $env:DC_TOKEN
)

$ErrorActionPreference = "Stop"
if (-not $ServerUrl) { $ServerUrl = "__DEPLOYCORE_SERVER__" }
if (-not $EnrollToken) { throw "No enroll token. Set `$env:DC_TOKEN or pass -EnrollToken." }
$ServerUrl = $ServerUrl.TrimEnd("/")

# Pinned stock RustDesk release - pinned, not 'latest', so an upstream release
# can't change install behaviour under us without a deliberate bump here.
$RustDeskVersion = "1.3.8"
$RustDeskMsiUrl  = "https://github.com/rustdesk/rustdesk/releases/download/$RustDeskVersion/rustdesk-$RustDeskVersion-x86_64.msi"
$InstallDir      = "$env:ProgramFiles\RustDesk"
$RustDeskExe     = Join-Path $InstallDir "rustdesk.exe"

function Write-Step($m) { Write-Host "[DeployCore] $m" }

# 1. Pull this instance's server config (relay address + public key) using the
#    enroll token. This is what lets the agent trust and reach the self-hosted
#    server without anything being copied by hand.
Write-Step "Fetching server configuration..."
$cfg = Invoke-RestMethod -Uri "$ServerUrl/api/remote/agent-config/$EnrollToken" -UseBasicParsing

# 2. Install the stock RustDesk client silently, if it isn't already there.
if (-not (Test-Path $RustDeskExe)) {
    Write-Step "Downloading RustDesk $RustDeskVersion..."
    $msi = Join-Path $env:TEMP "rustdesk-$RustDeskVersion.msi"
    Invoke-WebRequest -Uri $RustDeskMsiUrl -OutFile $msi -UseBasicParsing
    Write-Step "Installing..."
    Start-Process msiexec.exe -ArgumentList "/i", "`"$msi`"", "/qn" -Wait
    Remove-Item $msi -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $RustDeskExe)) { throw "RustDesk did not install to $RustDeskExe" }

# 3. Write the client config pointed at our server, headless (no tray/window).
#    verification-method=use-permanent-password + a permanent password below is
#    what makes unattended access (the login screen, before anyone signs in)
#    work at all.
Write-Step "Configuring..."
$confDir = "$env:APPDATA\RustDesk\config"
New-Item -ItemType Directory -Force -Path $confDir | Out-Null
@"
rendezvous_server = '$($cfg.id_server)'
nat_type = 1
serial = 0

[options]
key = '$($cfg.key)'
custom-rendezvous-server = '$($cfg.relay_host)'
relay-server = '$($cfg.relay_server)'
hide-tray = 'Y'
verification-method = 'use-permanent-password'
allow-hide-cm = 'Y'
"@ | Set-Content -Path (Join-Path $confDir "RustDesk2.toml") -Encoding UTF8 -Force

# 4. Install as a service (persists through logout/reboot; reachable at the
#    login screen) and set a locally-generated permanent password. The password
#    is minted here on the machine and only ever leaves it over the HTTPS enroll
#    call below - DeployCore never chooses it.
Write-Step "Installing service..."
Start-Process $RustDeskExe -ArgumentList "--install-service" -Wait
Start-Sleep -Seconds 3

$bytes = New-Object 'System.Byte[]' 18
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$permanentPassword = [Convert]::ToBase64String($bytes) -replace '[+/=]', 'x'
Start-Process $RustDeskExe -ArgumentList "--password", $permanentPassword -Wait

$rustdeskId = (& $RustDeskExe --get-id).Trim()
if (-not $rustdeskId) { throw "Could not read the RustDesk ID (--get-id returned nothing)." }

# 5. Brand what installer-level tweaks can reach, so a user poking around
#    Windows sees "DeployCore", not "RustDesk". This does NOT recompile
#    anything - it just relabels the stock client's own registrations:
#      * the Add/Remove Programs entry (display name + publisher + icon), and
#      * the Windows service's display name (the service key stays "RustDesk",
#        but services.msc shows the friendly name).
#    What it deliberately can't reach without a source recompile: the
#    rustdesk.exe process name, the C:\Program Files\RustDesk folder, and the
#    in-session "being controlled" banner.
$BrandName = "DeployCore Remote Management Agent"
Write-Step "Applying branding..."
try {
    $uninstallRoots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($root in $uninstallRoots) {
        Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
            $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
            if ($props.DisplayName -like "RustDesk*") {
                Set-ItemProperty $_.PSPath -Name DisplayName -Value $BrandName
                Set-ItemProperty $_.PSPath -Name Publisher -Value "DeployCore"
                # Point the entry's icon at our own .ico if the installer dropped
                # one next to this script (the .msi does; the one-liner path skips
                # it and just keeps the renamed entry without a custom icon).
                $ico = Join-Path $PSScriptRoot "deploycore.ico"
                if (Test-Path $ico) { Set-ItemProperty $_.PSPath -Name DisplayIcon -Value $ico }
            }
        }
    }
    # Service display name (service key name stays "RustDesk").
    & sc.exe config RustDesk DisplayName= "$BrandName" | Out-Null
    & sc.exe description RustDesk "Secure remote management by DeployCore." | Out-Null
} catch {
    Write-Step "Branding step skipped ($($_.Exception.Message)) - the agent still works."
}

# 6. Report ID + password home so the host flips to 'enrolled' in DeployCore.
Write-Step "Enrolling with DeployCore..."
$body = @{ rustdesk_id = $rustdeskId; rustdesk_key = $permanentPassword } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "$ServerUrl/api/remote/enroll/$EnrollToken" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing | Out-Null

Write-Step "Done. This machine is now reachable in DeployCore Remote Management (ID $rustdeskId)."
