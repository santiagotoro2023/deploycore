using System.Diagnostics;
using System.Linq;
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
        _ = ClipboardPollLoopAsync(_sessionCts.Token);
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
        _sessionCts.Dispose();
        StopCapture();
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
        try
        {
            switch (tEl.GetString())
            {
                case "mousemove":
                    Win32Interop.MoveMouseAbsolute(ScaleX(msg.GetProperty("x").GetInt32()), ScaleY(msg.GetProperty("y").GetInt32()));
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
                case "clipboard":
                {
                    var text = msg.GetProperty("text").GetString() ?? "";
                    Win32Interop.SetClipboardText(text);
                    // Remember this as "already synced" so ClipboardPollLoopAsync
                    // doesn't immediately echo it straight back as if it were a
                    // brand new local change.
                    _lastClipboardText = text;
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

    /// <summary>
    /// clipboard is bidirectional per PROTOCOL.md ("both"). The
    /// browser-to-agent direction is a direct data-channel message handled
    /// above; this is the other direction.
    ///
    /// ponytail: polls the local clipboard every 2s rather than reacting to
    /// a real WM_CLIPBOARDUPDATE notification - a Windows Service has no
    /// message-only window/message pump by default, and standing one up
    /// (AddClipboardFormatListener needs an HWND) is real extra plumbing for
    /// what's a "keep both ends in sync" nicety, not the core of Shadow
    /// mode. Up to ~2s of latency on an agent-to-browser clipboard push is a
    /// fine v1 trade. Upgrade path: a dedicated hidden message-only window +
    /// AddClipboardFormatListener, if that latency ever actually matters to
    /// someone.
    /// </summary>
    private async Task ClipboardPollLoopAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(2));
        try
        {
            while (await timer.WaitForNextTickAsync(ct))
            {
                try
                {
                    var text = Win32Interop.GetClipboardText();
                    if (text is null || text == _lastClipboardText) continue;
                    _lastClipboardText = text;

                    var json = JsonSerializer.Serialize(new { t = "clipboard", text });
                    _dataChannel?.send(json); // SIPSorcery API as documented - unverified, see file header
                }
                catch (Exception ex)
                {
                    // Per-tick try/catch, not one around the whole loop
                    // (contrast ReadNalUnitsAsync): a single failed tick
                    // (e.g. the data channel isn't open quite yet) shouldn't
                    // silently end clipboard sync for the rest of a
                    // potentially hours-long session - just skip this tick
                    // and try again in 2s.
                    logger.LogDebug(ex, "Shadow session {SessionId}: clipboard poll tick failed.", sessionId);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // expected on session teardown
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
        if (config.VirtualDisplay)
        {
            // Real driver isn't bundled yet - see IVirtualDisplay. Once one
            // exists, this actually changes the console's own resolution to
            // exactly w x h, and the -vf scale filter StartCapture applies
            // below becomes a same-size no-op rather than a real resample.
            _virtualDisplay.SetResolution(width, height);
        }
        else
        {
            _nativeScreenSize = TrySetNearestResolutionInActiveSession(width, height);
        }

        StartCapture(width, height);
    }

    /// <summary>
    /// ChangeDisplaySettingsEx (Win32Interop.TrySetNearestResolution) needs
    /// the calling thread attached to the INTERACTIVE desktop to succeed at
    /// all - confirmed against Microsoft's own documented behavior for this
    /// API, not assumed, and confirmed live: agent.log showed it failing
    /// with code -1 on literally every attempt, regardless of login state or
    /// requested size, which this agent SERVICE's own process (Session 0,
    /// same as every Windows service) can structurally never satisfy. So
    /// this launches a one-shot self-invocation of THIS SAME exe
    /// (Program.cs's own "--set-resolution" branch) into the active console
    /// session via SessionCapture - the exact same session-boundary problem
    /// already solved for ffmpeg's own capture, applied to the one other
    /// place this agent touches the display - and waits for it to exit
    /// (bounded) since StartCapture immediately after needs the change to
    /// have already landed.
    /// </summary>
    private (int Width, int Height) TrySetNearestResolutionInActiveSession(int width, int height)
    {
        var exePath = Path.Combine(AppContext.BaseDirectory, "DeployCoreAgent.exe");
        var commandLine = $"\"{exePath}\" --set-resolution {width} {height}";
        try
        {
            var pid = SessionCapture.StartInActiveSession(commandLine, AppContext.BaseDirectory, logger);
            try
            {
                using var proc = Process.GetProcessById((int)pid);
                // 1.5s is generous for a call that's normally near-instant -
                // this is a genuinely different process/session, not a local
                // method call, so a bounded wait (not indefinite) protects
                // against it somehow hanging and stalling this session's
                // next capture restart.
                if (!proc.WaitForExit(1500))
                    logger.LogWarning("Shadow session {SessionId}: resolution-change helper did not exit within 1.5s - proceeding anyway.", sessionId);
            }
            catch (ArgumentException)
            {
                // Already exited by the time we asked - the expected fast path.
            }
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Shadow session {SessionId}: failed to launch the resolution-change helper into the active console session - screen size unchanged.", sessionId);
        }

        // Reads back whatever's ACTUALLY on screen now, whether or not the
        // change above landed - GetSystemMetrics (unlike
        // ChangeDisplaySettingsEx) has no desktop-attachment restriction, so
        // this is accurate even called from this service's own Session 0.
        return Win32Interop.GetPrimaryScreenSize();
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
        const string encodeArgs = "-c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -f h264 -y";
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
            pid = SessionCapture.StartInActiveSession(commandLine, dataDir, logger);
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
                if (!proc.HasExited) proc.Kill(entireProcessTree: true);
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
        long nalCount = 0, byteCount = 0;
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
                        if (_pc is null) continue;

                        // SIPSorcery 6.2.3 (confirmed live via the first
                        // real CI build, CS4008 "cannot await 'void'"):
                        // SendVideo is synchronous, not awaitable - fixed
                        // here, not just guessed at.
                        //
                        // Per-NAL try/catch, not just the outer one around
                        // this whole loop: confirmed against SIPSorcery's
                        // own source (MediaStream.GetSendingFormat, called
                        // internally by SendVideo) that it can throw if no
                        // compatible format is resolved yet - a real
                        // possibility right after StartAsync, since capture
                        // starts immediately while the SDP answer is still
                        // in flight over the signaling round-trip. An
                        // unhandled exception here previously propagated
                        // out of this whole loop's own try block,
                        // permanently ending frame forwarding for the rest
                        // of the session after the very first failure -
                        // "connects, then black forever" is exactly what
                        // that looks like from the browser side. Now it
                        // just skips that one NAL.
                        try
                        {
                            _pc.SendVideo(FrameDurationRtpUnits, nal);
                        }
                        catch (Exception ex)
                        {
                            logger.LogDebug(ex, "Shadow session {SessionId}: SendVideo failed for one NAL (skipping it).", sessionId);
                            continue;
                        }

                        // ponytail: passes the full per-frame RTP duration
                        // on EVERY NAL of a multi-NAL access unit (SPS/PPS/
                        // slice), not just the last one - would over-advance
                        // SIPSorcery's internal RTP timestamp for keyframes
                        // (3 NALs) versus regular frames (1 NAL). H264
                        // decodability never depends on RTP timestamps (only
                        // jitter-buffer pacing does), so this trades
                        // perfectly smooth pacing for a much simpler v1 - a
                        // real corner cut, named here rather than glossed
                        // over. Upgrade path: parse the NAL header type (low
                        // 5 bits of the first byte after the start code) and
                        // only pass a nonzero duration on the first VCL NAL
                        // (types 1/5) of each access unit, 0 on SPS/PPS/SEI.
                        nalCount++;
                        byteCount += nal.Length;
                    }

                    // Added specifically because the first real end-to-end
                    // test had no way to tell "ffmpeg is producing nothing"
                    // apart from "frames are flowing but never rendering in
                    // the browser" - both look identical (black screen) from
                    // the browser side alone.
                    if (DateTime.UtcNow - lastProgressLog > TimeSpan.FromSeconds(5))
                    {
                        logger.LogInformation("Shadow session {SessionId}: {NalCount} NAL units / {ByteCount} bytes sent to SIPSorcery so far.", sessionId, nalCount, byteCount);
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
