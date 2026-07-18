using System.Text;
using Microsoft.Extensions.Logging;

namespace DeployCoreAgent;

/// <summary>
/// A minimal file logger, added specifically because the first real
/// end-to-end test (Shadow black screen, Connect erroring) had no usable
/// diagnostics to go on beyond the install script's own transcript - this
/// agent's own Microsoft.Extensions.Hosting default logging goes to Console
/// (invisible - a Windows Service has no console) and, via
/// UseWindowsService(), the Windows Event Log (real, but awkward to search/
/// copy compared to a plain text file).
///
/// Hand-rolled rather than a third-party file-logging package (Serilog.Sinks.File,
/// NLog, etc.) deliberately: this project already got burned once by guessing
/// at a third-party library's exact API from general familiarity
/// (SIPSorcery's SendVideo/createOffer signatures - see ShadowSession.cs's own
/// header comment and this project's README) - a second unverified dependency
/// is a second way for CI to fail on something that isn't actually a logic
/// bug. This is ~40 lines of plain File.AppendAllText calls against BCL types
/// only, nothing to get wrong at the API-surface level.
/// </summary>
internal sealed class FileLogger(string categoryName, string path, object writeLock) : ILogger
{
    public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

    public bool IsEnabled(LogLevel logLevel) => logLevel >= LogLevel.Information;

    public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
    {
        if (!IsEnabled(logLevel)) return;

        var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} [{logLevel}] {categoryName}: {formatter(state, exception)}";
        if (exception is not null) line += Environment.NewLine + exception;

        lock (writeLock)
        {
            try
            {
                File.AppendAllText(path, line + Environment.NewLine, Encoding.UTF8);
            }
            catch
            {
                // The log file itself being briefly locked/unwritable must
                // never take down the agent - this is diagnostics, not a
                // load-bearing feature.
            }
        }
    }
}

internal sealed class FileLoggerProvider : ILoggerProvider
{
    private readonly string _path;
    private readonly object _writeLock = new();

    public FileLoggerProvider(string path)
    {
        _path = path;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        // Fresh file per service start, not an ever-growing one - this is a
        // "what happened on the last run" diagnostic tool, not an audit
        // trail; no rotation/retention logic needed for that.
        try
        {
            File.WriteAllText(_path, $"=== DeployCoreAgent starting {DateTime.Now:yyyy-MM-dd HH:mm:ss} ==={Environment.NewLine}");
        }
        catch
        {
            // Same reasoning as FileLogger.Log above - never fatal.
        }
    }

    public ILogger CreateLogger(string categoryName) => new FileLogger(categoryName, _path, _writeLock);

    public void Dispose() { }
}
