using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Extensions.Logging;

namespace DeployCoreAgent;

/// <summary>
/// Launches a process INTO the active console session (Session 1+, wherever
/// the real console actually is) instead of as a normal child of this
/// service - which would otherwise inherit Session 0, same as every other
/// Windows service, with no access to the real interactive desktop at all.
///
/// THIS IS THE CONFIRMED ROOT CAUSE of Shadow's original black screen (see
/// agent.log from that first real end-to-end test: the WebRTC peer
/// connection reached "connected" - networking is session-independent, so
/// that part was always going to work - but zero video frames were ever
/// logged, because ffmpeg, launched via plain Process.Start from
/// ShadowSession, inherited THIS SERVICE's own Session 0).
///
/// THIRD REVISION. The first fix (retarget this service's own SYSTEM
/// token's session id via SetTokenInformation) compiled and ran with no
/// error but never actually worked. The second fix (steal explorer.exe's or
/// winlogon.exe's own token, target "winsta0\default", build a per-token
/// environment block for the logged-in case) was built from a genuinely
/// real reference (rustdesk/rustdesk's GetSessionUserTokenWin/
/// LaunchProcessWin, src/platform/windows.cc) but STILL didn't work -
/// ffmpeg kept dying before even writing its own -report file, on both the
/// winlogon.exe and explorer.exe paths, environment block or not. The
/// mistake: that C function has an "as_user"/"show" parameter pair this
/// project inferred defaults for instead of tracing back to how
/// rustdesk's OWN caller actually uses it for the exact same job (starting
/// its screen-capture server process). Fetched src/platform/windows.rs
/// directly and found the real call:
///
///     let h = unsafe { LaunchProcessWin(wstr, session_id, FALSE, FALSE, &mut token_pid) };
///
/// as_user=FALSE and show=FALSE, unconditionally, for their own
/// screen-capture server launch - not "explorer.exe when logged in", not
/// "winsta0\default", not a custom environment block. Concretely:
///   - as_user=FALSE -> GetLogonPid always looks for winlogon.exe, never
///     explorer.exe, regardless of whether anyone is logged in.
///   - show=FALSE -> LaunchProcessWin's own STARTUPINFO.lpDesktop is only
///     ever set `if (show)` - with show=FALSE it stays unset entirely.
///   - as_user=FALSE also means LaunchProcessWin's own
///     `if (as_user) CreateEnvironmentBlock(...)` never runs either -
///     lpEnvironment stays NULL.
///
/// FIFTH REVISION. Two separate things had to be right, and each was found
/// the hard way on real hardware:
///
/// 1. lpDesktop must be set EXPLICITLY. A NULL lpDesktop makes the child
///    inherit the PARENT's window station - this Session-0 service's
///    non-interactive service station - which, being both non-interactive and
///    cross-session, win32k/csrss cannot bind, so the process is destroyed
///    during init (ffmpeg died before main(), never writing its own -report).
///    Copying rustdesk's show=FALSE/no-lpDesktop launch was the wrong lesson:
///    its "--server" is rustdesk's OWN in-process capture engine that
///    re-attaches to the input desktop at runtime, not a spawned gdigrab.
///    Needs CharSet.Unicode on STARTUPINFO or the string is silently mangled
///    into CreateProcessAsUserW.
///
/// 2. The TOKEN must be the signed-in user's, not winlogon's SYSTEM token.
///    With lpDesktop fixed, ffmpeg started and read the screen geometry
///    ("Capturing whole desktop as 1024x768x32") but every frame failed with
///    "Failed to capture image (error 5)" = ERROR_ACCESS_DENIED, and the
///    in-session helper's ChangeDisplaySettingsEx returned -1
///    (DISP_CHANGE_FAILED) - both symptoms of acting on a desktop the process
///    doesn't own. StartInActiveSession now uses WTSQueryUserToken to get the
///    signed-in user's own token (duplicated to a primary token, with that
///    user's environment block) and the Default desktop, falling back to
///    winlogon.exe's token and the WINLOGON desktop only when nobody is
///    signed in - which is exactly the sign-in-screen case where the Winlogon
///    desktop is the one actually on screen.
///
/// Requires SeDebugPrivilege (see EnsureDebugPrivilege), not just
/// SeTcbPrivilege - confirmed via rustdesk-org's own "impersonate-system"
/// tool, which documents this exact requirement: "SeDebugPrivilege is
/// enabled... as it's required to open a HANDLE to winlogon.exe".
///
/// Deliberately does NOT try to redirect the launched process's stdout via
/// an inherited pipe handle across the CreateProcessAsUser boundary -
/// confirmed via research (not assumed) that ffmpeg writing to a named pipe
/// as its OUTPUT target is a known-unreliable pattern on Windows (it
/// commonly creates a plain file at that path instead of actually opening
/// the pipe). ShadowSession instead points ffmpeg at a real file and tails
/// it - see that class - which needs no handle-inheritance plumbing here at
/// all, at the cost of a small amount of disk I/O that's genuinely
/// negligible at this data rate (a low-fps H.264 stream, not raw video).
/// </summary>
internal static class SessionCapture
{
    #region Win32 constants

    private const int CREATE_NO_WINDOW = 0x08000000;
    private const uint INVALID_SESSION_ID = 0xFFFFFFFF;
    private static readonly IntPtr WTS_CURRENT_SERVER_HANDLE = IntPtr.Zero;

    private const uint TOKEN_ADJUST_PRIVILEGES = 0x0020;
    private const uint TOKEN_QUERY = 0x0008;
    private const uint SE_PRIVILEGE_ENABLED = 0x00000002;

    // Matches rustdesk/rustdesk's own GetSessionUserTokenWin exactly
    // (src/platform/windows.cc: OpenProcessToken(hProcess, TOKEN_ALL_ACCESS,
    // ...)) rather than hand-picking a narrower access mask.
    private const uint TOKEN_ALL_ACCESS = 0x000F01FF;
    private const uint PROCESS_QUERY_INFORMATION = 0x0400;

    #endregion

    #region Win32 structs

    private enum WTS_CONNECTSTATE_CLASS
    {
        WTSActive, WTSConnected, WTSConnectQuery, WTSShadow, WTSDisconnected,
        WTSIdle, WTSListen, WTSReset, WTSDown, WTSInit,
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WTS_SESSION_INFO
    {
        public readonly uint SessionID;
        [MarshalAs(UnmanagedType.LPStr)] public readonly string pWinStationName;
        public readonly WTS_CONNECTSTATE_CLASS State;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;
        public IntPtr hThread;
        public uint dwProcessId;
        public uint dwThreadId;
    }

    // CharSet.Unicode is REQUIRED, not cosmetic: CreateProcessAsUser below is
    // declared CharSet.Unicode (so it binds CreateProcessAsUserW, which reads
    // a STARTUPINFOW with wide-char lpDesktop). Without this attribute the
    // struct defaults to ANSI marshalling, so the "winsta0\default" string set
    // in StartInActiveSession would be written as ANSI bytes into a field the
    // W function reads as UTF-16 - a garbage desktop name. This is exactly
    // what defeated an earlier revision that DID set lpDesktop but still never
    // captured. Mirrors the DEVMODE struct in Win32Interop.cs, which already
    // gets this right.
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public string? lpReserved;
        public string? lpDesktop;
        public string? lpTitle;
        public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LUID { public uint LowPart; public int HighPart; }

    [StructLayout(LayoutKind.Sequential)]
    private struct LUID_AND_ATTRIBUTES { public LUID Luid; public uint Attributes; }

    [StructLayout(LayoutKind.Sequential)]
    private struct TOKEN_PRIVILEGES { public uint PrivilegeCount; public LUID_AND_ATTRIBUTES Privileges; }

    #endregion

    #region DllImports

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CreateProcessAsUser(
        IntPtr hToken, string? lpApplicationName, string lpCommandLine, IntPtr lpProcessAttributes,
        IntPtr lpThreadAttributes, bool bInheritHandles, uint dwCreationFlags, IntPtr lpEnvironment,
        string? lpCurrentDirectory, ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    [DllImport("kernel32.dll")]
    private static extern uint WTSGetActiveConsoleSessionId();

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern int WTSEnumerateSessions(IntPtr hServer, int reserved, int version, out IntPtr ppSessionInfo, out int pCount);

    [DllImport("wtsapi32.dll")]
    private static extern void WTSFreeMemory(IntPtr pMemory);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, uint dwProcessId);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool OpenProcessToken(IntPtr processHandle, uint desiredAccess, out IntPtr tokenHandle);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool LookupPrivilegeValue(string? lpSystemName, string lpName, out LUID lpLuid);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool AdjustTokenPrivileges(IntPtr tokenHandle, bool disableAllPrivileges,
        ref TOKEN_PRIVILEGES newState, uint bufferLength, IntPtr previousState, IntPtr returnLength);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();

    // The logged-in user's own token for a session (fails with
    // ERROR_NO_TOKEN when nobody is logged in - that's the login-screen case,
    // handled by falling back to winlogon's token).
    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSQueryUserToken(uint sessionId, out IntPtr phToken);

    // WTSQueryUserToken hands back an impersonation-capable token;
    // CreateProcessAsUser requires a PRIMARY one.
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool DuplicateTokenEx(
        IntPtr hExistingToken, uint dwDesiredAccess, IntPtr lpTokenAttributes,
        int impersonationLevel, int tokenType, out IntPtr phNewToken);

    [DllImport("userenv.dll", SetLastError = true)]
    private static extern bool CreateEnvironmentBlock(out IntPtr lpEnvironment, IntPtr hToken, bool bInherit);

    [DllImport("userenv.dll", SetLastError = true)]
    private static extern bool DestroyEnvironmentBlock(IntPtr lpEnvironment);

    private const uint MAXIMUM_ALLOWED = 0x02000000;
    private const int SecurityImpersonation = 2;
    private const int TokenPrimary = 1;
    private const int CREATE_UNICODE_ENVIRONMENT = 0x00000400;

    // The interactive desktop of a normal, signed-in session. Anything that
    // draws, captures, injects input, or changes the display mode must be on
    // this desktop (or on Winlogon below, at the sign-in screen).
    private const string DesktopDefault = @"winsta0\default";
    // The sign-in / lock / UAC desktop. When nobody is signed in, THIS is the
    // desktop actually being displayed - capturing "default" there yields an
    // access error or a blank image, which is why the two cases can't share
    // one desktop name.
    private const string DesktopWinlogon = @"winsta0\Winlogon";

    #endregion

    private static void EnsurePrivilege(ILogger logger, string privilegeName)
    {
        try
        {
            if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, out var hToken))
            {
                logger.LogWarning("OpenProcessToken failed (0x{Error:X}) - Shadow's session-launch may not work.", Marshal.GetLastWin32Error());
                return;
            }
            try
            {
                if (!LookupPrivilegeValue(null, privilegeName, out var luid))
                {
                    logger.LogWarning("LookupPrivilegeValue({Privilege}) failed (0x{Error:X}).", privilegeName, Marshal.GetLastWin32Error());
                    return;
                }
                var privileges = new TOKEN_PRIVILEGES
                {
                    PrivilegeCount = 1,
                    Privileges = new LUID_AND_ATTRIBUTES { Luid = luid, Attributes = SE_PRIVILEGE_ENABLED },
                };
                if (!AdjustTokenPrivileges(hToken, false, ref privileges, 0, IntPtr.Zero, IntPtr.Zero))
                {
                    logger.LogWarning("AdjustTokenPrivileges({Privilege}) failed (0x{Error:X}).", privilegeName, Marshal.GetLastWin32Error());
                    return;
                }
                logger.LogInformation("{Privilege} enabled.", privilegeName);
            }
            finally
            {
                CloseHandle(hToken);
            }
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Could not ensure {Privilege} - Shadow's session-launch may not work.", privilegeName);
        }
    }

    /// <summary>
    /// Needed to steal winlogon.exe's own token via OpenProcessToken below -
    /// held by LocalSystem's token (which this service runs as -
    /// New-Service with no -Credential in remote_agent_install.ps1 defaults
    /// to LocalSystem) but not necessarily ENABLED by default. Called once
    /// at agent startup (see Program.cs), not per-session.
    /// </summary>
    public static void EnsureTcbPrivilege(ILogger logger) => EnsurePrivilege(logger, "SeTcbPrivilege");

    /// <summary>
    /// Needed to OpenProcess a protected system process (winlogon.exe) that
    /// belongs to a DIFFERENT session than this service's own Session 0 -
    /// confirmed requirement, not assumed (rustdesk-org's own
    /// "impersonate-system" tool: "SeDebugPrivilege is enabled... as it's
    /// required to open a HANDLE to winlogon.exe"). Called once at agent
    /// startup, alongside EnsureTcbPrivilege.
    /// </summary>
    public static void EnsureDebugPrivilege(ILogger logger) => EnsurePrivilege(logger, "SeDebugPrivilege");

    private static uint GetActiveConsoleSessionId()
    {
        // WTSEnumerateSessions + the first WTSActive entry first (the
        // documented, more reliable way to find the session an actual
        // logged-in user owns); WTSGetActiveConsoleSessionId as a fallback
        // for when enumeration itself doesn't turn up an active session.
        if (WTSEnumerateSessions(WTS_CURRENT_SERVER_HANDLE, 0, 1, out var pSessionInfo, out var count) != 0)
        {
            try
            {
                var elementSize = Marshal.SizeOf<WTS_SESSION_INFO>();
                for (var i = 0; i < count; i++)
                {
                    var info = Marshal.PtrToStructure<WTS_SESSION_INFO>(pSessionInfo + i * elementSize);
                    if (info.State == WTS_CONNECTSTATE_CLASS.WTSActive) return info.SessionID;
                }
            }
            finally
            {
                WTSFreeMemory(pSessionInfo);
            }
        }
        return WTSGetActiveConsoleSessionId();
    }

    private static uint FindProcessIdInSession(uint sessionId, string processName)
    {
        foreach (var proc in Process.GetProcessesByName(processName))
        {
            using (proc)
            {
                try
                {
                    if ((uint)proc.SessionId == sessionId) return (uint)proc.Id;
                }
                catch
                {
                    // Exited between enumeration and this check - skip it,
                    // there may be another match (there normally isn't, but
                    // this is cheap insurance either way).
                }
            }
        }
        return 0;
    }

    /// <summary>
    /// Always winlogon.exe - matches rustdesk/rustdesk's own real call for
    /// this exact job (launching its screen-capture server process) exactly:
    /// LaunchProcessWin(cmd, session_id, /*as_user*/ FALSE, /*show*/ FALSE,
    /// ...) - as_user=FALSE means GetLogonPid always looks for winlogon.exe,
    /// never explorer.exe, regardless of login state. winlogon.exe exists in
    /// every session from the moment it's created, whether or not anyone is
    /// logged in. Returns that process's OWN token directly via
    /// OpenProcessToken - already a primary token (unlike
    /// WTSQueryUserToken's impersonation-type result), so no DuplicateTokenEx
    /// step is needed before CreateProcessAsUser. IntPtr.Zero if it couldn't
    /// be found or opened.
    /// </summary>
    private static IntPtr FindWinlogonToken(uint sessionId)
    {
        var pid = FindProcessIdInSession(sessionId, "winlogon");
        if (pid == 0) return IntPtr.Zero;

        var hProcess = OpenProcess(PROCESS_QUERY_INFORMATION, false, pid);
        if (hProcess == IntPtr.Zero) return IntPtr.Zero;
        try
        {
            return OpenProcessToken(hProcess, TOKEN_ALL_ACCESS, out var hToken) ? hToken : IntPtr.Zero;
        }
        finally
        {
            CloseHandle(hProcess);
        }
    }

    /// <summary>
    /// Launches <paramref name="commandLine"/> into the active console
    /// session - not this service's Session 0 - and returns the new
    /// process's id (so the caller can later terminate it via the normal
    /// System.Diagnostics.Process.GetProcessById/.Kill(), which works on any
    /// process this SYSTEM-context service has rights over regardless of who
    /// actually started it). Works whether or not anyone is logged in - see
    /// this class's own doc comment for the mechanism, including why lpDesktop
    /// is set to "winsta0\default" (the child needs the interactive desktop)
    /// while the environment block is left unset (matching rustdesk's own
    /// call).
    /// </summary>
    public static uint StartInActiveSession(string commandLine, string? workingDirectory, ILogger logger)
    {
        var sessionId = GetActiveConsoleSessionId();
        if (sessionId == INVALID_SESSION_ID)
        {
            throw new InvalidOperationException("No active console session found - is a display/console session even attached (e.g. the VM powered on)?");
        }

        // Prefer the SIGNED-IN USER's own token. Confirmed live that using
        // winlogon's SYSTEM token instead is why capture and resolution both
        // failed on a real VM: gdigrab read the screen geometry fine but
        // BitBlt returned "Failed to capture image (error 5)"
        // (ERROR_ACCESS_DENIED), and the helper's ChangeDisplaySettingsEx
        // returned -1 (DISP_CHANGE_FAILED). A SYSTEM process is not the owner
        // of the interactive desktop; the user's own token is, which is the
        // standard pattern for "a service launches something into the
        // interactive session". WTSQueryUserToken fails when nobody is signed
        // in - that's the sign-in-screen case, where winlogon's token IS the
        // right one, paired with the Winlogon desktop rather than Default.
        var hToken = IntPtr.Zero;
        var usingUserToken = false;
        var desktop = DesktopWinlogon;

        if (WTSQueryUserToken(sessionId, out var hUserToken))
        {
            try
            {
                if (DuplicateTokenEx(hUserToken, MAXIMUM_ALLOWED, IntPtr.Zero, SecurityImpersonation, TokenPrimary, out var hPrimary))
                {
                    hToken = hPrimary;
                    usingUserToken = true;
                    desktop = DesktopDefault;
                }
                else
                {
                    logger.LogWarning("DuplicateTokenEx on the session user's token failed (0x{Error:X}) - falling back to winlogon.exe's token.", Marshal.GetLastWin32Error());
                }
            }
            finally
            {
                CloseHandle(hUserToken);
            }
        }

        if (hToken == IntPtr.Zero)
        {
            hToken = FindWinlogonToken(sessionId);
            desktop = DesktopWinlogon;
        }

        if (hToken == IntPtr.Zero)
        {
            throw new InvalidOperationException(
                $"Could not obtain a token for session {sessionId} - neither the signed-in user's token nor " +
                $"winlogon.exe's (0x{Marshal.GetLastWin32Error():X}). Is SeDebugPrivilege enabled? (see EnsureDebugPrivilege).");
        }

        logger.LogInformation(
            "Launching into session {SessionId} on desktop {Desktop} using {TokenKind}.",
            sessionId, desktop, usingUserToken ? "the signed-in user's own token" : "winlogon.exe's token");

        // A user token needs that user's own environment block, or the child
        // inherits nothing usable (no TEMP/APPDATA/PATH for that account).
        // The winlogon/SYSTEM path deliberately passes NULL, matching what
        // rustdesk does for its own no-login launch.
        var environmentBlock = IntPtr.Zero;
        if (usingUserToken && !CreateEnvironmentBlock(out environmentBlock, hToken, false))
        {
            logger.LogWarning("CreateEnvironmentBlock failed (0x{Error:X}) - continuing with an inherited environment.", Marshal.GetLastWin32Error());
            environmentBlock = IntPtr.Zero;
        }

        try
        {
            // lpDesktop MUST be "winsta0\default" (the interactive desktop of
            // the active session). This is the fix for the whole "ffmpeg dies
            // before main(), no -report file" saga. Per the CreateProcessAsUser
            // docs, when lpDesktop is NULL the new process inherits the
            // PARENT's window station/desktop - and the parent here is this
            // Session-0 service, whose window station is the non-interactive
            // service station (Service-0x0-3e7$). The winlogon token puts the
            // process in the right session, but the inherited window station is
            // both non-interactive AND cross-session, so win32k/csrss can't
            // bind it and the process is torn down during initialization
            // (which is exactly why no -report was ever written: ffmpeg never
            // reached main()). gdigrab does a GetDC of the interactive desktop
            // and cannot recover from this. RustDesk's show=FALSE (no lpDesktop)
            // is safe for ITS launch only because its "--server" is its own
            // in-process capture engine, not a spawned gdigrab - copying that
            // flag for a spawned ffmpeg was the wrong lesson. winlogon's SYSTEM
            // token already has DACL access to winsta0\default, so no ACL edit
            // is needed. (Requires the CharSet.Unicode on STARTUPINFO above, or
            // this string is silently ANSI-corrupted.)
            var startupInfo = new STARTUPINFO
            {
                cb = Marshal.SizeOf<STARTUPINFO>(),
                lpDesktop = desktop,
            };

            var creationFlags = CREATE_NO_WINDOW;
            if (environmentBlock != IntPtr.Zero) creationFlags |= CREATE_UNICODE_ENVIRONMENT;

            if (!CreateProcessAsUser(hToken, null, commandLine, IntPtr.Zero, IntPtr.Zero, false,
                    (uint)creationFlags, environmentBlock, workingDirectory, ref startupInfo, out var processInfo))
            {
                throw new InvalidOperationException($"CreateProcessAsUser failed (0x{Marshal.GetLastWin32Error():X}).");
            }

            CloseHandle(processInfo.hThread);
            CloseHandle(processInfo.hProcess);
            return processInfo.dwProcessId;
        }
        finally
        {
            if (environmentBlock != IntPtr.Zero) DestroyEnvironmentBlock(environmentBlock);
            CloseHandle(hToken);
        }
    }
}
