@echo off
REM Launched by a one-time Scheduled Task the MSI's custom action registers and
REM immediately triggers (see DeployCoreRemoteAgent.wxs's RegisterAgentTask /
REM RunAgentTask custom actions) - deliberately NOT run directly from inside
REM the MSI's own custom action, because that would nest a second msiexec.exe
REM call (to install the bundled RustDesk client) inside the _MSIExecute mutex
REM the outer MSI still holds for its entire InstallExecuteSequence, including
REM anything scheduled after InstallFinalize (confirmed via Microsoft's own
REM _MSIExecute Mutex docs) - a deadlock/ERROR_INSTALL_ALREADY_RUNNING that is
REM exactly what produced a bare, detail-free MSI 1603 on the first real test.
REM Running this from a Scheduled Task instead means the outer MSI has already
REM fully exited (mutex released) by the time this, and the nested msiexec it
REM runs, actually executes.
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
