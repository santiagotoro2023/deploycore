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
      * bundled into the .msi (remote-agent/wix/), where a one-time Scheduled
        Task the MSI's own custom action registers and triggers runs it -
        deliberately NOT run directly from inside the custom action itself,
        since this script's own nested msiexec.exe call (installing the
        bundled RustDesk client) would otherwise collide with the
        _MSIExecute mutex the outer MSI still holds for its whole
        InstallExecuteSequence. See DeployCoreRemoteAgent.wxs's own comments.

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

# Always-on log on the machine itself, independent of MSI logging (which
# nothing here enables by default) - a VM deployed by the DeployCore pipeline
# has no interactive session to watch this run, and the only thing that
# reached DeployCore's own deployment log before this existed was a bare
# "installer exited with code 1603" with no detail on WHY.
$LogPath = "$env:ProgramData\DeployCore\agent-install.log"
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
Start-Transcript -Path $LogPath -Append | Out-Null

# Pinned stock RustDesk release - pinned, not 'latest', so an upstream release
# can't change install behaviour under us without a deliberate bump here. Must
# match the version build-agent-msi.yml downloads to bundle into the .msi.
$RustDeskVersion = "1.3.8"
$RustDeskMsiUrl  = "https://github.com/rustdesk/rustdesk/releases/download/$RustDeskVersion/rustdesk-$RustDeskVersion-x86_64.msi"
$InstallDir      = "$env:ProgramFiles\RustDesk"
$RustDeskExe     = Join-Path $InstallDir "rustdesk.exe"

function Write-Step($m) { Write-Host "[DeployCore] $m" }

# Best-effort: removes the one-time "DeployCoreAgentInstall" Scheduled Task the
# .msi's custom action registered to run this script (see this file's own
# header comment). Deleting the task definition while it's still the one
# running is fine - Windows lets an in-progress task instance keep running
# after its definition is removed. A no-op (silently fails, which is fine) on
# the one-liner path, which never registered any such task.
function Remove-AgentTask {
    try { & schtasks.exe /delete /tn DeployCoreAgentInstall /f 2>&1 | Out-Null } catch {}
}

try {

# 1. Pull this instance's server config (relay address + public key) using the
#    enroll token. This is what lets the agent trust and reach the self-hosted
#    server without anything being copied by hand.
Write-Step "Fetching server configuration..."
$cfg = Invoke-RestMethod -Uri "$ServerUrl/api/remote/agent-config/$EnrollToken" -UseBasicParsing

# 2. Install the stock RustDesk client silently, if it isn't already there.
#    Prefers a copy bundled next to this script (the .msi packages one, built
#    by CI on a machine that has internet - see build-agent-msi.yml) over
#    downloading it here: a VM the DeployCore deployment pipeline just
#    provisioned commonly has NO outbound internet access by design (this is
#    exactly what broke the very first real deployment test - a generic MSI
#    1603 with no detail, traced to this download failing silently). The only
#    network access a pipeline-deployed VM is guaranteed to have is to
#    DeployCore itself, which is where the .msi carrying the bundled copy came
#    from in the first place. Only the one-liner path (a human running this
#    manually, normally with their own internet) ever needs the download.
if (-not (Test-Path $RustDeskExe)) {
    $bundled = if ($PSScriptRoot) { Join-Path $PSScriptRoot "rustdesk-x86_64.msi" } else { $null }
    if ($bundled -and (Test-Path $bundled)) {
        Write-Step "Installing bundled RustDesk $RustDeskVersion..."
        Start-Process msiexec.exe -ArgumentList "/i", "`"$bundled`"", "/qn" -Wait
    } else {
        Write-Step "No bundled RustDesk installer found - downloading $RustDeskVersion..."
        $msi = Join-Path $env:TEMP "rustdesk-$RustDeskVersion.msi"
        Invoke-WebRequest -Uri $RustDeskMsiUrl -OutFile $msi -UseBasicParsing
        Write-Step "Installing..."
        Start-Process msiexec.exe -ArgumentList "/i", "`"$msi`"", "/qn" -Wait
        Remove-Item $msi -Force -ErrorAction SilentlyContinue
    }
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

} catch {
    Write-Step "FAILED: $($_.Exception.Message)"
    Remove-AgentTask
    Stop-Transcript | Out-Null
    # Re-thrown so the caller (schtasks-launched, effectively detached - see
    # this script's own header comment - or the one-liner's own shell) still
    # sees a real failure - this log is what explains WHY, since the exit code
    # alone never does (see the 1603 this replaced).
    throw
}

Remove-AgentTask
Stop-Transcript | Out-Null
