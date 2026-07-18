#Requires -Version 5.1
<#
    DeployCore Remote Management Agent installer - native agent, no RustDesk.

    Installs DeployCoreAgent.exe (this project's own capture/encode/WebRTC +
    RDP-tunnel service - see remote-agent/PROTOCOL.md and remote-agent/agent/)
    as a headless Windows service, points it at this DeployCore instance, and
    enrolls the machine so it shows up under Remote Management. There is no
    foreign product to hide or rebrand any more - the service, its config,
    and its installer are DeployCore's own from the start, which is also why
    this script is dramatically shorter than the RustDesk-based version it
    replaces: an entire class of problems (a foreign installer's own
    shortcuts/tray/Add-Remove-Programs entry, reading an ID/password back out
    of a running process over IPC, a UAC-hang workaround for someone else's
    --install-service flag) simply doesn't exist when the thing being
    installed is ours.

    This is the single source of truth for the install logic. It is:
      * served as-is by  GET /api/remote/install-script  (with the server URL
        baked in) for the one-line install shown on the Remote Management tab, and
      * bundled into the .msi (remote-agent/wix/), where a Scheduled Task the
        MSI's own custom action registers and triggers runs it - see
        DeployCoreRemoteAgent.wxs's own comments for why (a nested msiexec
        would collide with the outer MSI's own _MSIExecute mutex).

    Because a reboot can interrupt this mid-run and the Scheduled Task will
    simply fire it again on the next boot, every step here is written to be
    safe to re-run from scratch - the service (re)creation is idempotent, and
    enrollment is explicitly safe to call more than once (see remote_agent.py).

    Run standalone (as Administrator):
      $env:DC_TOKEN = "<enroll-token>"; iwr <server>/api/remote/install-script | iex
    Or with explicit args:
      .\Install-DeployCoreAgent.ps1 -ServerUrl https://deploycore.example.com -EnrollToken <token>
#>
[CmdletBinding()]
param(
    # Falls back to the server this script was served from (the route replaces
    # the placeholder), then the DC_SERVER/DC_TOKEN env vars the one-line
    # installer sets, then agent-params.ini next to this script (written by
    # the .msi's own WiX IniFile action - see DeployCoreRemoteAgent.wxs) if
    # still empty, so neither install path needs to pass these positionally.
    [string]$ServerUrl  = $env:DC_SERVER,
    [string]$EnrollToken = $env:DC_TOKEN
)

$ErrorActionPreference = "Stop"

# Always-on log on the machine itself, independent of MSI logging (which
# nothing here enables by default) - a VM deployed by the DeployCore pipeline
# has no interactive session to watch this run. Started before ANY validation
# below, deliberately, so a run that receives an empty/lost token still
# leaves a trace of having started at all.
$LogPath = "$env:ProgramData\DeployCore\agent-install.log"
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
Start-Transcript -Path $LogPath -Append | Out-Null

function Write-Step($m) { Write-Host "[DeployCore] $m" }

try {
    if ((-not $ServerUrl -or -not $EnrollToken) -and $PSScriptRoot) {
        $iniPath = Join-Path $PSScriptRoot "agent-params.ini"
        if (Test-Path $iniPath) {
            Write-Step "Reading server URL / enroll token from agent-params.ini..."
            $iniValues = @{}
            Get-Content $iniPath | ForEach-Object {
                if ($_ -match '^\s*(\w+)\s*=\s*(.*?)\s*$') { $iniValues[$matches[1]] = $matches[2] }
            }
            if (-not $ServerUrl) { $ServerUrl = $iniValues["ServerUrl"] }
            if (-not $EnrollToken) { $EnrollToken = $iniValues["EnrollToken"] }
        } else {
            Write-Step "No agent-params.ini found at $iniPath"
        }
    }

    if (-not $ServerUrl) { $ServerUrl = "__DEPLOYCORE_SERVER__" }
    if (-not $EnrollToken) { throw "No enroll token. Set `$env:DC_TOKEN, pass -EnrollToken, or check agent-params.ini." }
    $ServerUrl = $ServerUrl.TrimEnd("/")
} catch {
    Write-Step "FAILED: $($_.Exception.Message)"
    Stop-Transcript | Out-Null
    throw
}

$InstallDir  = "$env:ProgramFiles\DeployCore Remote Management Agent"
$AgentExe    = Join-Path $InstallDir "DeployCoreAgent.exe"
$FfmpegExe   = Join-Path $InstallDir "ffmpeg.exe"
$ConfigDir   = "$env:ProgramData\DeployCore"
$ConfigPath  = Join-Path $ConfigDir "agent-config.json"
$ServiceName = "DeployCoreRemoteAgent"

# Best-effort: removes the one-time "DeployCoreAgentInstall" Scheduled Task the
# .msi's custom action registered to run this script (see this file's own
# header comment). A no-op on the one-liner path, which never registered one.
function Remove-AgentTask {
    try { & schtasks.exe /delete /tn DeployCoreAgentInstall /f 2>&1 | Out-Null } catch {}
}

# Stubbed pending a real bundled driver choice (see docs/remote-agent-native-plan.md
# section 1, "IDD driver": itsmikethetech/Virtual-Display-Driver or Microsoft's
# own IndirectDisplay sample lineage are the two candidates to evaluate).
# Returns $false so DeployCoreAgent.exe knows to fall back to view-only
# scaling (mirror the console at its real resolution, scale client-side) -
# a real, working mode on its own, not a broken half-feature - rather than
# silently pretending an exact-resolution virtual monitor exists when it
# doesn't. Wire this up for real once a specific driver is chosen and its
# installer/signing story is settled; nothing else in this script depends on
# it either way.
function Install-VirtualDisplayDriver {
    Write-Step "Virtual display driver: not yet bundled - Shadow will mirror the console's real resolution (view-only scaling) until this is wired up."
    return $false
}

try {

# 1. Pull this instance's TURN config using the enroll token, so the agent
#    can build its own ICE server list for the Shadow WebRTC path with
#    nothing copied by hand. No relay/server-key concept any more - the
#    agent's control channel just connects to $ServerUrl directly, same as
#    every other call in this script.
Write-Step "Fetching server configuration..."
$cfg = Invoke-RestMethod -Uri "$ServerUrl/api/remote/agent-config/$EnrollToken" -UseBasicParsing

# 2. Install DeployCoreAgent.exe (+ ffmpeg.exe) if not already present.
#    Prefers copies bundled next to this script (the .msi packages them,
#    built by CI - see build-agent-msi.yml) over downloading here: a VM the
#    DeployCore deployment pipeline just provisioned commonly has NO
#    outbound internet access by design, and the only network access it's
#    guaranteed to have is to DeployCore itself, which is where the .msi
#    came from in the first place. Only the one-liner path (a human running
#    this manually, normally with their own internet) ever needs the
#    download - same fallback shape this script has always used.
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if (-not (Test-Path $AgentExe) -or -not (Test-Path $FfmpegExe)) {
    $bundledZip = if ($PSScriptRoot) { Join-Path $PSScriptRoot "DeployCoreAgent.zip" } else { $null }
    if ($bundledZip -and (Test-Path $bundledZip)) {
        Write-Step "Installing bundled agent..."
        Expand-Archive -Path $bundledZip -DestinationPath $InstallDir -Force
    } else {
        Write-Step "No bundled agent found - downloading the latest build..."
        $zip = Join-Path $env:TEMP "DeployCoreAgent.zip"
        Invoke-WebRequest -Uri "https://github.com/santiagotoro2023/deploycore/releases/download/agent-latest/DeployCoreAgent.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $InstallDir -Force
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
    }
}
if (-not (Test-Path $AgentExe)) { throw "DeployCoreAgent.exe did not install to $AgentExe" }

# 3. Best-effort: a virtual display driver for exact-resolution Shadow (see
#    the stub above for the current state of this).
$hasVirtualDisplay = Install-VirtualDisplayDriver

# 4. Enable RDP for Connect mode - Windows' own RDP server (TermService)
#    does the entire job there (see remote-agent/PROTOCOL.md); the only
#    thing this script needs to do is make sure it's reachable. Idempotent -
#    both are safe to set to the same value repeatedly.
Write-Step "Enabling Remote Desktop..."
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" -Name "fDenyTSConnections" -Value 0
Enable-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction SilentlyContinue

# 5. Enroll BEFORE installing the service (unlike the RustDesk-based version,
#    which had to enroll last because the ID/password could only be read
#    back from an already-running process) - agent_key is minted by
#    DeployCore itself and doesn't depend on anything local, so there's
#    nothing left to wait for. If this fails, the service never gets
#    installed pointed at a config with no credential in it.
Write-Step "Enrolling with DeployCore..."
$enrollResponse = Invoke-RestMethod -Uri "$ServerUrl/api/remote/enroll/$EnrollToken" -Method Post -ContentType "application/json" -UseBasicParsing

# 6. Write the agent's config. Plaintext here, but ACL'd to
#    SYSTEM/Administrators only (ProgramData is world-readable by default,
#    unlike the RustDesk-based version's config path under
#    ServiceProfiles\LocalService, which inherited restrictive permissions
#    for free) - DeployCoreAgent.exe re-protects agent_key with DPAPI on its
#    own first run and never trusts the plaintext file again after that.
Write-Step "Writing agent configuration..."
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
@{
    serverUrl    = $ServerUrl
    enrollToken  = $EnrollToken
    agentKey     = $enrollResponse.agent_key
    turnHost     = $cfg.turn_host
    turnPort     = $cfg.turn_port
    turnUsername = $cfg.turn_username
    turnPassword = $cfg.turn_password
    virtualDisplay = $hasVirtualDisplay
} | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8 -Force
& icacls.exe $ConfigPath /inheritance:r /grant:r "SYSTEM:(F)" "*S-1-5-32-544:(F)" | Out-Null  # *S-1-5-32-544 = Administrators, well-known SID (locale-independent)

# 7. Install as a service (persists through logout/reboot). New-Service
#    takes -BinaryPathName as a real string passed directly to the Service
#    Control Manager API, never round-tripped through a re-parsed command
#    line the way `sc.exe create ... binpath= "..."` is - avoids a class of
#    quoting bug the RustDesk-based version of this script hit and fixed
#    the same way.
Write-Step "Installing service..."
& sc.exe stop $ServiceName 2>&1 | Out-Null
& sc.exe delete $ServiceName 2>&1 | Out-Null
$deleteWaited = 0
while ((Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) -and $deleteWaited -lt 10) {
    Start-Sleep -Milliseconds 500
    $deleteWaited += 0.5
}
New-Service -Name $ServiceName -BinaryPathName "`"$AgentExe`"" -DisplayName "DeployCore Remote Management Agent" -Description "Secure remote management by DeployCore." -StartupType Automatic | Out-Null
Write-Step "Starting service..."
try {
    Start-Service -Name $ServiceName
} catch {
    $svcInfo = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    Write-Step "Start-Service reported failure (continuing - Task Scheduler retries on next boot regardless of StartupType=Automatic if this is a timing race): $($_.Exception.Message)"
    if ($svcInfo) {
        Write-Step "Win32_Service state: State=$($svcInfo.State) ExitCode=$($svcInfo.ExitCode) StartMode=$($svcInfo.StartMode) PathName=$($svcInfo.PathName)"
    }
}

Write-Step "Done. This machine is now reachable in DeployCore Remote Management."

} catch {
    Write-Step "FAILED: $($_.Exception.Message)"
    Stop-Transcript | Out-Null
    # Deliberately NOT calling Remove-AgentTask here (unlike the success path
    # below) - the Scheduled Task's ONSTART trigger is what lets a failed or
    # interrupted run (a reboot landing mid-script, a transient network blip
    # right after boot) simply try again on the next boot instead of being
    # permanently stranded. Only a genuinely successful run removes it.
    throw
}

Remove-AgentTask
Stop-Transcript | Out-Null
