using System.Net;
using System.Net.Sockets;
using Microsoft.Extensions.Logging;

namespace DeployCoreAgent;

/// <summary>
/// One instance per active session_id with mode "connect" (see
/// remote-agent/PROTOCOL.md section 1). Opens a loopback TCP connection to
/// the target's own RDP listener (TermService, enabled by
/// remote_agent_install.ps1's step 4) and pumps bytes bidirectionally
/// between that socket and the control channel's binary frames for this
/// session_id - a raw byte pipe, no protocol of its own. guacd (backend
/// side) speaks the entire Guacamole/RDP protocol; this class only moves
/// bytes.
/// </summary>
internal sealed class ConnectTunnel(string sessionId, ControlChannelClient controlChannel, ILogger<ConnectTunnel> logger)
    : IDisposable
{
    private const int RdpPort = 3389;

    private readonly CancellationTokenSource _cts = new();
    private TcpClient? _tcpClient;
    private int _disposed;

    public async Task StartAsync()
    {
        try
        {
            _tcpClient = new TcpClient();
            await _tcpClient.ConnectAsync(IPAddress.Loopback, RdpPort, _cts.Token);
            logger.LogInformation("Connect session {SessionId}: tunnel to 127.0.0.1:{Port} open.", sessionId, RdpPort);
            _ = PumpSocketToAgentAsync(); // long-running background loop for this session's lifetime
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Connect session {SessionId}: could not reach the local RDP listener.", sessionId);
            await SendSessionEndAsync();
        }
    }

    /// <summary>Binary frames arriving on the control channel for this
    /// session_id - written straight through to the local RDP socket,
    /// verbatim (per PROTOCOL.md: "no framing beyond that - it's a byte
    /// pipe, not a protocol").</summary>
    public async Task OnAgentBytesReceivedAsync(ReadOnlyMemory<byte> chunk)
    {
        var client = _tcpClient;
        if (client is not { Connected: true }) return;
        try
        {
            await client.GetStream().WriteAsync(chunk, _cts.Token);
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Connect session {SessionId}: write to local socket failed.", sessionId);
        }
    }

    private async Task PumpSocketToAgentAsync()
    {
        var buffer = new byte[65536];
        try
        {
            var stream = _tcpClient!.GetStream();
            while (!_cts.IsCancellationRequested)
            {
                var read = await stream.ReadAsync(buffer, _cts.Token);
                if (read == 0) break; // local RDP side closed cleanly
                await controlChannel.SendBinaryAsync(sessionId, buffer.AsMemory(0, read));
            }
        }
        catch (OperationCanceledException)
        {
            // expected on session_end/dispose
        }
        catch (Exception ex)
        {
            logger.LogDebug(ex, "Connect session {SessionId}: local socket read loop ended.", sessionId);
        }

        // The local socket closing (RDP session ended, TermService dropped
        // it, etc.) needs to reach the backend too, or its side of the
        // tunnel (guacd's own connection, bridged through the per-session
        // listener in managed_hosts.py) never finds out and just hangs -
        // see PROTOCOL.md's Connect-mode cleanup paragraph.
        //
        // Honesty note, read directly from backend/app/api/routes/remote_agent.py's
        // agent_control() receive loop at the time this was written: it only
        // branches on incoming "heartbeat" and "signal" message types from
        // the agent - there is no handler there for an agent-INITIATED
        // "session_end" today, so the backend currently just ignores this
        // message. Sending it anyway because it's what PROTOCOL.md specifies
        // and it's forward-compatible/harmless either way; flagged here so
        // it isn't mistaken for a bug in THIS file if a browser session
        // doesn't notice a dead tunnel promptly - the gap, if any, is
        // server-side.
        await SendSessionEndAsync();

        // Whether or not the server ever acts on the message above, this
        // agent still needs to stop routing binary frames to a tunnel whose
        // local socket is gone - remove it locally rather than depending on
        // a session_end coming back down to trigger that cleanup.
        controlChannel.RemoveConnectTunnel(sessionId);
        Dispose();
    }

    private async Task SendSessionEndAsync()
    {
        try
        {
            await controlChannel.SendJsonAsync(new { type = "session_end", session_id = sessionId });
        }
        catch
        {
            // control channel may already be down - session is ending either way
        }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _cts.Cancel();
        try
        {
            _tcpClient?.Close();
        }
        catch
        {
            // already closing/closed
        }
        _cts.Dispose();
    }
}
