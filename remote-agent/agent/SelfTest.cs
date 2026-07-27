using System.Diagnostics;
using System.Text;
using System.Text.Json;
using SIPSorcery.Net;

namespace DeployCoreAgent;

/// <summary>
/// "DeployCoreAgent.exe --selftest" - a runtime self-check that exercises the
/// parts of the agent that can't be validated by a plain compile: the
/// SIPSorcery WebRTC calls (written against the library's documented API, so
/// worth proving they actually construct an offer at runtime), the Annex-B
/// NAL splitter, the bundled ffmpeg binary, and - end to end - the new
/// in-session helper's pipe protocol (launch the child, round-trip a JSON
/// command, read its reply). Runs on a windows-latest CI runner (see
/// build-agent-msi.yml) so these are caught before a real VM deployment.
///
/// Exit code 0 iff every CRITICAL check passes. Best-effort checks (things
/// that need a real interactive desktop, which a CI runner may not fully
/// provide - a live gdigrab capture, a clipboard round-trip) only WARN, since
/// their real validation is the user's own VM; they never fail the run.
/// </summary>
internal static class SelfTest
{
    private static int _failures;

    public static async Task<int> RunAsync()
    {
        Console.WriteLine("=== DeployCore Agent self-test ===");

        Critical("Win32 basics", CheckWin32Basics);
        Critical("Annex-B NAL splitter", CheckNalSplitter);
        Critical("SIPSorcery WebRTC offer", CheckWebRtcOffer);
        Critical("ffmpeg binary present + runnable", CheckFfmpegVersion);
        await CriticalAsync("session-helper pipe round-trip", CheckHelperPipeRoundTripAsync);

        BestEffort("ffmpeg gdigrab capture", CheckGdigrabCapture);
        BestEffort("clipboard round-trip", CheckClipboardRoundTrip);

        Console.WriteLine(_failures == 0
            ? "=== self-test PASSED ==="
            : $"=== self-test FAILED ({_failures} critical check(s)) ===");
        return _failures == 0 ? 0 : 1;
    }

    // --- checks ---

    private static void CheckWin32Basics()
    {
        var (w, h) = Win32Interop.GetPrimaryScreenSize();
        if (w <= 0 || h <= 0) throw new Exception($"GetPrimaryScreenSize returned {w}x{h}");
        // Exercising a key event must not throw (it won't land anywhere useful
        // here, but a bad P/Invoke signature would throw).
        Win32Interop.KeyEvent("KeyA", down: true);
        Win32Interop.KeyEvent("KeyA", down: false);
        Console.WriteLine($"    primary screen {w}x{h}");
    }

    private static void CheckNalSplitter()
    {
        // 4-byte start code, NAL #1 = {0x67}, 3-byte start code, NAL #2 =
        // {0x65,0xAA,0xBB}. The splitter emits NAL #1 once it sees the SECOND
        // start code; NAL #2 stays buffered until more data / EOF, matching how
        // the real tail loop works. One complete NAL is enough to prove the
        // split logic.
        var stream = new byte[] { 0, 0, 0, 1, 0x67, 0, 0, 1, 0x65, 0xAA, 0xBB };
        var splitter = new AnnexBNalSplitter();
        var nals = splitter.Append(stream).ToList();
        if (nals.Count < 1) throw new Exception("no NAL units split out");
        if (nals[0].Length != 1 || nals[0][0] != 0x67)
            throw new Exception($"first NAL wrong (len {nals[0].Length}, byte0 0x{nals[0][0]:X2}, expected len 1 / 0x67)");
        Console.WriteLine($"    split {nals.Count} NAL unit(s), first is SPS (0x67)");
    }

    private static void CheckWebRtcOffer()
    {
        // Mirrors ShadowSession.CreatePeerConnection/StartAsync exactly, so if
        // any SIPSorcery method name/overload is wrong this fails here rather
        // than on a live session.
        var pc = new RTCPeerConnection(new RTCConfiguration());
        var videoFormat = new SDPAudioVideoMediaFormat(SDPMediaTypesEnum.video, 96, "H264", 90000,
            fmtp: "packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1");
        var track = new MediaStreamTrack(SDPMediaTypesEnum.video, false,
            new List<SDPAudioVideoMediaFormat> { videoFormat }, MediaStreamStatusEnum.SendOnly);
        pc.addTrack(track);
        var offer = pc.createOffer(null);
        if (offer?.sdp is null) throw new Exception("createOffer returned no SDP");
        if (!offer.sdp.Contains("m=video")) throw new Exception("offer SDP has no video media line");
        if (!offer.sdp.ToUpperInvariant().Contains("H264")) throw new Exception("offer SDP does not advertise H264");
        pc.close();
        Console.WriteLine("    RTCPeerConnection produced a valid H264 video offer");
    }

    private static void CheckFfmpegVersion()
    {
        var (code, stdout, _, timedOut) = RunProcess(FfmpegPath(), "-hide_banner -version", 15000);
        if (timedOut) throw new Exception("ffmpeg -version timed out");
        if (!stdout.Contains("ffmpeg version")) throw new Exception($"unexpected ffmpeg -version output (exit {code})");
        Console.WriteLine("    " + stdout.Split('\n')[0].Trim());
    }

    private static async Task CheckHelperPipeRoundTripAsync()
    {
        var pipeName = "DeployCoreAgentSelfTest-" + Guid.NewGuid().ToString("N");
        using var server = new System.IO.Pipes.NamedPipeServerStream(
            pipeName, System.IO.Pipes.PipeDirection.InOut, 1,
            System.IO.Pipes.PipeTransmissionMode.Byte, System.IO.Pipes.PipeOptions.Asynchronous);

        var exe = Path.Combine(AppContext.BaseDirectory, "DeployCoreAgent.exe");
        using var child = Process.Start(new ProcessStartInfo(exe, $"--session-helper {pipeName}") { UseShellExecute = false })
                          ?? throw new Exception("could not launch --session-helper child");
        try
        {
            // Task.WaitAsync bounds the WHOLE round-trip hard: unlike a
            // CancellationToken (which a named-pipe WaitForConnection/ReadLine
            // may not actually honor - that's what hung an earlier run), this
            // abandons the wait after the timeout no matter what, throwing
            // TimeoutException, so this check can never stall the self-test.
            await DoHelperRoundTripAsync(server).WaitAsync(TimeSpan.FromSeconds(25));
        }
        finally
        {
            try { server.Dispose(); } catch { /* ignore */ }
            try { if (!child.WaitForExit(3000)) child.Kill(entireProcessTree: true); } catch { /* ignore */ }
            DumpAgentLog(); // the helper's own log - shows its side of the round-trip
        }
    }

    private static async Task DoHelperRoundTripAsync(System.IO.Pipes.NamedPipeServerStream server)
    {
        await server.WaitForConnectionAsync();
        Console.WriteLine("    helper connected to the pipe");

        var writer = new StreamWriter(server, new UTF8Encoding(false)) { AutoFlush = true };
        var reader = new StreamReader(server, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false);

        // Read on a SEPARATE task, concurrently with writing. A named-pipe
        // Flush blocks until the peer reads, so if both ends write-then-flush
        // before reading, they deadlock - which is exactly what an earlier run
        // hit here (both "connected" but neither's write completed). Production
        // avoids this because ShadowSession reads immediately on connect; the
        // test must do the same. The helper sends a "screensize" on connect and
        // again after a "resize"; either one satisfies the check.
        var readTask = Task.Run(async () =>
        {
            for (var i = 0; i < 8; i++)
            {
                var line = await reader.ReadLineAsync();
                Console.WriteLine($"    read[{i}]: {(line is null ? "<null/EOF>" : line)}");
                if (line is null) return (string?)null;
                try
                {
                    var msg = JsonDocument.Parse(line).RootElement;
                    if (msg.TryGetProperty("t", out var t) && t.GetString() == "screensize"
                        && msg.TryGetProperty("w", out var w) && w.GetInt32() > 0)
                        return line;
                }
                catch (JsonException) { /* ignore non-JSON */ }
            }
            return (string?)null;
        });

        await Task.Delay(250); // let the reader drain the helper's initial screensize first
        try
        {
            await writer.WriteLineAsync("{\"t\":\"resize\",\"w\":800,\"h\":600}");
            Console.WriteLine("    sent resize to helper");
        }
        catch (Exception ex) { Console.WriteLine("    write to helper failed: " + ex.Message); }

        var gotScreensize = await readTask;
        if (gotScreensize is null) throw new Exception("no valid 'screensize' reply from the helper");
        Console.WriteLine($"    helper replied: {gotScreensize}");
    }

    private static void DumpAgentLog()
    {
        try
        {
            var log = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "DeployCore", "agent.log");
            if (!File.Exists(log)) { Console.WriteLine("    (no helper agent.log found)"); return; }
            var text = File.ReadAllText(log);
            Console.WriteLine("    --- helper agent.log (tail) ---");
            foreach (var l in (text.Length > 1500 ? text[^1500..] : text).Split('\n'))
                if (l.Trim().Length > 0) Console.WriteLine("    | " + l.TrimEnd());
            Console.WriteLine("    --- end agent.log ---");
        }
        catch (Exception ex) { Console.WriteLine("    (agent.log dump failed: " + ex.Message + ")"); }
    }

    private static void CheckGdigrabCapture()
    {
        var outPath = Path.Combine(Path.GetTempPath(), "deploycore-selftest-" + Guid.NewGuid().ToString("N") + ".h264");
        var args = $"-hide_banner -f gdigrab -framerate 10 -t 1 -i desktop -c:v libx264 -preset ultrafast -pix_fmt yuv420p -f h264 -y \"{outPath}\"";
        try
        {
            var (_, _, stderr, timedOut) = RunProcess(FfmpegPath(), args, 20000);
            var bytes = File.Exists(outPath) ? new FileInfo(outPath).Length : 0;
            if (timedOut) throw new Exception("gdigrab timed out (a CI runner may have no capturable desktop)");
            if (bytes <= 0)
                throw new Exception($"gdigrab produced no output. ffmpeg tail: {Tail(stderr)}");
            Console.WriteLine($"    gdigrab captured {bytes} bytes of H264");
        }
        finally
        {
            try { File.Delete(outPath); } catch { /* ignore */ }
        }
    }

    /// <summary>
    /// Runs a child process with BOTH stdout/stderr drained asynchronously
    /// (so a full pipe buffer can never deadlock it) and a hard timeout+kill.
    /// A plain ReadToEnd() has no timeout and blocks forever if the child
    /// never exits - which is exactly how gdigrab hung the whole self-test on
    /// a desktopless CI runner.
    /// </summary>
    private static (int exitCode, string stdout, string stderr, bool timedOut) RunProcess(string exe, string args, int timeoutMs)
    {
        var psi = new ProcessStartInfo(exe, args)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        using var proc = Process.Start(psi) ?? throw new Exception($"could not start {Path.GetFileName(exe)}");
        var stdout = new StringBuilder();
        var stderr = new StringBuilder();
        proc.OutputDataReceived += (_, e) => { if (e.Data is not null) lock (stdout) stdout.AppendLine(e.Data); };
        proc.ErrorDataReceived += (_, e) => { if (e.Data is not null) lock (stderr) stderr.AppendLine(e.Data); };
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();
        if (!proc.WaitForExit(timeoutMs))
        {
            try { proc.Kill(entireProcessTree: true); } catch { /* ignore */ }
            return (-1, stdout.ToString(), stderr.ToString(), true);
        }
        proc.WaitForExit(); // let the async output handlers flush
        return (proc.ExitCode, stdout.ToString(), stderr.ToString(), false);
    }

    private static void CheckClipboardRoundTrip()
    {
        var probe = "deploycore-selftest-" + Guid.NewGuid().ToString("N");
        Win32Interop.SetClipboardText(probe);
        var read = Win32Interop.GetClipboardText();
        if (read != probe) throw new Exception($"clipboard round-trip mismatch (set '{probe}', got '{read ?? "null"}') - a CI runner may have no window-station clipboard");
        Console.WriteLine("    clipboard set/get round-trips");
    }

    // --- harness ---

    private static string FfmpegPath()
    {
        var bundled = Path.Combine(AppContext.BaseDirectory, "ffmpeg.exe");
        return File.Exists(bundled) ? bundled : "ffmpeg.exe";
    }

    private static string Tail(string s) => s.Length > 500 ? s[^500..] : s;

    private static void Critical(string name, Action check)
    {
        try { check(); Console.WriteLine($"[PASS] {name}"); }
        catch (Exception ex) { _failures++; Console.WriteLine($"[FAIL] {name}: {ex.Message}"); }
    }

    private static async Task CriticalAsync(string name, Func<Task> check)
    {
        try { await check(); Console.WriteLine($"[PASS] {name}"); }
        catch (Exception ex) { _failures++; Console.WriteLine($"[FAIL] {name}: {ex.Message}"); }
    }

    private static void BestEffort(string name, Action check)
    {
        try { check(); Console.WriteLine($"[PASS] {name}"); }
        catch (Exception ex) { Console.WriteLine($"[WARN] {name}: {ex.Message}"); }
    }
}
