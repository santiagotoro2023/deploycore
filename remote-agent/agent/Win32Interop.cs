using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Logging;
using Microsoft.Win32;

namespace DeployCoreAgent;

/// <summary>
/// Every raw Win32 P/Invoke declaration the agent needs lives here, in one
/// place, rather than scattered across the files that use them: SendInput
/// (mouse/keyboard injection for Shadow mode's data channel), SendSAS
/// (Ctrl+Alt+Del), the raw clipboard API, and the DPAPI wrapper AgentConfig
/// uses to protect <c>agentKey</c> at rest. None of this has been
/// compile-checked (no Windows/.NET toolchain in the environment this was
/// written in) - struct layouts and P/Invoke signatures below are the
/// standard, widely-documented Win32 shapes for each API, which is a much
/// safer bet than SIPSorcery's own API (see ShadowSession.cs), but still
/// worth a sanity pass on a real build.
/// </summary>
internal static class Win32Interop
{
    #region SendInput (mouse + keyboard)

    private const int InputMouse = 0;
    private const int InputKeyboard = 1;

    private const uint MouseEventFMove = 0x0001;
    private const uint MouseEventFAbsolute = 0x8000;
    private const uint MouseEventFLeftDown = 0x0002;
    private const uint MouseEventFLeftUp = 0x0004;
    private const uint MouseEventFRightDown = 0x0008;
    private const uint MouseEventFRightUp = 0x0010;
    private const uint MouseEventFMiddleDown = 0x0020;
    private const uint MouseEventFMiddleUp = 0x0040;
    private const uint MouseEventFWheel = 0x0800;

    private const uint KeyEventFKeyUp = 0x0002;

    [StructLayout(LayoutKind.Sequential)]
    private struct Input
    {
        public int Type;
        public InputUnion U;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public MouseInput Mi;
        [FieldOffset(0)] public KeybdInput Ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MouseInput
    {
        public int Dx;
        public int Dy;
        public uint MouseData;
        public uint DwFlags;
        public uint Time;
        public IntPtr DwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KeybdInput
    {
        public ushort WVk;
        public ushort WScan;
        public uint DwFlags;
        public uint Time;
        public IntPtr DwExtraInfo;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint nInputs, Input[] pInputs, int cbSize);

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int nIndex);

    private const int SmCxScreen = 0;
    private const int SmCyScreen = 1;

    /// <summary>
    /// The PRIMARY monitor's real pixel size (not the whole virtual desktop
    /// - v1 is single-default-monitor only, see the project README's scope
    /// cuts). ShadowSession uses this both as the native gdigrab capture
    /// size before any resize, and as the target space for
    /// <see cref="MoveMouseAbsolute"/>'s normalized coordinates.
    /// </summary>
    public static (int Width, int Height) GetPrimaryScreenSize() =>
        (GetSystemMetrics(SmCxScreen), GetSystemMetrics(SmCyScreen));

    /// <summary>
    /// normalizedX/Y must already be in SendInput's own 0..65535 absolute
    /// range (see ShadowSession.RescaleAndNormalize) - this method does no
    /// scaling of its own. Deliberately does NOT set MOUSEEVENTF_VIRTUALDESK
    /// - that would map the normalized range across every monitor, not just
    /// the primary one this build targets.
    /// </summary>
    public static void MoveMouseAbsolute(int normalizedX, int normalizedY)
    {
        var input = new Input
        {
            Type = InputMouse,
            U = new InputUnion
            {
                Mi = new MouseInput
                {
                    Dx = normalizedX,
                    Dy = normalizedY,
                    DwFlags = MouseEventFMove | MouseEventFAbsolute,
                },
            },
        };
        SendInput(1, new[] { input }, Marshal.SizeOf<Input>());
    }

    /// <summary>button: 0 = left, 1 = middle, 2 = right - matches DOM
    /// MouseEvent.button, which is what PROTOCOL.md's data-channel schema
    /// (mousedown/mouseup) is defined in terms of.</summary>
    public static void MouseButton(int button, bool down)
    {
        uint flags = button switch
        {
            0 => down ? MouseEventFLeftDown : MouseEventFLeftUp,
            1 => down ? MouseEventFMiddleDown : MouseEventFMiddleUp,
            2 => down ? MouseEventFRightDown : MouseEventFRightUp,
            _ => 0,
        };
        if (flags == 0) return;
        var input = new Input { Type = InputMouse, U = new InputUnion { Mi = new MouseInput { DwFlags = flags } } };
        SendInput(1, new[] { input }, Marshal.SizeOf<Input>());
    }

    /// <summary>
    /// dy follows DOM WheelEvent.deltaY sign (positive = scroll toward the
    /// user/down); Win32's wheel delta is the opposite convention (positive
    /// = away from the user, WHEEL_DELTA=120 per notch), hence the negation.
    /// The exact deltaY-per-notch scale the browser side sends isn't
    /// verified here (that code isn't part of this change) - if scrolling
    /// feels too fast/slow once tested end to end, adjust the scale factor
    /// here, not the sign.
    /// </summary>
    public static void MouseWheel(int dy)
    {
        var input = new Input
        {
            Type = InputMouse,
            U = new InputUnion { Mi = new MouseInput { DwFlags = MouseEventFWheel, MouseData = unchecked((uint)(-dy)) } },
        };
        SendInput(1, new[] { input }, Marshal.SizeOf<Input>());
    }

    /// <summary>
    /// code is <c>event.code</c> (DOM physical key, e.g. "KeyA", "Digit1",
    /// "ShiftLeft") per PROTOCOL.md - never <c>event.key</c>. Mapped below to
    /// a Windows virtual-key constant, NOT a hardware scancode.
    ///
    /// ponytail: this makes physical-key correctness depend on the TARGET
    /// machine's active keyboard layout matching what the operator expects -
    /// VK_Q always means "the Q key on a US QWERTY layout" to Windows,
    /// re-interpreted through whatever layout is active on that machine.
    /// Genuine physical-position independence (what PROTOCOL.md's "never
    /// event.key" phrasing is really asking for) needs KEYEVENTF_SCANCODE
    /// with a full DOM-code -> PS/2 Set-1 scancode table, including the
    /// E0-prefixed extended keys - mechanical but tedious, and too easy to
    /// get subtly wrong from memory with no way to compile/hardware-test it
    /// here. VK mapping is correct today for the overwhelmingly common case
    /// (both ends on a US layout) and the table below covers the everyday
    /// keyset. Upgrade path: replace this table with a scancode table and
    /// set KEYEVENTF_SCANCODE, if a target machine ever runs a non-US
    /// layout and this becomes visible as wrong keys landing.
    /// </summary>
    public static void KeyEvent(string code, bool down)
    {
        if (!VirtualKeyMap.TryGetValue(code, out var vk)) return;
        var input = new Input
        {
            Type = InputKeyboard,
            U = new InputUnion { Ki = new KeybdInput { WVk = vk, DwFlags = down ? 0u : KeyEventFKeyUp } },
        };
        SendInput(1, new[] { input }, Marshal.SizeOf<Input>());
    }

    private static readonly Dictionary<string, ushort> VirtualKeyMap = BuildVirtualKeyMap();

    private static Dictionary<string, ushort> BuildVirtualKeyMap()
    {
        var map = new Dictionary<string, ushort>();
        for (var c = '0'; c <= '9'; c++) map[$"Digit{c}"] = c; // VK_0..VK_9 == ASCII '0'..'9'
        for (var c = 'A'; c <= 'Z'; c++) map[$"Key{c}"] = c; // VK_A..VK_Z == ASCII 'A'..'Z'
        for (var i = 0; i <= 9; i++) map[$"Numpad{i}"] = (ushort)(0x60 + i); // VK_NUMPAD0..VK_NUMPAD9
        for (var i = 1; i <= 12; i++) map[$"F{i}"] = (ushort)(0x70 + i - 1); // VK_F1..VK_F12

        map["ControlLeft"] = 0xA2; map["ControlRight"] = 0xA3; // VK_LCONTROL / VK_RCONTROL
        map["ShiftLeft"] = 0xA0; map["ShiftRight"] = 0xA1; // VK_LSHIFT / VK_RSHIFT
        map["AltLeft"] = 0xA4; map["AltRight"] = 0xA5; // VK_LMENU / VK_RMENU
        map["MetaLeft"] = 0x5B; map["MetaRight"] = 0x5C; // VK_LWIN / VK_RWIN
        map["OSLeft"] = 0x5B; map["OSRight"] = 0x5C; // some browsers report OS* instead of Meta*

        map["Enter"] = 0x0D; map["NumpadEnter"] = 0x0D; // VK_RETURN - indistinguishable in VK terms, see doc-comment above
        map["Escape"] = 0x1B; map["Backspace"] = 0x08; map["Tab"] = 0x09; map["Space"] = 0x20;
        map["Delete"] = 0x2E; map["Insert"] = 0x2D;
        map["Home"] = 0x24; map["End"] = 0x23; map["PageUp"] = 0x21; map["PageDown"] = 0x22;
        map["ArrowUp"] = 0x26; map["ArrowDown"] = 0x28; map["ArrowLeft"] = 0x25; map["ArrowRight"] = 0x27;
        map["CapsLock"] = 0x14; map["NumLock"] = 0x90; map["ScrollLock"] = 0x91;
        map["PrintScreen"] = 0x2C; map["Pause"] = 0x13; map["ContextMenu"] = 0x5D;

        map["Minus"] = 0xBD; map["Equal"] = 0xBB; // VK_OEM_MINUS / VK_OEM_PLUS
        map["BracketLeft"] = 0xDB; map["BracketRight"] = 0xDD; map["Backslash"] = 0xDC; // VK_OEM_4 / _6 / _5
        map["Semicolon"] = 0xBA; map["Quote"] = 0xDE; // VK_OEM_1 / VK_OEM_7
        map["Comma"] = 0xBC; map["Period"] = 0xBE; map["Slash"] = 0xBF; map["Backquote"] = 0xC0; // VK_OEM_COMMA/PERIOD/_2/_3

        map["NumpadMultiply"] = 0x6A; map["NumpadAdd"] = 0x6B;
        map["NumpadSubtract"] = 0x6D; map["NumpadDecimal"] = 0x6E; map["NumpadDivide"] = 0x6F;

        return map;
    }

    #endregion

    #region Display mode switching (ChangeDisplaySettingsEx - no driver needed)

    // Real, working resolution changes today, with NO virtual-display driver
    // installed - this is the standard Win32 mechanism every non-driver-based
    // remote-desktop tool uses (it's also what the OLD RustDesk-based version
    // of this agent did, and what a real IDD driver's exact-arbitrary sizing
    // - see IVirtualDisplay - is the upgrade FROM, not a replacement for).
    // The one real constraint (unchanged from the RustDesk-based version,
    // documented in this repo's own commit history fixing it there): this
    // API only accepts a mode the adapter already publishes via
    // EnumDisplaySettings, never an arbitrary exact pixel size - so this
    // finds the closest one rather than assuming the requested size exists.

    // Deliberately 0 (dynamic-only), not CDS_UPDATEREGISTRY - this is a
    // transient change driven by whatever size the operator's browser
    // window happens to be, not a new permanent default for the machine.
    // CDS_UPDATEREGISTRY would leave the VM's own registry-configured
    // resolution changed after the Shadow session ends, which nothing
    // about this feature should do.
    private const int CdsDynamicOnly = 0x00;
    private const int DisplayChangeSuccessful = 0;
    private const int DmPelsWidth = 0x00080000;
    private const int DmPelsHeight = 0x00100000;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct DevMode
    {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DmDeviceName;
        public short DmSpecVersion;
        public short DmDriverVersion;
        public short DmSize;
        public short DmDriverExtra;
        public int DmFields;
        public int DmPositionX;
        public int DmPositionY;
        public int DmDisplayOrientation;
        public int DmDisplayFixedOutput;
        public short DmColor;
        public short DmDuplex;
        public short DmYResolution;
        public short DmTTOption;
        public short DmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DmFormName;
        public short DmLogPixels;
        public int DmBitsPerPel;
        public int DmPelsWidth;
        public int DmPelsHeight;
        public int DmDisplayFlags;
        public int DmDisplayFrequency;
        public int DmICMMethod;
        public int DmICMIntent;
        public int DmMediaType;
        public int DmDitherType;
        public int DmReserved1;
        public int DmReserved2;
        public int DmPanningWidth;
        public int DmPanningHeight;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool EnumDisplaySettings(string? deviceName, int modeNum, ref DevMode devMode);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int ChangeDisplaySettingsEx(string? deviceName, ref DevMode devMode, IntPtr hwnd, int dwFlags, IntPtr lParam);

    /// <summary>
    /// Every mode the primary adapter publishes, deduplicated by
    /// (width, height) - a given resolution commonly appears several times
    /// over (once per supported refresh rate/color depth), and only the
    /// pixel size matters for picking the nearest match.
    /// </summary>
    private static List<(int Width, int Height)> EnumerateSupportedResolutions()
    {
        var seen = new HashSet<(int, int)>();
        var mode = new DevMode { DmSize = (short)Marshal.SizeOf<DevMode>() };
        for (var i = 0; EnumDisplaySettings(null, i, ref mode); i++)
        {
            seen.Add((mode.DmPelsWidth, mode.DmPelsHeight));
            mode = new DevMode { DmSize = (short)Marshal.SizeOf<DevMode>() };
        }
        return seen.ToList();
    }

    /// <summary>
    /// Picks the adapter-supported mode closest to the requested size -
    /// aspect-ratio-weighted the same way the old RustDesk-era frontend's
    /// (now-deleted) client-side nearestResolution heuristic was, except
    /// against the REAL modes this specific machine's adapter actually
    /// supports rather than a hardcoded guess list, and never larger than
    /// requested in either dimension (matches the same "never request bigger
    /// than the viewport" rule).
    /// </summary>
    private static (int Width, int Height)? FindNearestResolution(int targetWidth, int targetHeight)
    {
        var candidates = EnumerateSupportedResolutions();
        if (candidates.Count == 0) return null;

        var targetAspect = (double)targetWidth / targetHeight;
        (int Width, int Height)? best = null;
        var bestScore = double.MaxValue;
        foreach (var (w, h) in candidates)
        {
            if (w > targetWidth || h > targetHeight) continue;
            var aspectDiff = Math.Abs((double)w / h - targetAspect);
            var areaDiff = (double)(targetWidth * targetHeight - w * h) / (targetWidth * targetHeight);
            var score = aspectDiff * 5 + areaDiff;
            if (score < bestScore) { bestScore = score; best = (w, h); }
        }
        // Nothing fit under the target (a very small requested viewport) -
        // fall back to the smallest available mode rather than refusing to
        // change resolution at all.
        return best ?? candidates.OrderBy(c => c.Width * c.Height).FirstOrDefault();
    }

    /// <summary>
    /// Applies the nearest adapter-supported mode to (width, height) and
    /// returns the ACTUAL resulting resolution (which ShadowSession needs to
    /// keep mouse-coordinate normalization correct) - null if no mode could
    /// be applied (e.g. EnumDisplaySettings returned nothing, or the change
    /// itself failed), in which case the caller should keep using whatever
    /// resolution was already active.
    /// </summary>
    public static (int Width, int Height)? TrySetNearestResolution(int targetWidth, int targetHeight, ILogger logger)
    {
        var nearest = FindNearestResolution(targetWidth, targetHeight);
        if (nearest is null)
        {
            logger.LogWarning("No display modes enumerated - cannot change resolution.");
            return null;
        }

        var (width, height) = nearest.Value;
        var current = GetPrimaryScreenSize();
        if (current == (width, height))
            return current; // already there - ChangeDisplaySettingsEx is a real mode switch, not a free no-op to repeat

        var mode = new DevMode
        {
            DmSize = (short)Marshal.SizeOf<DevMode>(),
            DmPelsWidth = width,
            DmPelsHeight = height,
            DmFields = DmPelsWidth | DmPelsHeight,
        };
        var result = ChangeDisplaySettingsEx(null, ref mode, IntPtr.Zero, CdsDynamicOnly, IntPtr.Zero);
        if (result != DisplayChangeSuccessful)
        {
            logger.LogWarning("ChangeDisplaySettingsEx to {Width}x{Height} failed with code {Result}.", width, height, result);
            return null;
        }

        logger.LogInformation("Changed resolution to {Width}x{Height} (nearest supported match for requested {TargetWidth}x{TargetHeight}).",
            width, height, targetWidth, targetHeight);
        return (width, height);
    }

    #endregion

    #region SendSAS (Ctrl+Alt+Del)

    [DllImport("sas.dll")]
    private static extern void SendSAS(bool asUser);

    private const string SasPolicyKeyPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System";
    private const string SasPolicyValueName = "SoftwareSASGeneration";
    private const int SasPolicyAllowServices = 1; // 1 = services may call SendSAS (2 = Ease of Access apps, 3 = both)

    /// <summary>
    /// Called once at agent startup (see Program.cs), not per-session - this
    /// is a machine-wide policy value, not session state. Sets
    /// SoftwareSASGeneration to 1 ("services") only if it's missing or
    /// explicitly 0, logging what it did rather than assuming a freshly
    /// provisioned VM already has this configured, per this project's own
    /// build instructions.
    ///
    /// Not independently verified on real hardware: MSDN documents SendSAS
    /// as requiring the caller to run as LocalSystem (true here -
    /// New-Service with no -Credential defaults to LocalSystem) and this
    /// policy to permit non-WinLogon callers. It's also assumed here that
    /// CSRSS/WinLogon reads this value live rather than only at boot, which
    /// matches every public description found of it, but hasn't been
    /// exercised end to end.
    /// </summary>
    public static void EnsureSoftwareSasGeneration(ILogger logger)
    {
        try
        {
            using var key = Registry.LocalMachine.OpenSubKey(SasPolicyKeyPath, writable: true)
                             ?? Registry.LocalMachine.CreateSubKey(SasPolicyKeyPath, writable: true);
            var current = key.GetValue(SasPolicyValueName) as int?;
            if (current is { } existing && existing != 0)
            {
                logger.LogInformation("SoftwareSASGeneration is already {Value} - leaving it as-is.", existing);
                return;
            }

            key.SetValue(SasPolicyValueName, SasPolicyAllowServices, RegistryValueKind.DWord);
            logger.LogInformation(
                "SoftwareSASGeneration was {Previous} - set it to {NewValue} so SendSAS (Ctrl+Alt+Del) works from this service.",
                current?.ToString() ?? "unset", SasPolicyAllowServices);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Could not read/set SoftwareSASGeneration - Ctrl+Alt+Del via SendSAS may not work.");
        }
    }

    public static void SendSecureAttentionSequence(ILogger logger)
    {
        try
        {
            SendSAS(false);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "SendSAS failed.");
        }
    }

    #endregion

    #region Raw clipboard API (plain text only)

    // A Windows Service has no interactive desktop session and no STA
    // message pump by default, so System.Windows.Forms.Clipboard (which
    // wants both) is a poor fit here - the raw OpenClipboard/GetClipboardData/
    // SetClipboardData Win32 calls need neither, and using them avoids
    // pulling a WinForms dependency into the whole project for one feature.
    // This is the shortcut PROTOCOL.md/the build instructions explicitly
    // anticipated and asked to be called out - so: called out here.

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool OpenClipboard(IntPtr hWndNewOwner);

    [DllImport("user32.dll")]
    private static extern bool CloseClipboard();

    [DllImport("user32.dll")]
    private static extern bool EmptyClipboard();

    [DllImport("user32.dll")]
    private static extern IntPtr GetClipboardData(uint uFormat);

    [DllImport("user32.dll")]
    private static extern IntPtr SetClipboardData(uint uFormat, IntPtr hMem);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GlobalAlloc(uint uFlags, UIntPtr dwBytes);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GlobalLock(IntPtr hMem);

    [DllImport("kernel32.dll")]
    private static extern bool GlobalUnlock(IntPtr hMem);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GlobalFree(IntPtr hMem);

    private const uint CfUnicodeText = 13;
    private const uint GMemMoveable = 0x0002;

    public static void SetClipboardText(string text)
    {
        if (!TryOpenClipboard()) return;
        try
        {
            EmptyClipboard();
            var bytes = Encoding.Unicode.GetBytes(text + "\0");
            var hGlobal = GlobalAlloc(GMemMoveable, (UIntPtr)bytes.Length);
            if (hGlobal == IntPtr.Zero) return;

            var target = GlobalLock(hGlobal);
            if (target == IntPtr.Zero)
            {
                GlobalFree(hGlobal);
                return;
            }
            Marshal.Copy(bytes, 0, target, bytes.Length);
            GlobalUnlock(hGlobal);

            // Ownership of hGlobal transfers to the clipboard on success -
            // must NOT GlobalFree it ourselves in that case. On failure it's
            // still ours to free.
            if (SetClipboardData(CfUnicodeText, hGlobal) == IntPtr.Zero)
                GlobalFree(hGlobal);
        }
        finally
        {
            CloseClipboard();
        }
    }

    public static string? GetClipboardText()
    {
        if (!TryOpenClipboard()) return null;
        try
        {
            var handle = GetClipboardData(CfUnicodeText);
            if (handle == IntPtr.Zero) return null;

            var pointer = GlobalLock(handle);
            if (pointer == IntPtr.Zero) return null;
            try
            {
                return Marshal.PtrToStringUni(pointer);
            }
            finally
            {
                GlobalUnlock(handle);
            }
        }
        finally
        {
            CloseClipboard();
        }
    }

    // OpenClipboard commonly fails transiently ("access denied") if another
    // process holds it open at that exact instant - a short bounded retry is
    // standard practice for this specific API, not a general retry
    // framework: 5 attempts, 20ms apart, ~100ms worst case.
    private static bool TryOpenClipboard()
    {
        for (var attempt = 0; attempt < 5; attempt++)
        {
            if (OpenClipboard(IntPtr.Zero)) return true;
            Thread.Sleep(20);
        }
        return false;
    }

    #endregion

    #region DPAPI (agent-config.json's agentKey at rest)

    /// <summary>
    /// ProtectedData.Protect/Unprotect (System.Security.Cryptography.ProtectedData
    /// NuGet package - DPAPI moved out of the BCL proper starting with .NET
    /// Core). LocalMachine scope because this runs as a Windows Service under
    /// SYSTEM with no interactive user profile to scope a CurrentUser DPAPI
    /// key to. See AgentConfig.LoadAndProtect for why this matters: the
    /// installer's icacls ACL is the first line of defense, DPAPI is the
    /// second one that survives even if that ACL is ever weakened later.
    /// </summary>
    public static string ProtectToBase64(string plaintext)
    {
        var bytes = Encoding.UTF8.GetBytes(plaintext);
        var protectedBytes = ProtectedData.Protect(bytes, null, DataProtectionScope.LocalMachine);
        return Convert.ToBase64String(protectedBytes);
    }

    public static string UnprotectFromBase64(string protectedBase64)
    {
        var protectedBytes = Convert.FromBase64String(protectedBase64);
        var bytes = ProtectedData.Unprotect(protectedBytes, null, DataProtectionScope.LocalMachine);
        return Encoding.UTF8.GetString(bytes);
    }

    #endregion
}
