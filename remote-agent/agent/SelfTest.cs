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
        // Two NALs: a 4-byte start code then a 3-byte start code.
        var stream = new byte[] { 0, 0, 0, 1, 0x67, 0x42, 0, 0, 1, 0x65, 0xAA, 0xBB };
        var splitter = new AnnexBNalSplitter();
        var nals = splitter.Append(stream).ToList();
        // The first NAL is emitted once the SECOND start code is seen; the
        // trailing NAL stays buffered until more data / EOF - matching how the
        // real tail loop works. One complete NAL here is enough to prove the
        // split logic.
        if (nals.Count < 1) throw new Exception("no NAL units split out");
        if (nals[0].Length != 3 || nals[0][0] != 0x67) throw new Exception("first NAL content wrong");
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
        var ffmpeg = FfmpegPath();
        var psi = new ProcessStartInfo(ffmpeg, "-hide_banner -version")
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
        };
        using var proc = Process.Start(psi) ?? throw new Exception("could not start ffmpeg");
        var stdout = proc.StandardOutput.ReadToEnd();
        proc.WaitForExit(15000);
        if (!stdout.Contains("ffmpeg version")) throw new Exception("ffmpeg -version output unexpected");
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
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            await server.WaitForConnectionAsync(cts.Token);

            var writer = new StreamWriter(server, new UTF8Encoding(false)) { AutoFlush = true };
            var reader = new StreamReader(server, new UTF8Encoding(false));

            // The helper sends a "screensize" immediately on connect, and again
            // after a "resize". Send a resize and confirm we get a screensize
            // reply back - proves the whole JSON pipe protocol round-trips.
            await writer.WriteLineAsync("{\"t\":\"resize\",\"w\":800,\"h\":600}");

            string? gotScreensize = null;
            for (var i = 0; i < 5 && gotScreensize is null; i++)
            {
                var line = await reader.ReadLineAsync(cts.Token);
                if (line is null) break;
                try
                {
                    var msg = JsonDocument.Parse(line).RootElement;
                    if (msg.TryGetProperty("t", out var t) && t.GetString() == "screensize"
                        && msg.TryGetProperty("w", out var w) && w.GetInt32() > 0)
                        gotScreensize = line;
                }
                catch (JsonException) { /* ignore non-JSON */ }
            }
            if (gotScreensize is null) throw new Exception("no valid 'screensize' reply from the helper");
            Console.WriteLine($"    helper replied: {gotScreensize}");
        }
        finally
        {
            try { server.Dispose(); } catch { /* ignore */ }
            try { if (!child.WaitForExit(3000)) child.Kill(entireProcessTree: true); } catch { /* ignore */ }
        }
    }

    private static void CheckGdigrabCapture()
    {
        var outPath = Path.Combine(Path.GetTempPath(), "deploycore-selftest-" + Guid.NewGuid().ToString("N") + ".h264");
        var args = $"-hide_banner -f gdigrab -framerate 10 -t 1 -i desktop -c:v libx264 -preset ultrafast -pix_fmt yuv420p -f h264 -y \"{outPath}\"";
        try
        {
            using var proc = Process.Start(new ProcessStartInfo(FfmpegPath(), args)
            {
                RedirectStandardError = true,
                UseShellExecute = false,
            }) ?? throw new Exception("could not start ffmpeg");
            var stderr = proc.StandardError.ReadToEnd();
            proc.WaitForExit(30000);
            var bytes = File.Exists(outPath) ? new FileInfo(outPath).Length : 0;
            if (bytes <= 0)
                throw new Exception($"gdigrab produced no output (a CI runner may have no capturable desktop). ffmpeg tail: {Tail(stderr)}");
            Console.WriteLine($"    gdigrab captured {bytes} bytes of H264");
        }
        finally
        {
            try { File.Delete(outPath); } catch { /* ignore */ }
        }
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
