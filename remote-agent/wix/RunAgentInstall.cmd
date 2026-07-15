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
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0Install-DeployCoreAgent.ps1" -ServerUrl "%~1" -EnrollToken "%~2"
