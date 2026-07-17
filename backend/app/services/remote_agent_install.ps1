#Requires -Version 5.1
<#
    DeployCore Remote Management Agent installer.

    Installs a stock RustDesk client as a headless Windows service, points it
    at this DeployCore instance's self-hosted server, and enrolls the machine
    so it shows up under Remote Management. RustDesk itself is meant to be
    fully invisible - absorbed into "the Agent" rather than a second, separate
    program - so this also: installs with no Desktop/Start Menu shortcuts or
    virtual printer, kills any interactive RustDesk GUI instance that
    auto-launches post-install (confirmed some builds do this regardless of
    the shortcut properties), and hides RustDesk's own Add/Remove Programs
    entry outright (not just renames it - two "DeployCore..." entries would
    still look like two separate programs). DeployCore's own branded tray
    companion (installed separately by the .msi) is the only local UI shown.

    This is the single source of truth for the install logic. It is:
      * served as-is by  GET /api/remote/install-script  (with the server URL
        baked in) for the one-line install shown on the Remote Management tab, and
      * bundled into the .msi (remote-agent/wix/), where a Scheduled Task the
        MSI's own custom action registers and triggers runs it - deliberately
        NOT run directly from inside the custom action itself, since this
        script's own nested msiexec.exe call (installing the bundled RustDesk
        client) would otherwise collide with the _MSIExecute mutex the outer
        MSI still holds for its whole InstallExecuteSequence. That task
        triggers ONSTART (not just once immediately) - confirmed necessary
        live: the DeployCore deployment pipeline's own unconditional final
        reboot can land mid-script (it doesn't wait for this background task,
        only for the wrapper .msi's own quick self-install), so this script
        has to be safely resumable across that reboot, not just re-runnable
        by choice. See DeployCoreRemoteAgent.wxs's own comments.

    Because a reboot can interrupt this mid-run and the Scheduled Task will
    simply fire it again on the next boot, every step here is written to be
    safe to re-run from scratch: RustDesk's own install is skipped if already
    present, the service (re)creation is idempotent, and the final enrollment
    call is explicitly safe to call more than once (see remote_agent.py).
    Nothing here assumes it's the only or first attempt.

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
# below, deliberately: an earlier version of this script validated
# $EnrollToken before creating this log, so a run that received an empty/lost
# token (confirmed live) left literally no trace anywhere that it ever ran.
$LogPath = "$env:ProgramData\DeployCore\agent-install.log"
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
Start-Transcript -Path $LogPath -Append | Out-Null

function Write-Step($m) { Write-Host "[DeployCore] $m" }

# Wrapped in its own try/catch (mirroring the main one further down) purely
# so Stop-Transcript always runs and the log file is always properly
# finalized, even if this very first step is what throws - exactly the
# "empty enroll token" case that used to vanish without a trace.
try {
    # Reads agent-params.ini (Section [DeployCore], keys ServerUrl/EnrollToken)
    # if either value is still missing after params/env vars - only meaningful
    # for the .msi path ($PSScriptRoot is only set when run via -File, which is
    # how RunAgentInstall.cmd invokes this; the one-liner path has no such file
    # and already gets both values via DC_SERVER/DC_TOKEN, a no-op there).
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

# Pinned stock RustDesk release - pinned, not 'latest', so an upstream release
# can't change install behaviour under us without a deliberate bump here. Must
# match the version build-agent-msi.yml downloads to bundle into the .msi.
$RustDeskVersion = "1.3.8"
$RustDeskMsiUrl  = "https://github.com/rustdesk/rustdesk/releases/download/$RustDeskVersion/rustdesk-$RustDeskVersion-x86_64.msi"
$InstallDir      = "$env:ProgramFiles\RustDesk"
$RustDeskExe     = Join-Path $InstallDir "rustdesk.exe"

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
# No shortcuts, no printer - RustDesk is meant to be invisible, absorbed into
# "the Agent" rather than a second visible program. These are the REAL public
# properties RustDesk's own component Conditions check (confirmed directly
# against its WiX source, res/msi/Package/Fragments/ShortcutProperties.wxs
# and Components/RustDesk.wxs) - NOT "CREATEDESKTOPSHORTCUTS"/
# "CREATESTARTMENUSHORTCUTS"/"INSTALLPRINTER", which is what an earlier
# version of this script passed based on stale third-party documentation that
# was never checked against the actual source. Those DO exist as properties,
# but only feed an indirect RegistrySearch/SetProperty chain that (for
# reasons not fully root-caused) didn't reliably take effect - the shortcuts
# kept installing anyway on a real deployment despite them being set. Setting
# the real, directly-checked properties sidesteps that chain entirely.
# STARTUPSHORTCUTS specifically controls a "RustDesk Tray" auto-launch
# shortcut RustDesk's own MSI installs into the Startup folder directly -
# this is what was producing a second, unbranded tray icon at every logon,
# entirely independent of anything --install-service-related.
# All three only compare against the literal values 1/"Y"/"y" (Startup
# shortcuts checks only 1) to mean "install it" - anything else, including
# 0, means don't.
$noTraceArgs = "DESKTOPSHORTCUTS=0", "STARTMENUSHORTCUTS=0", "STARTUPSHORTCUTS=0"
if (-not (Test-Path $RustDeskExe)) {
    $bundled = if ($PSScriptRoot) { Join-Path $PSScriptRoot "rustdesk-x86_64.msi" } else { $null }
    if ($bundled -and (Test-Path $bundled)) {
        Write-Step "Installing bundled RustDesk $RustDeskVersion..."
        Start-Process msiexec.exe -ArgumentList (@("/i", "`"$bundled`"", "/qn") + $noTraceArgs) -Wait
    } else {
        Write-Step "No bundled RustDesk installer found - downloading $RustDeskVersion..."
        $msi = Join-Path $env:TEMP "rustdesk-$RustDeskVersion.msi"
        Invoke-WebRequest -Uri $RustDeskMsiUrl -OutFile $msi -UseBasicParsing
        Write-Step "Installing..."
        Start-Process msiexec.exe -ArgumentList (@("/i", "`"$msi`"", "/qn") + $noTraceArgs) -Wait
        Remove-Item $msi -Force -ErrorAction SilentlyContinue
    }
}
if (-not (Test-Path $RustDeskExe)) { throw "RustDesk did not install to $RustDeskExe" }

# Some RustDesk builds auto-launch the interactive GUI right after install
# (confirmed live: a tray icon and desktop shortcut showed up even though
# nothing in this script asked for either) - that instance runs as whichever
# user is logged in, reading ITS OWN per-user config (not the one written to
# the SYSTEM profile below), so it's an unconfigured, fully vanilla RustDesk
# window with no relation to the managed service this script sets up. Kill it
# before it can register itself anywhere further (autostart, etc.) - the
# actual agent is the hidden background service installed below, not this.
Get-Process -Name "rustdesk" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# Remove any Desktop/Start Menu shortcuts RustDesk's installer created despite
# the properties above (belt and suspenders - confirmed some builds ignore
# them), for every user profile, not just the one currently logged in.
Get-ChildItem "$env:SystemDrive\Users" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    @(
        Join-Path $_.FullName "Desktop\RustDesk.lnk"
        Join-Path $_.FullName "AppData\Roaming\Microsoft\Windows\Start Menu\Programs\RustDesk.lnk"
    ) | Where-Object { Test-Path $_ } | Remove-Item -Force -ErrorAction SilentlyContinue
}
Remove-Item "$env:PUBLIC\Desktop\RustDesk.lnk" -Force -ErrorAction SilentlyContinue

# 3. Write the client config pointed at our server, headless (no tray/window).
#    verification-method=use-permanent-password + a permanent password below is
#    what makes unattended access (the login screen, before anyone signs in)
#    work at all.
#
#    Deliberately NOT $env:APPDATA - the service installed in step 4 always
#    runs as Local System (confirmed live in services.msc: "Log On As: Local
#    System"), which reads its OWN config from the SYSTEM profile, not
#    whichever user happens to be running this script. $env:APPDATA only
#    resolves to that same path when the script itself is ALSO already
#    running as SYSTEM (the .msi's ONSTART scheduled task, which is exactly
#    why that path never surfaced this bug) - the one-liner path is
#    documented as "Run standalone (as Administrator)", i.e. run BY a human
#    admin account, where $env:APPDATA resolves to THEIR OWN profile
#    instead. Confirmed live: the config written there was byte-for-byte
#    correct (right server, right key), yet the service could never be
#    found by ID from the browser or a native client - because the actual
#    running service, reading its own separate, never-written config, had
#    no idea our server even existed and silently kept trying RustDesk's own
#    public default one instead. --get-id/--password below talk to the
#    ALREADY-RUNNING service over IPC, not this file - RustDesk's ID
#    generation is 100% local (no rendezvous round-trip, confirmed
#    elsewhere in this script), so enrollment reporting a real, valid ID
#    back to DeployCore never actually proved the service's own network
#    config was ever correct, just that the service process was alive.
$confDir = "$env:SystemRoot\System32\config\systemprofile\AppData\Roaming\RustDesk\config"
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
#
#    Deliberately NOT using RustDesk's own `--install-service` CLI flag here -
#    confirmed via its actual source (platform/windows.rs install_service())
#    that it wraps the real work (sc create/start, plus copying a tray
#    shortcut into the all-users Startup folder, which we don't want at all -
#    see the hiding step below) in a call through Rust's `runas` crate with
#    force_prompt(true), which unconditionally tries to show a UAC-style
#    elevation prompt EVEN THOUGH this process is already SYSTEM. In a
#    Session-0/no-desktop context - this scheduled task, and any unattended
#    deployment with nobody logged into the console - that prompt can never
#    be shown or dismissed, so the whole call hangs forever: confirmed live,
#    the transcript stopped dead at "Installing service..." with no error,
#    ever. Since this process is already SYSTEM, none of that elevation
#    dance is needed - just run the same sc.exe commands directly.
Write-Step "Installing service..."
# Earlier versions of this script also created a THROWAWAY service pointed
# at `--import-config` first (mirroring RustDesk's own install_service() in
# platform/windows.rs), on the assumption that was the real mechanism for
# getting our config into the service's context, not just our own direct
# file write above. Confirmed via RustDesk's actual source
# (src/core_main.rs's import_config()) that this was never doing anything
# for us: it loads the FIRST (unsuffixed, "RustDesk.toml") path argument as
# `Config`, and returns IMMEDIATELY if that's empty - `if config.is_empty()
# { return; }` - before ever touching the "2.toml"/Config2 file our actual
# settings (hide-tray, verification-method, etc.) live in. We never write an
# unsuffixed RustDesk.toml at all (there's no user identity to migrate in a
# headless deployment - RustDesk generates a fresh ID on first run, which is
# exactly what we want), so that check was ALWAYS true and the whole
# throwaway-service step ALWAYS returned immediately having done nothing -
# not a regression, just dead weight copied from a reference implementation
# built for a different scenario (converting an already-configured
# interactive install into a service). Our own direct write to the system
# profile's RustDesk2.toml above is what actually seeds the config;
# removing this step also removes an extra service
# create/start/stop/delete cycle immediately before the one that matters.
#
# Uses New-Service, not `sc.exe create`, for the same reason as before:
# `sc.exe create RustDesk binpath= "\"$RustDeskExe\" --service" ...` embeds a
# quoted, space-containing exe path INSIDE another quoted argument - confirmed
# live as a real bug, Windows PowerShell 5.1's argument-passing to a native
# console executable does not reliably re-serialize a backtick-escaped nested
# quote for the child process's own command-line parser, and `sc.exe` rejected
# the resulting mangled command line with exit 1639 ("invalid command line
# argument"). New-Service takes -BinaryPathName as a real string value passed
# directly to the Service Control Manager API, never round-tripped through a
# re-parsed command line at all.
& sc.exe stop RustDesk 2>&1 | Out-Null
& sc.exe delete RustDesk 2>&1 | Out-Null
# sc.exe delete only MARKS a service for deletion - it doesn't complete until
# every open handle to it closes, which isn't guaranteed to happen within the
# same instant the command returns. Wait for it to actually disappear before
# recreating the same service name (matters most on a retry, where a prior
# attempt's service may still be tearing down).
$deleteWaited = 0
while ((Get-Service -Name RustDesk -ErrorAction SilentlyContinue) -and $deleteWaited -lt 10) {
    Start-Sleep -Milliseconds 500
    $deleteWaited += 0.5
}

Write-Step "Creating the RustDesk service..."
New-Service -Name RustDesk -BinaryPathName "`"$RustDeskExe`" --service" -DisplayName "RustDesk Service" -StartupType Automatic | Out-Null
Write-Step "Starting the RustDesk service..."
# Not fatal on failure here, on purpose: RustDesk's own reference
# get_create_service()/install_service() in platform/windows.rs runs `sc
# create` then `sc start` back to back with NO exit-code check on either at
# all - upstream itself doesn't treat an immediate "start" bookkeeping
# failure as fatal, likely because StartupType=Automatic means it tries
# again on next boot regardless, and a fresh service's very first start can
# be a genuine SCM-registration timing race independent of anything actually
# being wrong. `--get-id` a few lines below (which needs the service's IPC
# to actually be up) is the REAL verification - if the service truly isn't
# running, that fails with its own specific, clear error instead of this
# generic one.
try {
    Start-Service -Name RustDesk
} catch {
    # The generic .NET ServiceController exception ("the service cannot be
    # started") hides the actual Win32 reason - surface Win32_Service's own
    # ExitCode/State (the Service Control Manager's real status for this
    # exact service) for whoever reads this log next, then continue rather
    # than fail the whole install over what upstream itself doesn't check.
    $svcInfo = Get-CimInstance Win32_Service -Filter "Name='RustDesk'" -ErrorAction SilentlyContinue
    Write-Step "Start-Service reported failure (continuing - see --get-id below for the real check): $($_.Exception.Message)"
    if ($svcInfo) {
        Write-Step "Win32_Service state: State=$($svcInfo.State) ExitCode=$($svcInfo.ExitCode) StartMode=$($svcInfo.StartMode) PathName=$($svcInfo.PathName)"
    }
}
# Wait for the service to actually reach Running before touching it via IPC
# (--password/--get-id below) - Start-Service can return without throwing
# before the process has finished initializing its IPC listener, and
# Get-Service's own "Running" flag is the best readiness signal available
# without RustDesk exposing a dedicated health check.
$svcReady = $false
for ($i = 0; $i -lt 20; $i++) {
    $svc = Get-Service -Name RustDesk -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") { $svcReady = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $svcReady) {
    $svcInfo = Get-CimInstance Win32_Service -Filter "Name='RustDesk'" -ErrorAction SilentlyContinue
    Write-Step "RustDesk service did not reach Running within 20s - State=$($svcInfo.State) ExitCode=$($svcInfo.ExitCode) (continuing anyway - --get-id below will fail clearly if it's genuinely not up)"
}

# Both --password and --get-id below capture stderr/exit code explicitly,
# not just stdout via a bare `& $RustDeskExe ...` call - confirmed live as a
# real gap: RustDesk's own ID generation (Config::gen_id() -> get_auto_id())
# is 100% local (derived from the machine's MAC address via the
# mac_address crate) with no network/rendezvous-server round trip at all,
# so it should never legitimately come back empty - if it does anyway, the
# process is failing before reaching that code, and a Rust panic writes to
# STDERR while a nonzero native exit code raises no PowerShell error unless
# explicitly checked. A bare `& program.exe` capturing only stdout would
# show silent empty output either way, with the real reason invisible.
$rdStdErrDir = "$env:ProgramData\DeployCore"

$passStdout = Join-Path $rdStdErrDir "rustdesk-password-stdout.log"
$passStderr = Join-Path $rdStdErrDir "rustdesk-password-stderr.log"
$bytes = New-Object 'System.Byte[]' 18
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$permanentPassword = [Convert]::ToBase64String($bytes) -replace '[+/=]', 'x'
$passProc = Start-Process $RustDeskExe -ArgumentList "--password", $permanentPassword -Wait -PassThru -RedirectStandardOutput $passStdout -RedirectStandardError $passStderr
$passStderrContent = Get-Content $passStderr -Raw -ErrorAction SilentlyContinue
if ($passProc.ExitCode -ne 0 -or $passStderrContent) {
    Write-Step "--password exit code $($passProc.ExitCode)$(if ($passStderrContent) { " - stderr: $passStderrContent" })"
}

# --get-id talks to the running service over IPC, which can lag a moment
# behind Get-Service's own "Running" status - retry rather than give up on
# the first empty response, and log stderr/exit code on every failed
# attempt so a genuine, reproducible crash (as opposed to a one-off timing
# miss) shows up clearly instead of silently repeating 10 times.
$rustdeskId = $null
for ($i = 0; $i -lt 10; $i++) {
    $idStdout = Join-Path $rdStdErrDir "rustdesk-getid-stdout.log"
    $idStderr = Join-Path $rdStdErrDir "rustdesk-getid-stderr.log"
    $idProc = Start-Process $RustDeskExe -ArgumentList "--get-id" -Wait -PassThru -RedirectStandardOutput $idStdout -RedirectStandardError $idStderr
    $idOutput = Get-Content $idStdout -Raw -ErrorAction SilentlyContinue
    if ($idOutput) { $rustdeskId = $idOutput.Trim() }
    if ($rustdeskId) { break }
    $idStderrContent = Get-Content $idStderr -Raw -ErrorAction SilentlyContinue
    Write-Step "--get-id attempt $($i + 1): exit code $($idProc.ExitCode), stdout empty$(if ($idStderrContent) { ", stderr: $idStderrContent" })"
    Start-Sleep -Seconds 2
}
if (-not $rustdeskId) { throw "Could not read the RustDesk ID (--get-id returned nothing after repeated attempts) - see the per-attempt exit code/stderr logged above for the actual reason." }

# 5. RustDesk is meant to be invisible - absorbed into "the Agent," not a
#    second visible program - so its OWN Add/Remove Programs entry is hidden
#    outright (SystemComponent=1, the standard mechanism bundled/dependency
#    installers use to keep an installed product out of the visible list)
#    rather than just relabeled to another "DeployCore..." entry, which would
#    leave two confusingly-identical-looking entries instead of one. This
#    does NOT recompile anything - RustDesk's actual files/service are
#    unmodified, only its own uninstall registry entry's visibility changes.
#    The Windows service is also renamed (service key stays "RustDesk", only
#    the display name/description shown in services.msc changes).
#    What none of this reaches without a source recompile: the rustdesk.exe
#    process name, the C:\Program Files\RustDesk folder, and the in-session
#    "being controlled" banner.
$BrandName = "DeployCore Remote Management Agent"
Write-Step "Hiding RustDesk's own presence..."
try {
    $uninstallRoots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($root in $uninstallRoots) {
        Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
            $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
            if ($props.DisplayName -like "RustDesk*") {
                Set-ItemProperty $_.PSPath -Name SystemComponent -Value 1 -Type DWord
            }
        }
    }
    # Service display name (service key name stays "RustDesk").
    & sc.exe config RustDesk DisplayName= "$BrandName" | Out-Null
    & sc.exe description RustDesk "Secure remote management by DeployCore." | Out-Null
} catch {
    Write-Step "Hiding step skipped ($($_.Exception.Message)) - the agent still works."
}

# 6. Report ID + password home so the host flips to 'enrolled' in DeployCore.
Write-Step "Enrolling with DeployCore..."
$body = @{ rustdesk_id = $rustdeskId; rustdesk_key = $permanentPassword } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "$ServerUrl/api/remote/enroll/$EnrollToken" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing | Out-Null

Write-Step "Done. This machine is now reachable in DeployCore Remote Management (ID $rustdeskId)."

} catch {
    Write-Step "FAILED: $($_.Exception.Message)"
    Stop-Transcript | Out-Null
    # Deliberately NOT calling Remove-AgentTask here (unlike the success path
    # below) - the Scheduled Task's ONSTART trigger is what lets a failed or
    # interrupted run (a reboot landing mid-script, a transient network blip
    # right after boot) simply try again on the next boot instead of being
    # permanently stranded. Only a genuinely successful run removes it.
    # Re-thrown so the caller (schtasks-launched, effectively detached - see
    # this script's own header comment - or the one-liner's own shell) still
    # sees a real failure - this log is what explains WHY, since the exit code
    # alone never does (see the 1603 this replaced).
    throw
}

# Only reached on full success - see the catch block's own comment for why
# failure deliberately leaves the Scheduled Task in place instead of cleaning
# it up here too.
Remove-AgentTask
Stop-Transcript | Out-Null
