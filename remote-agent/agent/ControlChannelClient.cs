using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace DeployCoreAgent;

/// <summary>
/// The agent's one persistent outbound connection (see
/// remote-agent/PROTOCOL.md section 1) - authenticates at the HTTP upgrade
/// via X-Enroll-Token/X-Agent-Key headers (confirmed against the real
/// handler, backend/app/api/routes/remote_agent.py's <c>agent_control()</c>,
/// which reads exactly these two header names, lower-cased by ASGI/HTTP's
/// own case-insensitivity, before <c>accept()</c>), reconnects with backoff
/// forever (a managed host reboots, loses network, etc. with nobody around
/// to intervene), and dispatches session_start/session_end/signal (text)
/// and the 16-byte-session-id-prefixed RDP tunnel (binary) to the right
/// ShadowSession/ConnectTunnel.
/// </summary>
internal sealed class ControlChannelClient
{
    private static readonly TimeSpan[] BackoffSteps =
    {
        TimeSpan.FromSeconds(3), TimeSpan.FromSeconds(6), TimeSpan.FromSeconds(12), TimeSpan.FromSeconds(30),
    };

    private static readonly TimeSpan HeartbeatInterval = TimeSpan.FromSeconds(20);

    private readonly AgentConfig _config;
    private readonly ILoggerFactory _loggerFactory;
    private readonly ILogger<ControlChannelClient> _logger;
    private readonly Uri _controlUri;

    private readonly ConcurrentDictionary<string, ShadowSession> _shadowSessions = new();
    private readonly ConcurrentDictionary<string, ConnectTunnel> _connectTunnels = new();

    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private ClientWebSocket? _socket;

    public ControlChannelClient(AgentConfig config, ILoggerFactory loggerFactory)
    {
        _config = config;
        _loggerFactory = loggerFactory;
        _logger = loggerFactory.CreateLogger<ControlChannelClient>();

        // serverUrl is http(s) (the installer also uses it for plain REST
        // calls) - the control channel itself is a WebSocket, so swap the
        // scheme, matching PROTOCOL.md's own wss://{server}/... notation.
        var builder = new UriBuilder(config.ServerUrl)
        {
            Scheme = config.ServerUrl.StartsWith("https", StringComparison.OrdinalIgnoreCase) ? "wss" : "ws",
            Path = "/api/remote/agent-control",
        };
        _controlUri = builder.Uri;
    }

    /// <summary>Runs until <paramref name="ct"/> is cancelled (service
    /// shutdown) - reconnects with backoff on every drop in between.</summary>
    public async Task RunAsync(CancellationToken ct)
    {
        var backoffIndex = 0;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                await ConnectAndPumpAsync(ct);
                backoffIndex = 0; // a connection that came up and later dropped -> try promptly again, not from the top of the backoff
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Control channel connection failed or dropped.");
            }

            if (ct.IsCancellationRequested) break;

            var delay = BackoffSteps[Math.Min(backoffIndex, BackoffSteps.Length - 1)];
            backoffIndex++;
            _logger.LogInformation("Reconnecting to control channel in {Delay}.", delay);
            try
            {
                await Task.Delay(delay, ct);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private async Task ConnectAndPumpAsync(CancellationToken ct)
    {
        using var socket = new ClientWebSocket();
        // Must be set before ConnectAsync - the server authenticates at the
        // HTTP upgrade itself (remote_agent.py's agent_control(), before
        // websocket.accept()), not via a first message.
        socket.Options.SetRequestHeader("X-Enroll-Token", _config.EnrollToken);
        socket.Options.SetRequestHeader("X-Agent-Key", _config.AgentKey);

        await socket.ConnectAsync(_controlUri, ct);
        _socket = socket;
        _logger.LogInformation("Control channel connected to {Uri}.", _controlUri);

        using var heartbeatCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var heartbeatTask = HeartbeatLoopAsync(heartbeatCts.Token);

        try
        {
            await ReceiveLoopAsync(socket, ct);
        }
        finally
        {
            heartbeatCts.Cancel();
            try
            {
                await heartbeatTask;
            }
            catch (OperationCanceledException)
            {
                // expected
            }

            _socket = null;

            // The control channel is gone - Shadow's peer connections and
            // Connect's tunnels can't be told about session_end anymore
            // anyway (their signaling/byte-pump has no path left), so tear
            // them down locally rather than leaking them until reconnect.
            foreach (var kvp in _shadowSessions) kvp.Value.Stop();
            _shadowSessions.Clear();
            foreach (var kvp in _connectTunnels) kvp.Value.Dispose();
            _connectTunnels.Clear();
        }
    }

    private async Task HeartbeatLoopAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(HeartbeatInterval);
        try
        {
            while (await timer.WaitForNextTickAsync(ct))
                await SendJsonAsync(new { type = "heartbeat" });
        }
        catch (OperationCanceledException)
        {
            // expected on disconnect/shutdown
        }
    }

    private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken ct)
    {
        var buffer = new byte[65536];
        while (socket.State == WebSocketState.Open && !ct.IsCancellationRequested)
        {
            using var messageStream = new MemoryStream();
            WebSocketReceiveResult result;
            do
            {
                result = await socket.ReceiveAsync(buffer, ct);
                if (result.MessageType == WebSocketMessageType.Close) return;
                messageStream.Write(buffer, 0, result.Count);
            } while (!result.EndOfMessage);

            var payload = messageStream.ToArray();

            // Both branches are awaited (not fire-and-forget) deliberately:
            // this is the ONE receive loop for the whole control channel, so
            // awaiting keeps every session's messages (and Connect's binary
            // chunks, where byte ORDER matters) processed strictly in
            // arrival order with no risk of two dispatches racing each
            // other. Session startup and a socket write are both fast
            // (local loopback connect / local process spawn) so this costs
            // negligible latency for the next frame.
            if (result.MessageType == WebSocketMessageType.Text)
                await HandleTextMessageAsync(Encoding.UTF8.GetString(payload));
            else
                await HandleBinaryMessageAsync(payload);
        }
    }

    private async Task HandleTextMessageAsync(string json)
    {
        JsonElement message;
        try
        {
            message = JsonDocument.Parse(json).RootElement;
        }
        catch (JsonException ex)
        {
            _logger.LogWarning(ex, "Ignoring malformed control-channel text frame.");
            return;
        }

        if (!message.TryGetProperty("type", out var typeEl)) return;
        switch (typeEl.GetString())
        {
            case "session_start":
                await HandleSessionStartAsync(message);
                break;

            case "session_end":
            {
                var sessionId = message.GetProperty("session_id").GetString() ?? "";
                if (_shadowSessions.TryRemove(sessionId, out var session)) session.Stop();
                if (_connectTunnels.TryRemove(sessionId, out var tunnel)) tunnel.Dispose();
                break;
            }

            case "signal":
            {
                // Shadow only - SDP/ICE relay (see PROTOCOL.md section 1).
                var sessionId = message.GetProperty("session_id").GetString() ?? "";
                if (_shadowSessions.TryGetValue(sessionId, out var session)) session.HandleSignal(message);
                break;
            }
        }
    }

    private async Task HandleSessionStartAsync(JsonElement message)
    {
        var sessionId = message.GetProperty("session_id").GetString() ?? "";
        var mode = message.GetProperty("mode").GetString();

        if (mode == "shadow")
        {
            var session = new ShadowSession(sessionId, _config, this, _loggerFactory.CreateLogger<ShadowSession>());
            _shadowSessions[sessionId] = session;
            await session.StartAsync();
        }
        else if (mode == "connect")
        {
            var tunnel = new ConnectTunnel(sessionId, this, _loggerFactory.CreateLogger<ConnectTunnel>());
            _connectTunnels[sessionId] = tunnel;
            await tunnel.StartAsync();
        }
        else
        {
            _logger.LogWarning("session_start for {SessionId} had unrecognized mode {Mode} - ignoring.", sessionId, mode);
        }
    }

    private async Task HandleBinaryMessageAsync(byte[] payload)
    {
        // Connect mode only - Shadow never receives binary control-channel
        // frames (all its traffic is WebRTC, not this channel - see
        // PROTOCOL.md and remote_agent.py's own comment on the matching
        // server-side branch).
        if (payload.Length < 16) return;

        // session_id round-trips as a PLAIN hex string throughout this
        // protocol (Python's uuid.uuid4().hex on the server, then
        // bytes.fromhex(...) for the binary prefix, then .hex() to read it
        // back - see managed_hosts.py/remote_agent.py). Deliberately NOT
        // using System.Guid here: Guid.Parse(hex).ToByteArray() would
        // reorder the first three fields via .NET's mixed-endian GUID byte
        // layout, producing DIFFERENT bytes than Python's straightforward
        // bytes.fromhex() for the exact same hex string. Convert.ToHexString/
        // FromHexString do a plain byte-for-byte hex codec with no such
        // reinterpretation, which is what actually matches the server.
        var sessionId = Convert.ToHexString(payload, 0, 16).ToLowerInvariant();
        if (_connectTunnels.TryGetValue(sessionId, out var tunnel))
            await tunnel.OnAgentBytesReceivedAsync(payload.AsMemory(16));
    }

    /// <summary>Removes a Connect tunnel this client no longer needs to
    /// route binary frames to. Called by ConnectTunnel itself when its local
    /// socket closes (see that class - the server doesn't reliably tell us
    /// to clean up in that direction today), in addition to the normal
    /// session_end path above.</summary>
    internal void RemoveConnectTunnel(string sessionId) => _connectTunnels.TryRemove(sessionId, out _);

    public async Task SendJsonAsync(object message)
    {
        var json = JsonSerializer.Serialize(message);
        await SendAsync(Encoding.UTF8.GetBytes(json), WebSocketMessageType.Text);
    }

    public async Task SendBinaryAsync(string sessionId, ReadOnlyMemory<byte> chunk)
    {
        var sessionIdBytes = Convert.FromHexString(sessionId); // see HandleBinaryMessageAsync's comment - plain hex codec, not System.Guid
        var framed = new byte[16 + chunk.Length];
        sessionIdBytes.CopyTo(framed.AsSpan(0));
        chunk.Span.CopyTo(framed.AsSpan(16));
        await SendAsync(framed, WebSocketMessageType.Binary);
    }

    private async Task SendAsync(byte[] payload, WebSocketMessageType type)
    {
        var socket = _socket;
        if (socket is null || socket.State != WebSocketState.Open) return;

        // ClientWebSocket allows only one send in flight at a time - this
        // lock is real synchronization for genuinely concurrent callers (the
        // heartbeat timer, plus every active Shadow/Connect session), not
        // speculative.
        await _sendLock.WaitAsync();
        try
        {
            await socket.SendAsync(payload, type, endOfMessage: true, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Send over control channel failed (connection likely dropping).");
        }
        finally
        {
            _sendLock.Release();
        }
    }
}
