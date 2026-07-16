@echo off
REM Sole purpose: the Scheduled Task's own /tr target, installed to
REM C:\ProgramData\DeployCore\ (no spaces anywhere in that path) instead of
REM pointing schtasks /tr directly at RunAgentInstall.cmd under
REM "C:\Program Files\DeployCore Remote Management Agent\" - confirmed live,
REM the real bug: quotes authored around that spaced path in the .wxs did not
REM survive the round trip through WixQuietExec64 into the registered task.
REM `schtasks /query /tn ... /xml` on a real failing install showed the
REM Action had been split at the first space -
REM <Command>C:\Program</Command><Arguments>Files\DeployCore Remote
REM Management Agent\RunAgentInstall.cmd</Arguments> - so Task Scheduler
REM tried to launch "C:\Program" (doesn't exist, error 0x80070002) instead of
REM the real file, which visibly existed on disk the whole time. This file's
REM own path needs no quoting at all, so there's nothing left to lose.
"C:\Program Files\DeployCore Remote Management Agent\RunAgentInstall.cmd"
