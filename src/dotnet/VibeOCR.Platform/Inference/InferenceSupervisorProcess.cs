// Phase 7B: supervisor child-process owner for WinUI.
//
// Reuses the WorkerProcessSupervisor conventions (process startup, log file
// rotation, no-shell-execute) and adds the v2 specifics:
//   * the child binds 127.0.0.1:0 itself and reports the chosen port back via
//     the first stdout line (ready envelope) — no port-selection race;
//   * the parent generates the 256-bit session token and passes it via the
//     inherited VIBEOCR_SUP_TOKEN env var — never on argv/stdout/logs;
//   * on disposal the whole process tree is terminated.
//
// Production wiring spawns `python -m vibeocr.supervisor.main`. Tests inject
// an alternate FileName (e.g. a fake script) and read the ready line back.
using System.Diagnostics;
using System.Text.Json;

namespace VibeOCR.Platform.Inference;

/// <summary>Ready envelope emitted by the supervisor on its first stdout line.</summary>
public sealed record SupervisorReadyEnvelope(int Pid, int Port, string InstanceId, int ProtocolVersion, int SchemaVersion)
{
    public Uri BaseUrl => new($"http://127.0.0.1:{Port}");

    public static SupervisorReadyEnvelope Parse(string line)
    {
        using JsonDocument doc = JsonDocument.Parse(line);
        JsonElement root = doc.RootElement;
        return new SupervisorReadyEnvelope(
            Pid: root.GetProperty("pid").GetInt32(),
            Port: root.GetProperty("port").GetInt32(),
            InstanceId: root.GetProperty("instance_id").GetString()!,
            ProtocolVersion: root.GetProperty("protocol_version").GetInt32(),
            SchemaVersion: root.GetProperty("schema_version").GetInt32());
    }
}

/// <summary>Options for launching the supervisor child process.</summary>
public sealed record InferenceSupervisorOptions(
    string FileName,
    IReadOnlyList<string> Arguments,
    string WorkingDirectory,
    string LogPath,
    TimeSpan StartupTimeout);

/// <summary>
/// Owns the lifecycle of one supervisor child process. The supervisor binds its
/// own loopback socket; this class reads the ready envelope and exposes the
/// base URL + session token to the client.
/// </summary>
public sealed class InferenceSupervisorProcess : IDisposable
{
    private readonly InferenceSupervisorOptions _options;
    private readonly string _sessionToken;
    private Process? _process;
    private SupervisorReadyEnvelope? _ready;
    private readonly object _logLock = new();
    private readonly List<string> _logLines = new();

    public InferenceSupervisorProcess(InferenceSupervisorOptions options, string sessionToken)
    {
        _options = options ?? throw new ArgumentNullException(nameof(options));
        ArgumentException.ThrowIfNullOrWhiteSpace(sessionToken);
        _sessionToken = sessionToken;
    }

    /// <summary>The parsed ready envelope (valid after <see cref="StartAsync"/> succeeds).</summary>
    public SupervisorReadyEnvelope Ready
        => _ready ?? throw new InvalidOperationException("Supervisor has not started.");

    /// <summary>The session token to pass to <see cref="InferenceHttpClient"/>.</summary>
    public string SessionToken => _sessionToken;

    /// <summary>A snapshot of captured child log lines.</summary>
    public IReadOnlyList<string> LogLines
    {
        get
        {
            lock (_logLock)
            {
                return _logLines.ToArray();
            }
        }
    }

    /// <summary>Launch the child and await its ready envelope.</summary>
    public async Task<SupervisorReadyEnvelope> StartAsync(CancellationToken cancellationToken = default)
    {
        if (_process is { HasExited: false })
        {
            throw new InvalidOperationException("Supervisor process is already running.");
        }

        Directory.CreateDirectory(Path.GetDirectoryName(_options.LogPath)!);
        var startInfo = new ProcessStartInfo
        {
            FileName = _options.FileName,
            WorkingDirectory = _options.WorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            // Merge stderr into stdout so a single drain keeps both pipes from
            // filling and deadlocking the child.
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (string argument in _options.Arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }
        // Token via env — never on argv or in the ready envelope.
        startInfo.Environment["VIBEOCR_SUP_TOKEN"] = _sessionToken;

        var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        process.ErrorDataReceived += (_, e) => AppendLog("stderr", e.Data);
        if (!process.Start())
        {
            throw new InvalidOperationException("Failed to start supervisor process.");
        }

        _process = process;
        // Read the first stdout line synchronously (it is the ready envelope).
        // Subsequent stdout is log text; drain stderr asynchronously.
        process.BeginErrorReadLine();
        try
        {
            string firstLine = await process.StandardOutput.ReadLineAsync(cancellationToken)
                .ConfigureAwait(false) ?? string.Empty;
            _ready = SupervisorReadyEnvelope.Parse(firstLine);
            // Drain remaining stdout to a background task so the pipe does not block.
            _ = Task.Run(() => DrainStdoutAsync(process), CancellationToken.None);
            return _ready;
        }
        catch (OperationCanceledException)
        {
            Terminate();
            throw;
        }
        catch (Exception)
        {
            Terminate();
            throw;
        }
    }

    private async Task DrainStdoutAsync(Process process)
    {
        try
        {
            // ReadLineAsync returns null at EOF; avoid EndOfStream which is a
            // blocking poll flagged by CA2024 in async methods.
            string? line;
            while ((line = await process.StandardOutput.ReadLineAsync().ConfigureAwait(false)) is not null)
            {
                AppendLog("stdout", line);
            }
        }
        catch
        {
            // Best-effort drain; process exit will end this.
        }
    }

    /// <summary>Terminate the supervisor child (and whole tree on Windows).</summary>
    public void Dispose() => Terminate();

    private void Terminate()
    {
        if (_process is null)
        {
            return;
        }

        try
        {
            if (!_process.HasExited)
            {
                _process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // Best-effort.
        }

        _process.Dispose();
        _process = null;
    }

    private void AppendLog(string channel, string? line)
    {
        if (string.IsNullOrEmpty(line))
        {
            return;
        }

        lock (_logLock)
        {
            _logLines.Add($"[{channel}] {line}");
        }
    }
}
