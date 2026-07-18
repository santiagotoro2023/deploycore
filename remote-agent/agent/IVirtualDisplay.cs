using Microsoft.Extensions.Logging;

namespace DeployCoreAgent;

/// <summary>
/// Seam for a real Indirect Display Driver (IDD) virtual monitor - see
/// docs/remote-agent-native-plan.md section 1 ("IDD driver") and
/// remote_agent_install.ps1's own <c>Install-VirtualDisplayDriver</c> stub,
/// which is the matching install-side placeholder (it always returns
/// <c>virtualDisplay: false</c> today, since no driver is bundled yet). A
/// real implementation would ask the driver's virtual monitor to switch to
/// EXACTLY width x height - no "nearest supported mode" heuristic, because a
/// driver DeployCore bundles would define its own mode list.
///
/// Created now, with exactly one no-op/log-only implementation, so
/// ShadowSession's resize handling has a stable seam to call through rather
/// than an inline <c>if (config.VirtualDisplay)</c> with nothing on the
/// other side of it. Not a speculative abstraction - the resize handler
/// needs *something* to call when virtualDisplay is true, and this is it,
/// deliberately doing nothing until a real driver is chosen (see that plan
/// doc's Phase 4 and its two named driver candidates).
/// </summary>
internal interface IVirtualDisplay
{
    void SetResolution(int width, int height);
}

/// <summary>
/// The only implementation today - and, since remote_agent_install.ps1
/// never actually sets <c>virtualDisplay: true</c> yet, one that in
/// practice never even gets called. If it ever is, it just logs: no driver
/// exists to actually change the console's resolution, so ShadowSession
/// still falls back to view-only scaling (ffmpeg's <c>-vf scale=w:h</c>
/// against the real, unchanged desktop resolution) exactly as if
/// virtualDisplay were false.
/// </summary>
internal sealed class NoOpVirtualDisplay(ILogger logger) : IVirtualDisplay
{
    public void SetResolution(int width, int height) =>
        logger.LogInformation("IVirtualDisplay.SetResolution({Width}, {Height}) called - no driver bundled yet, no-op.", width, height);
}
