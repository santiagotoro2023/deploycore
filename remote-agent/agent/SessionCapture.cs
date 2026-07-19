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
/// SECOND REVISION, after the first fix's own fallback path turned out not
/// to work on real hardware: the original "nobody logged in" fallback
/// duplicated THIS SERVICE's own SYSTEM token and retargeted its
/// TokenSessionId field via SetTokenInformation. That compiled, ran, and
/// returned no error from CreateProcessAsUser - but ffmpeg's capture file
/// never appeared (confirmed live via agent.log: "capture file never
/// appeared after 10s" on every attempt) - a hand-constructed token that
/// merely claims to belong to another session, with no real logon tied to
/// it, apparently doesn't carry whatever the interactive window
/// station/desktop actually checks for, even though the API calls
/// themselves all report success.
///
/// Replaced with the mechanism a real, shipping remote-desktop product
/// actually uses for exactly this (rustdesk/rustdesk's own
/// src/platform/windows.cc, GetSessionUserTokenWin/LaunchProcessWin -
/// fetched and reviewed directly, not reconstructed from memory, matching
/// this project's own rule against guessing at Win32 internals a second
/// time): find a process that is ALREADY genuinely, natively running IN the
/// target session - explorer.exe if a user is logged in, or winlogon.exe
/// otherwise (winlogon owns and renders the logon/lock screen, and exists
/// in every session from the moment it's created, regardless of login
/// state) - and steal ITS OWN process token directly via OpenProcessToken.
/// That token is already legitimately, natively scoped to that session (it
/// belongs to a process the OS itself put there), unlike a token merely
/// patched to claim a session id. It's also already a PRIMARY token (a
/// process's own token, unlike WTSQueryUserToken's impersonation-type
/// result), so no DuplicateTokenEx step is needed before handing it to
/// CreateProcessAsUser - rustdesk's own code doesn't do one either.
///
/// lpDesktop is unconditionally "winsta0\default" - confirmed against that
/// same reference, which uses this even for the winlogon-token/no-login
/// case, not a separate "winsta0\winlogon" target as the previous revision
/// here guessed.
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

    private const int CREATE_UNICODE_ENVIRONMENT = 0x00000400;
    private const int CREATE_NO_WINDOW = 0x08000000;
    private const uint INVALID_SESSION_ID = 0xFFFFFFFF;
    private static readonly IntPtr WTS_CURRENT_SERVER_HANDLE = IntPtr.Zero;

    private const uint TOKEN_ADJUST_PRIVILEGES = 0x0020;
    private const uint TOKEN_QUERY = 0x0008;
    private const uint SE_PRIVILEGE_ENABLED = 0x00000002;

    // Matches rustdesk/rustdesk's own GetSessionUserTokenWin exactly
    // (src/platform/windows.cc: OpenProcessToken(hProcess, TOKEN_ALL_ACCESS,
    // ...)) rather than hand-picking a narrower access mask - this project
    // already spent one real test cycle on a hand-constructed, unproven
    // token mechanism that compiled fine and reported no error but never
    // actually produced a working capture; not repeating that here.
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

    [StructLayout(LayoutKind.Sequential)]
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

    [DllImport("userenv.dll", SetLastError = true)]
    private static extern bool CreateEnvironmentBlock(ref IntPtr lpEnvironment, IntPtr hToken, bool bInherit);

    [DllImport("userenv.dll", SetLastError = true)]
    private static extern bool DestroyEnvironmentBlock(IntPtr lpEnvironment);

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
    /// Needed to steal winlogon.exe's/explorer.exe's own token via
    /// OpenProcessToken below - held by LocalSystem's token (which this
    /// service runs as - New-Service with no -Credential in
    /// remote_agent_install.ps1 defaults to LocalSystem) but not necessarily
    /// ENABLED by default. Called once at agent startup (see Program.cs),
    /// not per-session.
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
    /// explorer.exe if someone's logged into this session (preferred - a
    /// real logged-in user's own token), else winlogon.exe (nobody has
    /// logged in - this is the process rendering the logon/lock screen
    /// itself, and it exists in every session from the moment it's created,
    /// regardless of login state). Returns that process's OWN token
    /// directly via OpenProcessToken - see this class's own doc comment for
    /// why this, and not a manufactured/retargeted token, is what actually
    /// works. IntPtr.Zero if neither process could be found or opened.
    /// </summary>
    private static IntPtr FindSessionToken(uint sessionId, out bool foundLoggedInUser)
    {
        foundLoggedInUser = true;
        var pid = FindProcessIdInSession(sessionId, "explorer");
        if (pid == 0)
        {
            foundLoggedInUser = false;
            pid = FindProcessIdInSession(sessionId, "winlogon");
        }
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
    /// session's own desktop - not this service's Session 0 - and returns
    /// the new process's id (so the caller can later terminate it via the
    /// normal System.Diagnostics.Process.GetProcessById/.Kill(), which works
    /// on any process this SYSTEM-context service has rights over regardless
    /// of who actually started it). Works whether or not anyone is logged
    /// in - see this class's own doc comment for the mechanism.
    /// </summary>
    public static uint StartInActiveSession(string commandLine, string? workingDirectory, ILogger logger)
    {
        var sessionId = GetActiveConsoleSessionId();
        if (sessionId == INVALID_SESSION_ID)
        {
            throw new InvalidOperationException("No active console session found - is a display/console session even attached (e.g. the VM powered on)?");
        }

        var hToken = FindSessionToken(sessionId, out var foundLoggedInUser);
        if (hToken == IntPtr.Zero)
        {
            throw new InvalidOperationException(
                $"Could not find explorer.exe or winlogon.exe running in session {sessionId}, or could not open its token " +
                $"(0x{Marshal.GetLastWin32Error():X}) - is SeDebugPrivilege enabled? (see EnsureDebugPrivilege).");
        }

        logger.LogInformation(
            foundLoggedInUser
                ? "Launching into session {SessionId} using explorer.exe's own token (a user is logged in)."
                : "Launching into session {SessionId} using winlogon.exe's own token (nobody has logged in yet).",
            sessionId);

        var pEnv = IntPtr.Zero;
        try
        {
            // Only build a custom environment block for a REAL logged-in
            // user's own token (explorer.exe) - confirmed against
            // rustdesk/rustdesk's own LaunchProcessWin, which does exactly
            // this and nothing else: for the winlogon.exe/no-login case, it
            // leaves lpEnvironment NULL rather than calling
            // CreateEnvironmentBlock at all. This was NOT matched here
            // originally (this code called CreateEnvironmentBlock
            // unconditionally for both cases) - confirmed live as the
            // likely real cause of ffmpeg dying near-instantly with no
            // -report file ever written (crashing before even reaching its
            // own main(), the signature of a broken startup environment):
            // winlogon.exe is not a normal interactively-logged-on user
            // with a loaded profile, so CreateEnvironmentBlock building an
            // environment "from its profile" plausibly produces something
            // missing basics like PATH/SystemRoot that any child process's
            // own CRT init needs. Passing NULL instead makes the new
            // process inherit THIS SERVICE's own environment (a normal,
            // complete SYSTEM environment, since this service starts
            // normally under the SCM) - matches rustdesk's proven behavior
            // exactly rather than trying to fix CreateEnvironmentBlock's
            // input instead.
            if (foundLoggedInUser)
            {
                if (!CreateEnvironmentBlock(ref pEnv, hToken, true))
                {
                    throw new InvalidOperationException($"CreateEnvironmentBlock failed (0x{Marshal.GetLastWin32Error():X}).");
                }
            }

            var startupInfo = new STARTUPINFO
            {
                cb = Marshal.SizeOf<STARTUPINFO>(),
                lpDesktop = "winsta0\\default",
            };
            // CREATE_UNICODE_ENVIRONMENT only makes sense (and is only set
            // by rustdesk's own reference) when an actual Unicode
            // environment block is being passed - meaningless, and not
            // worth risking undefined behavior over, when pEnv is NULL.
            var creationFlags = CREATE_NO_WINDOW | (pEnv != IntPtr.Zero ? CREATE_UNICODE_ENVIRONMENT : 0);

            if (!CreateProcessAsUser(hToken, null, commandLine, IntPtr.Zero, IntPtr.Zero, false,
                    (uint)creationFlags, pEnv, workingDirectory, ref startupInfo, out var processInfo))
            {
                throw new InvalidOperationException($"CreateProcessAsUser failed (0x{Marshal.GetLastWin32Error():X}).");
            }

            CloseHandle(processInfo.hThread);
            CloseHandle(processInfo.hProcess);
            return processInfo.dwProcessId;
        }
        finally
        {
            if (pEnv != IntPtr.Zero) DestroyEnvironmentBlock(pEnv);
            CloseHandle(hToken);
        }
    }
}
