using System.Diagnostics;
using System.IO.Pipes;
using System.Linq;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SIPSorcery.Net; // RTCPeerConnection and friends live here as of the 6.x line, per SIPSorcery's own examples - if a real restore/build puts some of these types (e.g. MediaStreamTrack/SDPAudioVideoMediaFormat) in a sibling namespace instead, add that using too; see the file-level note below.

namespace DeployCoreAgent;

/// <summary>
/// One instance per active session_id with mode "shadow" (see
/// remote-agent/PROTOCOL.md sections 1-2). Pipeline: ffmpeg (gdigrab desktop
/// capture + libx264 encode) -> Annex-B NAL parsing -> a SIPSorcery
/// RTCPeerConnection's H264 video track, plus an RTCDataChannel carrying
/// mouse/keyboard/clipboard/resize messages the other way.
///
/// Unverified-API note (see this project's README): every SIPSorcery call in
/// this file is written against the library's documented/example public API
/// from general familiarity with the 6.x line, NOT compiled against the
/// actual restored package (no Windows/.NET/internet in the environment this
/// was written in). Method names, casing (SIPSorcery mirrors the browser
/// WebRTC spec's camelCase in many places - createOffer, addTrack,
/// addIceCandidate - rather than .NET's usual PascalCase), and exact
/// overloads may need small fixes on the first real build; that's expected
/// and called out rather than hidden.
/// </summary>
internal sealed class ShadowSession(string sessionId, AgentConfig config, ControlChannelClient controlChannel, ILogger<ShadowSession> logger)
{
    private const int FrameRate = 30;
    private const int VideoClockRateHz = 90000;
    private const uint FrameDurationRtpUnits = VideoClockRateHz / FrameRate;

    private readonly IVirtualDisplay _virtualDisplay = new NoOpVirtualDisplay(logger);

    // NOT readonly: TrySetNearestResolution actually changes this machine's
    // real resolution (see HandleResize), so mouse-coordinate normalization
    // (RescaleAndNormalize) needs to track the CURRENT real size, not just
    // whatever it was at session start.
    private (int Width, int Height) _nativeScreenSize = Win32Interop.GetPrimaryScreenSize();

    // Spans this session's whole lifetime (StartAsync..Stop) - unlike
    // _captureCts below, which is scoped to just the current ffmpeg process
    // and gets replaced on every resize.
    private readonly CancellationTokenSource _sessionCts = new();
    private string? _lastClipboardText;

    // The in-session helper (see SessionHelper.cs) that actually performs
    // input injection, clipboard, and resolution changes on the interactive
    // desktop - the agent SERVICE runs in Session 0 and can't. This end is
    // the pipe server; the helper (a child launched into the active session)
    // is the client. Everything the old code called Win32Interop for directly
    // in OnDataChannelMessage now goes over this pipe instead.
    private NamedPipeServerStream? _helperPipe;
    private StreamWriter? _helperWriter;
    private readonly SemaphoreSlim _helperWriteSem = new(1, 1);
    private uint? _helperProcessId;

    // The desktop actually receiving input, as reported by the in-session
    // helper (OpenInputDesktop). ffmpeg must be launched on THIS desktop or it
    // captures a blank screen - confirmed live: a session that encoded real
    // H.264 at 30fps the whole time was faithfully recording the empty
    // sign-in desktop while the user was on Default. Null until the helper
    // reports; capture restarts whenever it changes (sign-in, lock, UAC).
    private string? _inputDesktop;

    // Access-unit assembly for the video path: NALs are buffered until a
    // complete frame is ready, then handed to SIPSorcery in Annex-B form WITH
    // start codes (see FlushAccessUnit and the tail loop).
    private readonly List<byte[]> _pendingNals = new();
    private bool _pendingHasVcl;
    // The desktop the CURRENT helper process was launched on, and a counter so
    // each relaunch gets its own pipe name (a fresh server can't reuse the
    // name while the old one is still tearing down).
    private string? _helperDesktop;
    private int _helperGeneration;
    // The size/desktop the current capture was started with, so a desktop
    // change can relaunch ffmpeg with the same dimensions.
    private int? _requestedWidth;
    private int? _requestedHeight;

    private RTCPeerConnection? _pc;
    private RTCDataChannel? _dataChannel;

    // NOT a Process object - ffmpeg is launched into the active console
    // session via SessionCapture (Session 0, where this service itself
    // runs, has no access to the real interactive desktop at all - see that
    // class's own doc comment), which returns only a process id, not a
    // System.Diagnostics.Process. Process.GetProcessById(pid) is enough to
    // kill it later (see StopCapture).
    private uint? _ffmpegProcessId;
    private string? _captureFilePath;
    private CancellationTokenSource? _captureCts;
    private int _captureWidth;
    private int _captureHeight;

    public async Task StartAsync()
    {
        _pc = CreatePeerConnection();

        // Data channel must be added before createOffer() so it's
        // represented in the initial SDP - standard WebRTC ordering,
        // agent-offers or not.
        _dataChannel = await _pc.createDataChannel("input");
        _dataChannel.onmessage += OnDataChannelMessage; // SIPSorcery 6.x event shape as documented - unverified, see file header

        // The agent creates the SDP offer here, deliberately (PROTOCOL.md:
        // "the agent creates the SDP offer... not the more common
        // browser-offers pattern, because here the agent is the one with
        // media to add"). Trickle ICE, not vanilla-SDP: candidates are sent
        // one at a time via onicecandidate below as separate "kind":"ice"
        // signal messages, matching PROTOCOL.md's message table - we don't
        // wait for ICE gathering to finish before sending the offer.
        var offer = _pc.createOffer(null); // believed synchronous in SIPSorcery - unverified, see file header
        await _pc.setLocalDescription(offer);

        await controlChannel.SendJsonAsync(new
        {
            type = "signal",
            session_id = sessionId,
            kind = "offer",
            sdp = offer.sdp,
        });

        StartCapture(width: null, height: null); // native desktop resolution until the first resize
        StartHelper(); // input/clipboard/resolution, performed in the active session
    }

    /// <summary>Dispatches an incoming "signal" message for this session
    /// (answer/ice - see PROTOCOL.md section 1).</summary>
    public void HandleSignal(JsonElement message)
    {
        var kind = message.TryGetProperty("kind", out var kindEl) ? kindEl.GetString() : null;
        switch (kind)
        {
            case "answer":
            {
                var sdp = message.GetProperty("sdp").GetString() ?? "";
                _pc?.setRemoteDescription(new RTCSessionDescriptionInit { type = RTCSdpType.answer, sdp = sdp });
                break;
            }
            case "ice":
            {
                var candidate = message.GetProperty("candidate").GetString() ?? "";
                var sdpMid = message.TryGetProperty("sdpMid", out var midEl) ? midEl.GetString() : null;
                // TryGetInt32 (not TryGetUInt16) - JsonElement's exact set of
                // sized-integer TryGet* overloads isn't worth guessing at;
                // Int32 is unambiguously there and sdpMLineIndex always fits.
                ushort sdpMLineIndex = message.TryGetProperty("sdpMLineIndex", out var idxEl) && idxEl.TryGetInt32(out var v) ? (ushort)v : (ushort)0;
                _pc?.addIceCandidate(new RTCIceCandidateInit { candidate = candidate, sdpMid = sdpMid, sdpMLineIndex = sdpMLineIndex });
                break;
            }
            default:
                logger.LogWarning("Shadow session {SessionId}: signal with unrecognized kind {Kind} ignored.", sessionId, kind);
                break;
        }
    }

    public void Stop()
    {
        _sessionCts.Cancel();
        StopCapture();
        StopHelper(); // closes the pipe -> helper restores the original resolution and exits
        _sessionCts.Dispose();
        try
        {
            _dataChannel?.close(); // SIPSorcery API as documented - unverified, see file header
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Shadow session {SessionId}: error closing data channel.", sessionId);
        }

        try
        {
            _pc?.close(); // SIPSorcery API as documented - unverified, see file header
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Shadow session {SessionId}: error closing peer connection.", sessionId);
        }

        _pc = null;
        _dataChannel = null;
    }

    // --- Peer connection setup ---

    private RTCPeerConnection CreatePeerConnection()
    {
        var rtcConfig = new RTCConfiguration
        {
            iceServers = new List<RTCIceServer>
            {
                new() { urls = $"stun:{config.TurnHost}:{config.TurnPort}" },
                new() { urls = $"turn:{config.TurnHost}:{config.TurnPort}", username = config.TurnUsername, credential = config.TurnPassword },
            },
        };
        var pc = new RTCPeerConnection(rtcConfig);

        // "H264", clock rate 90000, dynamic payload type 96 - a conventional
        // choice, not one PROTOCOL.md pins to a specific number; the SDP
        // negotiation is what actually tells the browser which payload type
        // to expect. The fmtp line matters, not just cosmetic: confirmed
        // against SIPSorcery's own source (SDPAudioVideoMediaFormat.CheckCompatible)
        // that a missing/ambiguous packetization-mode is exactly the kind of
        // thing that can leave the offer without a fully specified H264
        // profile - packetization-mode=1 (non-interleaved) matches how
        // SendVideo's own per-NAL calls are packetized (see
        // ReadNalUnitsAsync below) and is what every mainstream browser
        // expects by default; profile-level-id 42e01f is Constrained
        // Baseline, level 3.1 - the safest, most broadly-decodable choice
        // for a first real test, not tuned for quality yet.
        const string h264Fmtp = "packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1";
        var videoFormat = new SDPAudioVideoMediaFormat(SDPMediaTypesEnum.video, 96, "H264", VideoClockRateHz, fmtp: h264Fmtp);
        var videoTrack = new MediaStreamTrack(SDPMediaTypesEnum.video, false,
            new List<SDPAudioVideoMediaFormat> { videoFormat }, MediaStreamStatusEnum.SendOnly);
        pc.addTrack(videoTrack);

        pc.onicecandidate += candidate =>
        {
            if (candidate is null) return;
            _ = controlChannel.SendJsonAsync(new
            {
                type = "signal",
                session_id = sessionId,
                kind = "ice",
                candidate = candidate.candidate,
                sdpMid = candidate.sdpMid,
                sdpMLineIndex = candidate.sdpMLineIndex,
            });
        };

        pc.onconnectionstatechange += state =>
            logger.LogInformation("Shadow session {SessionId}: peer connection state {State}.", sessionId, state);

        return pc;
    }

    // --- Data channel message handling (PROTOCOL.md section 2) ---

    private void OnDataChannelMessage(RTCDataChannel channel, DataChannelPayloadProtocols protocol, byte[] data)
    {
        JsonElement msg;
        try
        {
            msg = JsonDocument.Parse(data).RootElement;
        }
        catch (JsonException)
        {
            return;
        }

        if (!msg.TryGetProperty("t", out var tEl)) return;
        // Every interactive operation below (mouse, keys, Ctrl+Alt+Del,
        // clipboard, resolution) MUST run in the active console session, not
        // here in Session 0 - so each is forwarded to the in-session helper
        // over the pipe rather than calling Win32Interop directly (which would
        // silently act on Session 0's own dead desktop). Mouse coordinates are
        // normalized to SendInput's 0..65535 space HERE (this side owns the
        // capture-size/native-size state the math needs); the helper just
        // injects them.
        try
        {
            switch (tEl.GetString())
            {
                case "mousemove":
                    SendToHelper(new { t = "mouseabs", x = ScaleX(msg.GetProperty("x").GetInt32()), y = ScaleY(msg.GetProperty("y").GetInt32()) });
                    break;
                case "mousedown":
                    SendToHelper(new { t = "mousedown", button = msg.GetProperty("button").GetInt32() });
                    break;
                case "mouseup":
                    SendToHelper(new { t = "mouseup", button = msg.GetProperty("button").GetInt32() });
                    break;
                case "wheel":
                    SendToHelper(new { t = "wheel", dy = msg.GetProperty("dy").GetInt32() });
                    break;
                case "keydown":
                    SendToHelper(new { t = "keydown", code = msg.GetProperty("code").GetString() ?? "" });
                    break;
                case "keyup":
                    SendToHelper(new { t = "keyup", code = msg.GetProperty("code").GetString() ?? "" });
                    break;
                case "cad":
                    SendToHelper(new { t = "cad" });
                    break;
                case "clipboard":
                {
                    var text = msg.GetProperty("text").GetString() ?? "";
                    // Remember it so the helper's own clipboard poll (echoed
                    // back over the pipe) doesn't bounce this straight back to
                    // the browser as if it were a fresh local change.
                    _lastClipboardText = text;
                    SendToHelper(new { t = "clipset", text });
                    break;
                }
                case "resize":
                    HandleResize(msg.GetProperty("w").GetInt32(), msg.GetProperty("h").GetInt32());
                    break;
            }
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Shadow session {SessionId}: error handling data channel message.", sessionId);
        }
    }

    // --- In-session helper (input / clipboard / resolution) ---

    /// <summary>
    /// Launches the "--session-helper" child into the active console session
    /// and wires up the duplex pipe to it (see SessionHelper.cs). Input
    /// injection, the clipboard, and ChangeDisplaySettingsEx all have to run
    /// on the interactive desktop, which this Session-0 service can't touch -
    /// the helper does them on its behalf. Best-effort: if the helper can't
    /// launch, Shadow still streams video, just without input/clipboard/
    /// resolution, and logs why.
    /// </summary>
    private void StartHelper()
    {
        var pipeName = $"DeployCoreAgentSession-{sessionId}-{++_helperGeneration}";
        _helperDesktop = _inputDesktop;
        try
        {
            // Explicit 64KB buffers, NOT the default 0. With a zero-size out
            // buffer a named-pipe write does not complete until the peer
            // physically reads it - so a burst of mouse-move messages can
            // block on the helper's read loop instead of completing locally.
            // Confirmed live in CI: a single 30-byte write hung indefinitely
            // and only unblocked when the pipe was disposed.
            _helperPipe = new NamedPipeServerStream(pipeName, PipeDirection.InOut, 1,
                PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 65536, 65536);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Shadow session {SessionId}: could not create the helper pipe - input/clipboard/resolution disabled.", sessionId);
            return;
        }

        var exePath = Path.Combine(AppContext.BaseDirectory, "DeployCoreAgent.exe");
        var commandLine = $"\"{exePath}\" --session-helper {pipeName}";
        try
        {
            _helperProcessId = SessionCapture.StartInActiveSession(commandLine, AppContext.BaseDirectory, logger, _inputDesktop);
            logger.LogInformation(
                "Shadow session {SessionId}: launched session-helper (pid {Pid}) on desktop '{Desktop}'.",
                sessionId, _helperProcessId, _inputDesktop ?? "(bootstrap)");
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Shadow session {SessionId}: failed to launch the session-helper - input/clipboard/resolution disabled.", sessionId);
            return;
        }

        _ = HelperConnectAndReadAsync(_helperPipe, _sessionCts.Token);
    }

    /// <summary>
    /// Waits for the helper to connect, then reads its messages: "clip" (the
    /// in-session clipboard changed - forward it to the browser) and
    /// "screensize" (the real console resolution - keep _nativeScreenSize in
    /// step so mouse-coordinate normalization stays correct after a resize).
    /// </summary>
    private async Task HelperConnectAndReadAsync(NamedPipeServerStream pipe, CancellationToken ct)
    {
        try
        {
            await pipe.WaitForConnectionAsync(ct);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Shadow session {SessionId}: session-helper never connected.", sessionId);
            return;
        }
        logger.LogInformation("Shadow session {SessionId}: session-helper connected.", sessionId);

        _helperWriter = new StreamWriter(pipe, new UTF8Encoding(false)) { AutoFlush = true };
        var reader = new StreamReader(pipe, new UTF8Encoding(false));
        try
        {
            string? line;
            while (!ct.IsCancellationRequested && (line = await reader.ReadLineAsync(ct)) is not null)
            {
                JsonElement msg;
                try { msg = JsonDocument.Parse(line).RootElement; }
                catch (JsonException) { continue; }
                if (!msg.TryGetProperty("t", out var tEl)) continue;

                switch (tEl.GetString())
                {
                    case "clip":
                    {
                        var text = msg.TryGetProperty("text", out var txtEl) ? txtEl.GetString() : null;
                        if (text is null || text == _lastClipboardText) break;
                        _lastClipboardText = text;
                        try { _dataChannel?.send(JsonSerializer.Serialize(new { t = "clipboard", text })); }
                        catch (Exception ex) { logger.LogDebug(ex, "Shadow session {SessionId}: forwarding helper clipboard to browser failed.", sessionId); }
                        break;
                    }
                    case "screensize":
                    {
                        if (msg.TryGetProperty("w", out var wEl) && msg.TryGetProperty("h", out var hEl))
                            _nativeScreenSize = (wEl.GetInt32(), hEl.GetInt32());
                        break;
                    }
                    case "desktop":
                    {
                        // The helper (inside the session) told us which desktop
                        // is genuinely receiving input. Relaunch capture there
                        // if it isn't already - this is what turns "encoding a
                        // blank screen at 30fps" into an actual picture, and
                        // what keeps the picture alive across sign-in, lock,
                        // and UAC prompts.
                        var name = msg.TryGetProperty("name", out var nameEl) ? nameEl.GetString() : null;
                        if (string.IsNullOrEmpty(name) || name == _inputDesktop) break;
                        var previous = _inputDesktop;
                        _inputDesktop = name;
                        logger.LogInformation(
                            "Shadow session {SessionId}: input desktop is '{Desktop}' (was '{Previous}') - restarting capture on it.",
                            sessionId, name, previous ?? "unknown");
                        StartCapture(_requestedWidth, _requestedHeight);
                        // Put the helper itself on that desktop too, so its
                        // SendInput/clipboard/display calls act on the desktop
                        // the operator is actually looking at. The relaunched
                        // helper reports the same desktop back, which is then a
                        // no-op - so this converges rather than looping.
                        if (_helperDesktop != name)
                        {
                            logger.LogInformation(
                                "Shadow session {SessionId}: relaunching the session-helper on desktop '{Desktop}'.", sessionId, name);
                            _ = Task.Run(() => { StopHelper(); StartHelper(); });
                        }
                        break;
                    }
                }
            }
        }
        catch (OperationCanceledException) { /* session teardown */ }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Shadow session {SessionId}: helper read loop ended.", sessionId);
        }
    }

    private void SendToHelper(object message)
    {
        var writer = _helperWriter;
        if (writer is null) return; // helper not connected (yet / at all) - input is best-effort
        _ = SendToHelperAsync(writer, message);
    }

    private async Task SendToHelperAsync(StreamWriter writer, object message)
    {
        var json = JsonSerializer.Serialize(message);
        await _helperWriteSem.WaitAsync();
        // AutoFlush delivers each line; the helper is always in its own read
        // loop, so this never blocks on the pipe-Flush-waits-for-peer gotcha.
        try { await writer.WriteLineAsync(json); }
        catch (Exception ex) { logger.LogDebug(ex, "Shadow session {SessionId}: send to helper failed.", sessionId); }
        finally { _helperWriteSem.Release(); }
    }

    private void StopHelper()
    {
        // Closing the pipe makes the helper's own read loop hit EOF, at which
        // point it restores the original console resolution and exits on its
        // own. We still bound-wait and then force-kill as a backstop.
        _helperWriter = null;
        try { _helperPipe?.Dispose(); } catch { /* best-effort */ }
        _helperPipe = null;

        if (_helperProcessId is { } pid)
        {
            try
            {
                using var proc = Process.GetProcessById((int)pid);
                if (!proc.HasExited && !proc.WaitForExit(2000)) proc.Kill(entireProcessTree: true);
            }
            catch (ArgumentException) { /* already exited */ }
            catch (Exception ex) { logger.LogDebug(ex, "Shadow session {SessionId}: error stopping session-helper.", sessionId); }
            _helperProcessId = null;
        }
    }

    /// <summary>
    /// resize never renegotiates the RTCPeerConnection (see PROTOCOL.md
    /// section 2 - this is the actual fix for the old "WebSocket already
    /// CLOSING/CLOSED" churn class of bug: there is no teardown left to
    /// churn). Only the capture child process restarts.
    ///
    /// Two real, working paths, not one stub and one no-op:
    ///   - config.VirtualDisplay (a real IDD driver installed): exact
    ///     arbitrary sizing via IVirtualDisplay - not bundled yet, so this
    ///     is dormant in practice (see that interface's own docs).
    ///   - Otherwise (every install today): TrySetNearestResolutionInActiveSession
    ///     actually changes THIS machine's real display resolution via the
    ///     standard ChangeDisplaySettingsEx API, snapping to the closest mode
    ///     the adapter actually supports - a real, live resolution change,
    ///     not client-side-only scaling. ffmpeg's own -vf scale filter still
    ///     resamples to the EXACT requested size on top of that (the nearest
    ///     supported mode is rarely pixel-identical to the browser's own
    ///     viewport), so the final video is always exactly the right size
    ///     regardless of which adapter modes exist.
    /// </summary>
    private void HandleResize(int width, int height)
    {
        // Even dimensions only. libx264 + yuv420p (4:2:0 chroma) rejects an
        // odd width or height outright ("width/height not divisible by 2") and
        // ffmpeg exits instead of rounding - which showed up as a permanent
        // black screen after any resize to an odd size (the browser sends the
        // raw clientWidth/clientHeight of a flex-filled div, frequently odd
        // like 1283x817). Round down to even so the encoded size, the scale
        // filter, and the capture size used for mouse math all agree.
        width &= ~1;
        height &= ~1;
        if (width < 2 || height < 2) return;

        if (config.VirtualDisplay)
        {
            // Real IDD driver (not bundled yet - see IVirtualDisplay): exact
            // arbitrary sizing, and the -vf scale filter becomes a no-op.
            _virtualDisplay.SetResolution(width, height);
        }
        else
        {
            // The actual ChangeDisplaySettingsEx mode switch runs in the
            // session-helper - it has to be on the interactive desktop, which
            // this Session-0 service isn't. The helper reports the resulting
            // real size back as a "screensize" message, which updates
            // _nativeScreenSize for mouse-coordinate math.
            SendToHelper(new { t = "resize", w = width, h = height });
        }

        StartCapture(width, height);
    }

    // --- ffmpeg capture process ---

    private static string BuildFfmpegArgs(int? width, int? height, string outputPath)
    {
        // ponytail: `ddagrab` (DXGI Desktop Duplication - GPU-side, much
        // lower latency than gdigrab's GDI BitBlt-based capture) is the
        // documented future upgrade here, once verified against whatever
        // ffmpeg build actually ships (needs a newer ffmpeg build than the
        // one CI currently pins, plus a `-f lavfi -i ddagrab=...`
        // filter-graph invocation that's materially different from this
        // one) - not attempted blind in an environment where it can't be
        // tested against a real build. gdigrab ships in every ffmpeg build
        // and is good enough to prove the whole pipeline end to end first.
        // NOTE: gdigrab and ddagrab are equally affected by the Session 0
        // problem SessionCapture solves - this is a session-level
        // restriction, not specific to either capture API.
        // -report: ffmpeg's OWN diagnostic log (full command line, every
        // stderr line, the real reason gdigrab/libx264 fails if either
        // does) written to a file in the working directory - added after a
        // real test showed the capture file simply never appearing, with no
        // way to tell WHY from this agent's own side (stdout/stderr were
        // never captured at all - see SessionCapture's own doc comment on
        // why piping them across the CreateProcessAsUser boundary wasn't
        // attempted). TailCaptureFileAsync reads this file back and logs
        // its content itself if the capture file still never shows up.
        const string baseArgs = "-report -f gdigrab -framerate 30 -i desktop";
        // -y: overwrite the output file without an interactive prompt - now
        // load-bearing, not cosmetic, since output is a real file path that
        // may already exist from this session's own previous
        // start/resize/restart (ffmpeg's default behavior otherwise waits on
        // stdin for a y/N answer that will never come from a Windows
        // service with no console - a real, silent hang this project has
        // already been burned by once elsewhere, in the old RustDesk
        // install script's own UAC-prompt hang).
        // -g 30 / -keyint_min 30: an IDR every second. libx264's default GOP is
        // ~250 frames (8+ seconds at 30fps), and a browser can decode NOTHING
        // until it receives a keyframe with its SPS/PPS - so a viewer that
        // joins mid-GOP (always, since capture starts before the WebRTC
        // handshake finishes) stares at a black video element until the next
        // one. Also makes recovery from any dropped packet a second, not eight.
        const string encodeArgs = "-c:v libx264 -preset ultrafast -tune zerolatency -g 30 -keyint_min 30 -pix_fmt yuv420p -f h264 -y";
        var quotedOutput = $"\"{outputPath}\"";

        if (width is null || height is null)
            return $"{baseArgs} {encodeArgs} {quotedOutput}";

        // gdigrab always captures at whatever the console's CURRENT native
        // size already is - by the time this runs, HandleResize has already
        // either called IVirtualDisplay (a real driver, not bundled yet) or
        // Win32Interop.TrySetNearestResolution (today's real path: an actual
        // mode switch to the closest size the adapter supports). Either way,
        // "native" may not be pixel-identical to the browser's requested
        // w x h - a driver's own mode list might not have this exact size,
        // and TrySetNearestResolution only ever snaps to an existing mode -
        // so this scale filter is what guarantees the final encoded video is
        // always exactly w x h regardless of how close the underlying mode
        // switch landed.
        return $"{baseArgs} -vf scale={width}:{height} {encodeArgs} {quotedOutput}";
    }

    private void StartCapture(int? width, int? height)
    {
        StopCapture();
        _requestedWidth = width;
        _requestedHeight = height;

        var bundled = Path.Combine(AppContext.BaseDirectory, "ffmpeg.exe");
        var ffmpegPath = File.Exists(bundled) ? bundled : "ffmpeg.exe"; // PATH fallback

        // C:\ProgramData\DeployCore, NOT Path.GetTempPath() - this service
        // runs as SYSTEM, but ffmpeg is launched into the ACTIVE CONSOLE
        // SESSION under a different (the logged-in user's) token (see
        // SessionCapture) - %TEMP% resolves to a DIFFERENT, per-account
        // path for each of them, and the user's copy of ffmpeg has no
        // reason to be able to write into SYSTEM's own temp directory.
        // %ProgramData% is a single, fixed machine-wide path regardless of
        // which account resolves it, and is already where this agent's own
        // config file lives (see AgentConfig/Program.cs), so it's already
        // known to be writable/reachable from both contexts.
        var dataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "DeployCore");
        Directory.CreateDirectory(dataDir);
        _captureFilePath = Path.Combine(dataDir, $"shadow-{sessionId}.h264");
        try { File.Delete(_captureFilePath); } catch { /* fine if it never existed */ }

        var commandLine = $"\"{ffmpegPath}\" {BuildFfmpegArgs(width, height, _captureFilePath)}";
        uint pid;
        try
        {
            // dataDir, not AppContext.BaseDirectory - this is where -report's
            // own log file lands (ffmpeg writes it relative to its working
            // directory), the same place TailCaptureFileAsync looks for it.
            pid = SessionCapture.StartInActiveSession(commandLine, dataDir, logger, _inputDesktop);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Shadow session {SessionId}: failed to launch ffmpeg into the active console session.", sessionId);
            return;
        }
        // Confirms the launch itself succeeded, distinct from whether ffmpeg
        // then actually produces frames (TailCaptureFileAsync's own 5s
        // progress log / 10s "never appeared" warning cover that side) -
        // added specifically because the first real test's log had no way to
        // tell "ffmpeg never launched" from "launched but silent" apart from
        // both looking like a black screen with no capture-launch failure
        // logged either.
        logger.LogInformation("Shadow session {SessionId}: launched ffmpeg (pid {Pid}) into the active console session.", sessionId, pid);

        _ffmpegProcessId = pid;
        _captureWidth = width ?? _nativeScreenSize.Width;
        _captureHeight = height ?? _nativeScreenSize.Height;
        _captureCts = new CancellationTokenSource();

        _ = TailCaptureFileAsync(_captureFilePath, _captureCts.Token);
    }

    private void StopCapture()
    {
        _captureCts?.Cancel();
        _captureCts = null;

        if (_ffmpegProcessId is { } pid)
        {
            // Checked BEFORE killing the process below, and regardless of
            // how long this capture attempt lived - confirmed live that
            // sessions were consistently torn down (peer connection closed)
            // within 3-9 seconds, always shortly after a resize-triggered
            // restart, well under TailCaptureFileAsync's own 10s "never
            // appeared" threshold - so that check never got a chance to
            // fire, and this agent still had no visibility into what a
            // short-lived capture attempt was actually doing. A real file
            // with real bytes already has TailCaptureFileAsync's own 5s
            // progress log covering it - this only dumps ffmpeg's own
            // -report content for the empty-or-missing case.
            long fileBytes = -1;
            if (_captureFilePath is { } checkPath)
            {
                try { fileBytes = new FileInfo(checkPath).Length; }
                catch { /* doesn't exist yet / not accessible */ }
            }
            if (fileBytes <= 0 && _captureFilePath is not null)
            {
                LogFfmpegFailureDiagnostics(_captureFilePath);
            }

            try
            {
                using var proc = Process.GetProcessById((int)pid);
                if (!proc.HasExited)
                {
                    proc.Kill(entireProcessTree: true);
                    // Wait for it to actually exit before StartCapture (right
                    // after) deletes and relaunches ffmpeg at the SAME output
                    // path - otherwise the dying ffmpeg can still hold
                    // shadow-{sessionId}.h264 open for a few ms, colliding with
                    // the new writer (a sharing violation or a burst of garbled
                    // H264 right after every resize). Bounded so a wedged
                    // process can't stall the restart forever.
                    proc.WaitForExit(2000);
                }
            }
            catch (ArgumentException)
            {
                // GetProcessById throws when the pid no longer exists - it
                // already exited on its own, not an error worth logging.
            }
            catch (Exception ex)
            {
                logger.LogDebug(ex, "Shadow session {SessionId}: error stopping ffmpeg (likely already exiting).", sessionId);
            }
            _ffmpegProcessId = null;
        }

        if (_captureFilePath is { } path)
        {
            try { File.Delete(path); } catch { /* best-effort cleanup */ }
            _captureFilePath = null;
        }
    }

    /// <summary>
    /// Called both when the capture file never appears within
    /// TailCaptureFileAsync's own 10s retry window, AND from StopCapture
    /// for a capture that ended (for any reason) before that 10s window
    /// even elapsed with nothing written yet - confirmed live that the
    /// latter is the common case, not the former: real sessions were
    /// consistently torn down well under 10 seconds. Two concrete,
    /// distinguishing checks - added after a real test showed this still
    /// happening even once the launch
    /// mechanism itself started reporting success at every step (a real
    /// session, a real desktop, a real PID - see SessionCapture) - so the
    /// remaining open question is specifically what ffmpeg itself is doing:
    ///   1. Is the process even still alive? (a crash vs. a genuine hang
    ///      look identical from the capture-file side alone.)
    ///   2. What does ffmpeg's OWN -report log say? (BuildFfmpegArgs adds
    ///      -report specifically for this - it's ffmpeg's real stderr
    ///      output, including the actual reason gdigrab or libx264 failed,
    ///      if either did - this agent has never been able to see that
    ///      before, since stdout/stderr were never captured across the
    ///      CreateProcessAsUser boundary at all.)
    /// </summary>
    private void LogFfmpegFailureDiagnostics(string capturePath)
    {
        var stillRunning = false;
        if (_ffmpegProcessId is { } pid)
        {
            try
            {
                using var proc = Process.GetProcessById((int)pid);
                stillRunning = !proc.HasExited;
            }
            catch (ArgumentException)
            {
                // already exited - stillRunning stays false
            }
        }

        string? reportContent = null;
        try
        {
            var dir = Path.GetDirectoryName(capturePath);
            if (dir is not null)
            {
                var report = Directory.GetFiles(dir, "ffmpeg-*.log")
                    .Select(f => new FileInfo(f))
                    .OrderByDescending(f => f.LastWriteTimeUtc)
                    .FirstOrDefault();
                if (report is not null)
                {
                    using var reportStream = new FileStream(report.FullName, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                    using var reportReader = new StreamReader(reportStream);
                    var text = reportReader.ReadToEnd();
                    // Tail only - a long-lived ffmpeg's own report can run to
                    // many KB of per-frame logging; the actual failure
                    // reason is always near the end, close to wherever it
                    // gave up or crashed.
                    reportContent = text.Length > 4000 ? text[^4000..] : text;
                }
            }
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Shadow session {SessionId}: could not read ffmpeg's own -report log.", sessionId);
        }

        logger.LogWarning(
            "Shadow session {SessionId}: capture file {Path} has no data - ffmpeg process {StillRunning} still running. ffmpeg's own -report log:\n{ReportContent}",
            sessionId, capturePath, stillRunning ? "IS" : "is NOT",
            reportContent ?? "(no -report log file found - ffmpeg may not have started at all)");
    }

    /// <summary>
    /// Reads NAL units from ffmpeg's OUTPUT FILE as it grows, not from a
    /// redirected stdout pipe - see SessionCapture's own doc comment for
    /// why (ffmpeg writing to a Windows named pipe as OUTPUT is a confirmed
    /// unreliable pattern, and CreateProcessAsUser makes inheriting a piped
    /// stdout handle across the session boundary its own separate risk this
    /// project isn't taking on without being able to test it). The NAL
    /// splitting / SendVideo logic below is otherwise UNCHANGED from the
    /// stdout-based version.
    /// </summary>
    private async Task TailCaptureFileAsync(string path, CancellationToken ct)
    {
        // ffmpeg (launched into a different session - see SessionCapture)
        // needs a moment to actually start and create this file; a plain
        // bounded retry loop is simpler and safer than a FileSystemWatcher
        // for a single, already-known path. IOException here commonly means
        // a sharing violation while ffmpeg still has the file open
        // exclusively for creation - also worth retrying, not failing on.
        FileStream? stream = null;
        try
        {
            for (var attempt = 0; attempt < 100 && stream is null && !ct.IsCancellationRequested; attempt++)
            {
                try
                {
                    stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                }
                catch (FileNotFoundException) { await Task.Delay(100, ct); }
                catch (DirectoryNotFoundException) { await Task.Delay(100, ct); }
                catch (IOException) { await Task.Delay(100, ct); }
                catch (UnauthorizedAccessException) { await Task.Delay(100, ct); }
            }
        }
        catch (OperationCanceledException)
        {
            return; // session ended/resized while still waiting for the file to appear
        }
        catch (Exception ex)
        {
            // A catch-all here specifically because this method is invoked
            // fire-and-forget (`_ = TailCaptureFileAsync(...)`) - an
            // exception type this retry loop doesn't already expect would
            // otherwise propagate out of an unobserved Task and vanish with
            // NO log line at all, the exact silent-failure shape this
            // project already found and fixed once this round (see
            // SendVideo's own per-NAL try/catch above).
            logger.LogWarning(ex, "Shadow session {SessionId}: unexpected error waiting for the capture file to appear.", sessionId);
            return;
        }
        if (stream is null)
        {
            LogFfmpegFailureDiagnostics(path);
            return;
        }

        var splitter = new AnnexBNalSplitter();
        var buffer = new byte[65536];
        long frameCount = 0, byteCount = 0;
        _pendingNals.Clear();
        _pendingHasVcl = false;
        var lastProgressLog = DateTime.UtcNow;
        try
        {
            using (stream)
            {
                while (!ct.IsCancellationRequested)
                {
                    var read = await stream.ReadAsync(buffer, ct);
                    if (read == 0)
                    {
                        // Real end-of-file-SO-FAR, not "the writer closed
                        // the pipe" - this is a growing FILE, not a pipe, so
                        // 0 bytes just means nothing new has been written
                        // yet. Keep polling rather than treating this as the
                        // capture having ended (that was the actual bug
                        // shape when this read stdout - see git history).
                        await Task.Delay(20, ct);
                        continue;
                    }

                    foreach (var nal in splitter.Append(buffer.AsSpan(0, read)))
                    {
                        if (_pc is null || nal.Length == 0) continue;

                        // Assemble ACCESS UNITS (whole frames) and hand them to
                        // SIPSorcery in Annex-B form, START CODES INCLUDED.
                        // This is the difference between a healthy-looking
                        // session and a black one: SIPSorcery's H264 path scans
                        // its input for Annex-B start codes to find NAL
                        // boundaries, so a bare NAL with the start code stripped
                        // yields no NALs and therefore no RTP at all - the agent
                        // happily counts "NAL units sent" while the browser
                        // receives nothing and renders black.
                        //
                        // Sending per access unit rather than per NAL also fixes
                        // the timestamp handling this file used to call out as a
                        // corner cut: the frame duration is now passed once per
                        // frame instead of on every NAL of a multi-NAL frame
                        // (SPS+PPS+IDR), which was over-advancing RTP time on
                        // keyframes.
                        var nalType = nal[0] & 0x1F;
                        var isVcl = nalType is >= 1 and <= 5; // coded slice = this frame's picture data
                        // A new coded slice means the previous access unit is
                        // complete (its SPS/PPS/SEI already buffered ahead of it).
                        if (isVcl && _pendingHasVcl) FlushAccessUnit(ref frameCount, ref byteCount);
                        _pendingNals.Add(nal);
                        if (isVcl) _pendingHasVcl = true;
                    }

                    // Added specifically because the first real end-to-end
                    // test had no way to tell "ffmpeg is producing nothing"
                    // apart from "frames are flowing but never rendering in
                    // the browser" - both look identical (black screen) from
                    // the browser side alone.
                    if (DateTime.UtcNow - lastProgressLog > TimeSpan.FromSeconds(5))
                    {
                        logger.LogInformation("Shadow session {SessionId}: {FrameCount} frames / {ByteCount} bytes sent to SIPSorcery so far.", sessionId, frameCount, byteCount);
                        lastProgressLog = DateTime.UtcNow;
                    }
                }
            }
        }
        catch (OperationCanceledException)
        {
            // expected on capture restart/session teardown
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Shadow session {SessionId}: capture tail loop ended.", sessionId);
        }
    }

    /// <summary>
    /// Concatenates the buffered NALs into one Annex-B access unit - each NAL
    /// prefixed with a 4-byte start code - and hands it to SIPSorcery as a
    /// single frame.
    ///
    /// The start codes are the load-bearing part. SIPSorcery's H264 send path
    /// parses its input BY SCANNING FOR ANNEX-B START CODES; given a bare NAL
    /// with the start code stripped it finds no NAL boundaries and emits no
    /// RTP at all. That produced the worst possible failure mode: the agent
    /// logged frames "sent" and the peer connection reported connected, while
    /// the browser received nothing and rendered black.
    /// </summary>
    private void FlushAccessUnit(ref long frameCount, ref long byteCount)
    {
        if (_pendingNals.Count == 0) return;

        var total = 0;
        foreach (var nal in _pendingNals) total += 4 + nal.Length;
        var accessUnit = new byte[total];
        var offset = 0;
        foreach (var nal in _pendingNals)
        {
            accessUnit[offset] = 0x00;
            accessUnit[offset + 1] = 0x00;
            accessUnit[offset + 2] = 0x00;
            accessUnit[offset + 3] = 0x01;
            offset += 4;
            Buffer.BlockCopy(nal, 0, accessUnit, offset, nal.Length);
            offset += nal.Length;
        }
        _pendingNals.Clear();
        _pendingHasVcl = false;

        try
        {
            // Per-frame try/catch: SIPSorcery's GetSendingFormat can throw
            // before the SDP answer has landed (capture starts immediately,
            // signalling is still in flight). Skipping one frame is right;
            // letting it escape would end video for the whole session.
            _pc?.SendVideo(FrameDurationRtpUnits, accessUnit);
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Shadow session {SessionId}: SendVideo failed for one frame (skipping it).", sessionId);
            return;
        }

        frameCount++;
        byteCount += accessUnit.Length;
    }

    // --- Mouse coordinate rescaling ---

    private int ScaleX(int browserX) => RescaleAndNormalize(browserX, _captureWidth, _nativeScreenSize.Width);
    private int ScaleY(int browserY) => RescaleAndNormalize(browserY, _captureHeight, _nativeScreenSize.Height);

    /// <summary>
    /// Maps a coordinate the browser reports (relative to the CURRENT
    /// capture resolution - the last requested resize w/h, or native before
    /// any resize - per PROTOCOL.md's mousemove field docs) onto
    /// SendInput's normalized 0..65535 absolute coordinate space for the
    /// REAL primary screen (_nativeScreenSize, kept up to date by
    /// HandleResize's call to TrySetNearestResolution). The real screen DOES
    /// actually change size today (the nearest adapter-supported mode - see
    /// HandleResize), but rarely to something pixel-identical to the
    /// browser's own requested w x h, so ffmpeg's -vf scale filter still
    /// resamples on top of that - this first undoes THAT rescale before
    /// normalizing against whatever the real size currently is. Once a real
    /// IDD driver exists and virtualDisplay=true, captureSize == realSize
    /// exactly (the driver resizes to the exact requested size, no scale
    /// filter needed at all) and this multiply/divide pair becomes an
    /// identity - the same code stays correct in both regimes.
    /// </summary>
    private static int RescaleAndNormalize(int coordinate, int captureSize, int realSize)
    {
        if (captureSize <= 0 || realSize <= 0) return 0;
        long realCoordinate = (long)coordinate * realSize / captureSize;
        long normalized = realSize <= 1 ? 0 : realCoordinate * 65535 / (realSize - 1);
        return (int)Math.Clamp(normalized, 0, 65535);
    }
}

/// <summary>
/// Splits a raw H.264 Annex-B byte stream (start-code-delimited NAL units -
/// exactly what ffmpeg's "-f h264 -" stdout produces) into individual NAL
/// units with the start code stripped, in arrival order. Annex-B allows
/// either the 3-byte (00 00 01) or 4-byte (00 00 00 01) start code
/// interchangeably - libx264/ffmpeg uses both depending on position - so
/// this looks for either. Buffers only as much as one NAL unit's worth of
/// bytes between calls (a few KB to tens of KB for a keyframe at this
/// resolution/bitrate), which is fine at 30fps - this isn't a hot path
/// processing anywhere near enough data for a ring buffer to matter.
/// </summary>
internal sealed class AnnexBNalSplitter
{
    private readonly List<byte> _buffer = new();

    public IEnumerable<byte[]> Append(ReadOnlySpan<byte> data)
    {
        _buffer.AddRange(data.ToArray());
        var results = new List<byte[]>();

        while (true)
        {
            var firstStart = FindStartCode(0, out var firstLen);
            if (firstStart < 0) break; // no start code yet - wait for more data

            var searchFrom = firstStart + firstLen;
            var secondStart = FindStartCode(searchFrom, out _);
            if (secondStart < 0) break; // this NAL hasn't fully arrived yet - wait for more data

            var nalLength = secondStart - searchFrom;
            var nal = new byte[nalLength];
            _buffer.CopyTo(searchFrom, nal, 0, nalLength);
            results.Add(nal);

            _buffer.RemoveRange(0, secondStart); // leaves the second start code in place for the next iteration
        }

        return results;
    }

    private int FindStartCode(int from, out int codeLength)
    {
        for (var i = from; i + 2 < _buffer.Count; i++)
        {
            if (_buffer[i] != 0 || _buffer[i + 1] != 0) continue;
            if (_buffer[i + 2] == 1) { codeLength = 3; return i; }
            if (i + 3 < _buffer.Count && _buffer[i + 2] == 0 && _buffer[i + 3] == 1) { codeLength = 4; return i; }
        }
        codeLength = 0;
        return -1;
    }
}
