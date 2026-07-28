using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace DeployCoreAgent;

/// <summary>
/// Runs INSIDE the active console session, launched by ShadowSession via
/// SessionCapture.StartInActiveSession as "DeployCoreAgent.exe
/// --session-helper &lt;pipe&gt;". Its whole reason to exist is Session 0
/// isolation: SendInput, the clipboard API, and ChangeDisplaySettingsEx all
/// act on the CALLING THREAD's window station/desktop, so when the agent
/// SERVICE (Session 0, a non-interactive service window station) calls them
/// directly the mouse/keys never move, the clipboard read/written is the
/// wrong one, and the resolution change fails with DISP_CHANGE_FAILED. This
/// process lives on the real interactive desktop (winsta0\default of the
/// active session - see SessionCapture's lpDesktop handling), so it performs
/// those operations on the service's behalf.
///
/// Protocol: newline-delimited JSON over a duplex named pipe (this end is the
/// client; ShadowSession is the server). Service -&gt; helper commands (all
/// tagged "t"): mouseabs {x,y} (already SendInput-normalized 0..65535),
/// mousedown/mouseup {button}, wheel {dy}, keydown/keyup {code}, cad,
/// clipset {text}, resize {w,h}. Helper -&gt; service: clip {text} (the
/// in-session clipboard changed), screensize {w,h} (the real console
/// resolution, reported at startup and after every resize so the service's
/// mouse-coordinate math tracks the true size rather than Session 0's own
/// desktop metrics).
/// </summary>
internal static class SessionHelper
{
    public static async Task RunAsync(string pipeName, ILogger logger)
    {
        using var pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        try
        {
            await pipe.ConnectAsync(15000);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "session-helper: could not connect to pipe {Pipe} within 15s.", pipeName);
            return;
        }

        // The resolution we start at - restored on exit. A flags=0
        // ChangeDisplaySettingsEx change is a live change that persists until
        // reboot (it's only "not written to the registry", NOT auto-reverted),
        // so without this the console would be left at whatever size the last
        // browser viewport requested.
        var originalSize = Win32Interop.GetPrimaryScreenSize();
        logger.LogInformation("session-helper connected on pipe {Pipe}; console resolution {W}x{H}; {Station}.",
            pipeName, originalSize.Width, originalSize.Height, Win32Interop.DescribeStationAndDesktop());

        var writer = new StreamWriter(pipe, new UTF8Encoding(false)) { AutoFlush = true };
        var reader = new StreamReader(pipe, new UTF8Encoding(false));
        var writeSem = new SemaphoreSlim(1, 1);

        async Task SendAsync(object msg)
        {
            var json = JsonSerializer.Serialize(msg);
            await writeSem.WaitAsync();
            // AutoFlush pushes each line to the pipe. Safe against the
            // pipe-Flush-blocks-until-peer-reads gotcha because the service
            // (ShadowSession) enters its read loop immediately on connect, so
            // it's always reading while we write.
            try { await writer.WriteLineAsync(json); }
            catch (Exception ex) { logger.LogDebug(ex, "session-helper: send failed (pipe closing?)."); }
            finally { writeSem.Release(); }
        }

        // Report the real in-session screen size up front so the service's
        // coordinate normalization uses the console's true resolution.
        await SendAsync(new { t = "screensize", w = originalSize.Width, h = originalSize.Height });

        using var cts = new CancellationTokenSource();

        // Tell the service which desktop is ACTUALLY on the monitor, and keep
        // telling it when that changes (sign-in, lock, UAC, screen saver).
        // The service can't determine this from Session 0 - only a process
        // inside the session can - and capturing the wrong desktop is exactly
        // how a session ends up as a perfectly-encoded picture of a blank
        // screen. Also attaches this thread to that desktop so SendInput, the
        // clipboard, and ChangeDisplaySettingsEx all act on it.
        string? lastDesktop = null;
        async Task SyncInputDesktopAsync()
        {
            var name = Win32Interop.AttachThreadToInputDesktop() ?? Win32Interop.GetInputDesktopName();
            if (name is null || name == lastDesktop) return;
            lastDesktop = name;
            logger.LogInformation("session-helper: input desktop is now '{Desktop}'.", name);
            await SendAsync(new { t = "desktop", name });
        }

        await SyncInputDesktopAsync();

        var desktopWatchTask = Task.Run(async () =>
        {
            using var timer = new PeriodicTimer(TimeSpan.FromSeconds(2));
            try
            {
                while (await timer.WaitForNextTickAsync(cts.Token)) await SyncInputDesktopAsync();
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { logger.LogDebug(ex, "session-helper: desktop watch ended."); }
        });

        // Clipboard poll loop, IN-SESSION (so it reads the logged-in user's
        // own clipboard, not Session 0's). Seed lastClip with the current
        // value so the very first real change is what gets pushed, not the
        // pre-existing contents on connect.
        var lastClip = SafeGetClipboard();
        var clipTask = Task.Run(async () =>
        {
            using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
            try
            {
                while (await timer.WaitForNextTickAsync(cts.Token))
                {
                    var text = SafeGetClipboard();
                    if (text is null || text == lastClip) continue;
                    lastClip = text;
                    await SendAsync(new { t = "clip", text });
                }
            }
            catch (OperationCanceledException) { }
        });

        try
        {
            string? line;
            while ((line = await reader.ReadLineAsync()) is not null)
            {
                JsonElement msg;
                try { msg = JsonDocument.Parse(line).RootElement; }
                catch (JsonException) { continue; }
                if (!msg.TryGetProperty("t", out var tEl)) continue;

                try
                {
                    switch (tEl.GetString())
                    {
                        case "mouseabs":
                            Win32Interop.MoveMouseAbsolute(msg.GetProperty("x").GetInt32(), msg.GetProperty("y").GetInt32());
                            break;
                        case "mousedown":
                            Win32Interop.MouseButton(msg.GetProperty("button").GetInt32(), down: true);
                            break;
                        case "mouseup":
                            Win32Interop.MouseButton(msg.GetProperty("button").GetInt32(), down: false);
                            break;
                        case "wheel":
                            Win32Interop.MouseWheel(msg.GetProperty("dy").GetInt32());
                            break;
                        case "keydown":
                            Win32Interop.KeyEvent(msg.GetProperty("code").GetString() ?? "", down: true);
                            break;
                        case "keyup":
                            Win32Interop.KeyEvent(msg.GetProperty("code").GetString() ?? "", down: false);
                            break;
                        case "cad":
                            Win32Interop.SendSecureAttentionSequence(logger);
                            break;
                        case "clipset":
                        {
                            var text = msg.GetProperty("text").GetString() ?? "";
                            Win32Interop.SetClipboardText(text);
                            // Remember it so the poll loop above doesn't
                            // immediately echo our own write straight back.
                            lastClip = text;
                            break;
                        }
                        case "resize":
                        {
                            var w = msg.GetProperty("w").GetInt32();
                            var h = msg.GetProperty("h").GetInt32();
                            var result = Win32Interop.TrySetNearestResolution(w, h, logger);
                            var actual = result ?? Win32Interop.GetPrimaryScreenSize();
                            await SendAsync(new { t = "screensize", w = actual.Width, h = actual.Height });
                            break;
                        }
                    }
                }
                catch (Exception ex)
                {
                    logger.LogDebug(ex, "session-helper: error handling command line.");
                }
            }
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "session-helper: pipe read loop ended.");
        }
        finally
        {
            cts.Cancel();
            try { await clipTask; } catch { /* ignore */ }
            try { await desktopWatchTask; } catch { /* ignore */ }

            // Best-effort restore of the console resolution we started at.
            try
            {
                if (Win32Interop.GetPrimaryScreenSize() != originalSize)
                {
                    Win32Interop.TrySetNearestResolution(originalSize.Width, originalSize.Height, logger);
                    logger.LogInformation("session-helper: restored console resolution to {W}x{H}.",
                        originalSize.Width, originalSize.Height);
                }
            }
            catch (Exception ex)
            {
                logger.LogDebug(ex, "session-helper: could not restore original resolution.");
            }
        }
    }

    private static string? SafeGetClipboard()
    {
        try { return Win32Interop.GetClipboardText(); }
        catch { return null; }
    }
}
