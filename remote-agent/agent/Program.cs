using DeployCoreAgent;
using Microsoft.Extensions.DependencyInjection; // AddHostedService<T>() - IServiceCollection extension, lives in this namespace regardless of who calls it
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Hosting.WindowsServices; // UseWindowsService() - belt-and-suspenders alongside Microsoft.Extensions.Hosting, in case the extension method itself resolves from this namespace instead
using Microsoft.Extensions.Logging;

// One-shot CLI mode, not the normal service entry point: ShadowSession
// launches THIS SAME exe with these two args (via SessionCapture, see that
// class's own doc comment) instead of calling ChangeDisplaySettingsEx
// in-process, because that API requires the CALLING THREAD to be attached
// to the interactive desktop to succeed at all - confirmed against
// Microsoft's own documented behavior for it, not assumed, and confirmed
// live: agent.log showed ChangeDisplaySettingsEx failing with code -1
// (DISP_CHANGE_FAILED) on literally every attempt, before AND after login,
// at every resolution tried - the exact fingerprint of this restriction,
// not a per-mode compatibility problem. The agent SERVICE's own process,
// like every Windows service, runs on a non-interactive window station in
// Session 0, so it can never satisfy that requirement regardless of login
// state. A child launched via SessionCapture.StartInActiveSession, by
// contrast, has its desktop assignment set correctly from the moment it's
// created (that's the same fix already applied to ffmpeg's own capture) -
// so doing the ACTUAL ChangeDisplaySettingsEx call from inside a process
// launched that way is what makes it work at all. Exits immediately either
// way - never reaches the normal service bootstrap below.
if (args is ["--set-resolution", var widthArg, var heightArg]
    && int.TryParse(widthArg, out var targetWidth) && int.TryParse(heightArg, out var targetHeight))
{
    using var oneShotLoggerFactory = LoggerFactory.Create(builder => builder.AddProvider(new FileLoggerProvider(
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "DeployCore", "agent.log"))));
    Win32Interop.TrySetNearestResolution(targetWidth, targetHeight, oneShotLoggerFactory.CreateLogger("Win32Interop"));
    return;
}

// A modern .NET Windows Service: Microsoft.Extensions.Hosting +
// Microsoft.Extensions.Hosting.WindowsServices already solve "run a
// cancellable background loop under the Service Control Manager" for free
// (start/stop signals, an EventLog logger provider when actually running as
// a service, graceful shutdown) - see AgentWorker below for the one
// BackgroundService this whole agent is. No hand-rolled ServiceBase.
IHost host = Host.CreateDefaultBuilder(args)
    .UseWindowsService(options =>
    {
        // Cosmetic (event-log source naming) - the actual SCM service
        // identity is whatever remote_agent_install.ps1's `New-Service -Name`
        // registered ("DeployCoreRemoteAgent"), fixed independently of this.
        options.ServiceName = "DeployCoreRemoteAgent";
    })
    .ConfigureLogging(logging => logging.AddProvider(new FileLoggerProvider(
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "DeployCore", "agent.log"))))
    .ConfigureServices((_, services) => services.AddHostedService<AgentWorker>())
    .Build();

await host.RunAsync();

// Top-level statements above must live in the compiler-generated global
// Program class/global namespace - everything else in this project lives in
// namespace DeployCoreAgent, so AgentWorker is wrapped in an explicit block
// (rather than a file-scoped `namespace DeployCoreAgent;`, which can't
// follow top-level statements in the same file) purely for that consistency.
namespace DeployCoreAgent
{
    /// <summary>
    /// The entire agent is one BackgroundService: load the config, make sure
    /// SendSAS will work, then hand off to ControlChannelClient's own
    /// reconnect-forever loop for the rest of this process's life. See this
    /// project's README for the config file contract and what's genuinely
    /// unverified below the surface of this file.
    /// </summary>
    internal sealed class AgentWorker(ILogger<AgentWorker> logger, ILoggerFactory loggerFactory) : BackgroundService
    {
        // Matches "$ConfigPath = Join-Path $ConfigDir agent-config.json" where
        // "$ConfigDir = $env:ProgramData\DeployCore" in remote_agent_install.ps1
        // exactly - SpecialFolder.CommonApplicationData resolves to the same
        // %ProgramData% the installer used, without hardcoding a "C:\" drive
        // letter.
        private static readonly string ConfigPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "DeployCore", "agent-config.json");

        protected override async Task ExecuteAsync(CancellationToken stoppingToken)
        {
            logger.LogInformation("DeployCore Remote Management Agent starting.");

            // Machine-wide policy, not per-session state - see Win32Interop's own
            // doc-comment on why this runs once here rather than lazily inside
            // ShadowSession on the first "cad" message.
            Win32Interop.EnsureSoftwareSasGeneration(loggerFactory.CreateLogger("Win32Interop"));

            // Same reasoning, same "ensure once at startup" pattern - see
            // SessionCapture's own doc comment for why Shadow's capture
            // needs both of these (Session 0 isolation, and stealing
            // winlogon.exe's/explorer.exe's own token across a session
            // boundary).
            var sessionCaptureLogger = loggerFactory.CreateLogger("SessionCapture");
            SessionCapture.EnsureTcbPrivilege(sessionCaptureLogger);
            SessionCapture.EnsureDebugPrivilege(sessionCaptureLogger);

            AgentConfig config;
            try
            {
                config = AgentConfig.LoadAndProtect(ConfigPath, loggerFactory.CreateLogger("AgentConfig"));
            }
            catch (Exception ex)
            {
                logger.LogCritical(ex, "Could not load {ConfigPath} - the installer should have written this file. Exiting.", ConfigPath);
                return;
            }

            var client = new ControlChannelClient(config, loggerFactory);
            await client.RunAsync(stoppingToken);

            logger.LogInformation("DeployCore Remote Management Agent stopping.");
        }
    }
}
