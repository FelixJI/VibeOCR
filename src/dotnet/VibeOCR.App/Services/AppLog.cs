using System.Diagnostics;
using System.Text;

namespace VibeOCR.App.Services;

/// <summary>
/// Lightweight dev logger: writes to the Rider/VS Debug output window AND a
/// rotating log file so backend + frontend activity is visible during
/// development without a separate console. Dual-write so you can tail the file
/// and/or read it in the IDE.
/// </summary>
public static class AppLog
{
    private static readonly object _gate = new();
    private static string? _logPath;

    /// <summary>Initialize with a log directory (e.g. layout.DataRoot/logs).</summary>
    public static void Initialize(string logDirectory)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(logDirectory);
        Directory.CreateDirectory(logDirectory);
        _logPath = Path.Combine(logDirectory, $"winui-dev-{DateTime.Now:yyyyMMdd}.log");
    }

    public static void Info(string message) => Write("INFO ", message);
    public static void Warn(string message) => Write("WARN ", message);
    public static void Error(string message, Exception? ex = null) =>
        Write("ERROR", ex is null ? message : $"{message}:{Environment.NewLine}{ex}");

    private static void Write(string level, string message)
    {
        string line = $"{DateTime.Now:HH:mm:ss.fff} [{level}] {message}";
        // Rider / VS Debug output window.
        Debug.WriteLine(line);
        // File (best-effort).
        string? path = _logPath;
        if (path is null)
        {
            return;
        }

        try
        {
            lock (_gate)
            {
                File.AppendAllText(path, line + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch
        {
            // Logging must never crash the app.
        }
    }
}
