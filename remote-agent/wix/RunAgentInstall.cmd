@echo off
REM Launched by a one-time Scheduled Task the MSI's custom action registers and
REM immediately triggers (see DeployCoreRemoteAgent.wxs's RegisterAgentTask /
REM RunAgentTask custom actions) - deliberately NOT run directly from inside
REM the MSI's own custom action. The original reason this pattern exists was
REM RustDesk-specific (a nested msiexec.exe call installing RustDesk would
REM have deadlocked inside the outer MSI's own _MSIExecute mutex) and no
REM longer applies - this script has no nested msiexec at all now, just
REM extracting a bundled zip. It's kept anyway for a reason that was always
REM independently true: deferred/commit custom actions can only be scheduled
REM between InstallInitialize and InstallFinalize (Windows Installer error
REM 2762), and the real work here - HTTP calls to fetch config and enroll -
REM would otherwise run synchronously inside the MSI transaction, extending
REM (and risking timing out) the visible "installing..." step for no reason.
REM Running from a Scheduled Task instead lets the outer MSI finish in
REM moments, with the actual work happening moments later, fully outside any
REM MSI transaction.
REM
REM No arguments here on purpose - the Scheduled Task's own /tr is a bare path
REM (see RegisterAgentTask), not a multi-argument command line. SERVERURL/
REM ENROLLTOKEN are read by the script itself from agent-params.ini (written
REM by WiX's own IniFile action at install time), not passed positionally -
REM confirmed necessary live: trusting Task Scheduler to correctly re-parse a
REM deeply nested-quoted /tr string at actual execution time (not just at
REM registration, which is all the old approach could ever have verified) was
REM producing a silently empty/lost enroll token on a real deployment.
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0Install-DeployCoreAgent.ps1"
