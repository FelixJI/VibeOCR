// Supervisor child-process owner for WinUI.
//
// Applies the process startup, logging, and no-shell-execute conventions with
// the v2-specific guarantees:
//   * the child binds 127.0.0.1:0 itself and reports the chosen port back via
//     the first stdout line (ready envelope) — no port-selection race;
//   * the parent generates the 256-bit session token and passes it via the
//     inherited VIBEOCR_SUP_TOKEN env var — never on argv/stdout/logs;
//   * on disposal the whole process tree is terminated.
//
// Production wiring spawns `python -m vibeocr.backend.supervisor.main`. Tests inject
// an alternate FileName (e.g. a fake script) and read the ready line back.
using System.Diagnostics;
using System.Text.Json;
using VibeOCR.Runtime.Contracts.Generated;

namespace VibeOCR.Platform.Inference;

/// <summary>Ready envelope emitted by the supervisor on its first stdout line.</summary>
public sealed record SupervisorReadyEnvelope(
    int Pid,
    int Port,
    string InstanceId,
    int ProtocolVersion,
    int SchemaVersion,
    int ReadyVersion,
    IReadOnlyList<string> Capabilities)
{
    public Uri BaseUrl => new($"http://127.0.0.1:{Port}");

    public static SupervisorReadyEnvelope Parse(string line)
    {
        RuntimeReadyEnvelope? wire = JsonSerializer.Deserialize<RuntimeReadyEnvelope>(line);
        if (wire is null || !wire.Ready)
        {
            throw new InvalidDataException("Supervisor did not emit a ready envelope.");
        }

        var envelope = new SupervisorReadyEnvelope(
            Pid: wire.Pid,
            Port: wire.Port,
            InstanceId: wire.InstanceId,
            ProtocolVersion: wire.ProtocolVersion,
            SchemaVersion: wire.SchemaVersion,
            ReadyVersion: wire.ReadyVersion,
            Capabilities: wire.Capabilities);
        if (envelope.Pid <= 0
            || envelope.Port is <= 0 or > 65535
            || string.IsNullOrWhiteSpace(envelope.InstanceId))
        {
            throw new InvalidDataException("Supervisor ready envelope contains invalid identity.");
        }
        if (envelope.ProtocolVersion != RuntimeProtocol.ProtocolVersion
            || envelope.SchemaVersion != RuntimeProtocol.SchemaVersion
            || envelope.ReadyVersion != RuntimeProtocol.ReadyEnvelopeVersion)
        {
            throw new InvalidDataException(
                $"Supervisor protocol/schema mismatch: "
                + $"{envelope.ProtocolVersion}/{envelope.SchemaVersion}.");
        }
        if (envelope.Capabilities is null
            || envelope.Capabilities.Count != envelope.Capabilities.Distinct().Count()
            || envelope.Capabilities.Any(
                capability => !RuntimeProtocol.AllCapabilities.Contains(capability)))
        {
            throw new InvalidDataException("Supervisor ready envelope contains invalid capabilities.");
        }
        string[] missingCapabilities = RuntimeProtocol.AllCapabilities
            .Except(envelope.Capabilities, StringComparer.Ordinal)
            .ToArray();
        if (missingCapabilities.Length > 0)
        {
            throw new InvalidDataException(
                "Supervisor does not provide required Next capabilities: "
                + string.Join(", ", missingCapabilities));
        }
        return envelope;
    }
}

/// <summary>Options for launching the supervisor child process.</summary>
public sealed record InferenceSupervisorOptions(
    string FileName,
    IReadOnlyList<string> Arguments,
    string WorkingDirectory,
    string LogPath,
    TimeSpan StartupTimeout,
    IReadOnlyDictionary<string, string>? EnvironmentOverrides = null);

/// <summary>Details for an unplanned supervisor-process exit.</summary>
public sealed record SupervisorUnexpectedExitEventArgs(int? ExitCode);

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
    private WindowsJobObject? _jobObject;
    private SupervisorReadyEnvelope? _ready;
    private readonly object _lifecycleLock = new();
    private bool _startAttempted;
    private bool _disposed;
    private bool _terminationRequested;
    private readonly object _logLock = new();
    private readonly List<string> _logLines = new();

    public InferenceSupervisorProcess(InferenceSupervisorOptions options, string sessionToken)
    {
        _options = options ?? throw new ArgumentNullException(nameof(options));
        ArgumentException.ThrowIfNullOrWhiteSpace(sessionToken);
        _sessionToken = sessionToken;
    }

    /// <summary>
    /// Raised when the child exits without this owner requesting termination.
    /// Consumers can create a fresh one-shot owner and reconnect their clients.
    /// </summary>
    public event EventHandler<SupervisorUnexpectedExitEventArgs>? UnexpectedExit;

    /// <summary>The parsed ready envelope (valid after <see cref="StartAsync"/> succeeds).</summary>
    public SupervisorReadyEnvelope Ready
        => Volatile.Read(ref _ready)
            ?? throw new InvalidOperationException("Supervisor has not started.");

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

    /// <summary>
    /// Launch the child and await its ready envelope. Each owner permits exactly
    /// one launch attempt, including attempts that fail or are cancelled.
    /// </summary>
    public async Task<SupervisorReadyEnvelope> StartAsync(CancellationToken cancellationToken = default)
    {
        Process process;
        lock (_lifecycleLock)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            if (_startAttempted)
            {
                throw new InvalidOperationException(
                    "InferenceSupervisorProcess permits only one launch attempt.");
            }
            _startAttempted = true;

            Directory.CreateDirectory(Path.GetDirectoryName(_options.LogPath)!);
            var startInfo = new ProcessStartInfo
            {
                FileName = _options.FileName,
                WorkingDirectory = _options.WorkingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            foreach (string argument in _options.Arguments)
            {
                startInfo.ArgumentList.Add(argument);
            }
            // Token via env — never on argv or in the ready envelope.
            startInfo.Environment["VIBEOCR_SUP_TOKEN"] = _sessionToken;
            if (_options.EnvironmentOverrides is not null)
            {
                foreach ((string name, string value) in _options.EnvironmentOverrides)
                {
                    if (string.Equals(name, "VIBEOCR_SUP_TOKEN", StringComparison.OrdinalIgnoreCase))
                    {
                        throw new ArgumentException(
                            "Environment overrides cannot replace VIBEOCR_SUP_TOKEN.",
                            nameof(_options));
                    }
                    startInfo.Environment[name] = value;
                }
            }

            process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            process.ErrorDataReceived += (_, e) => AppendLog("stderr", e.Data);
            process.Exited += OnProcessExited;
            try
            {
                if (!process.Start())
                {
                    throw new InvalidOperationException("Failed to start supervisor process.");
                }
            }
            catch
            {
                process.Dispose();
                throw;
            }

            _process = process;
            try
            {
                _jobObject = new WindowsJobObject();
                _jobObject.Assign(process);
            }
            catch
            {
                Terminate();
                throw;
            }
        }
        // Read the first stdout line synchronously (it is the ready envelope).
        // Subsequent stdout is log text; drain stderr asynchronously.
        process.BeginErrorReadLine();
        try
        {
            using var startup = CancellationTokenSource.CreateLinkedTokenSource(
                cancellationToken);
            startup.CancelAfter(_options.StartupTimeout);
            string firstLine = await process.StandardOutput.ReadLineAsync(startup.Token)
                .ConfigureAwait(false) ?? string.Empty;
            SupervisorReadyEnvelope ready = SupervisorReadyEnvelope.Parse(firstLine);
            lock (_lifecycleLock)
            {
                ObjectDisposedException.ThrowIf(_disposed, this);
                _ready = ready;
            }
            // Drain remaining stdout to a background task so the pipe does not block.
            _ = Task.Run(() => DrainStdoutAsync(process), CancellationToken.None);
            return ready;
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            Terminate();
            throw new TimeoutException(
                $"Supervisor did not become ready within {_options.StartupTimeout}.");
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
    public void Dispose()
    {
        lock (_lifecycleLock)
        {
            if (_disposed)
            {
                return;
            }
            _disposed = true;
        }
        Terminate();
        GC.SuppressFinalize(this);
    }

    private void Terminate()
    {
        Process? process;
        WindowsJobObject? jobObject;
        lock (_lifecycleLock)
        {
            _terminationRequested = true;
            _ready = null;
            process = _process;
            _process = null;
            jobObject = _jobObject;
            _jobObject = null;
        }

        try
        {
            if (process is not null && !process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // Best-effort.
        }

        jobObject?.Dispose();
        if (process is not null)
        {
            try
            {
                process.WaitForExit(milliseconds: 5_000);
            }
            catch
            {
                // Best-effort.
            }
            process.Dispose();
        }
    }

    private void OnProcessExited(object? sender, EventArgs eventArgs)
    {
        if (sender is not Process process)
        {
            return;
        }

        bool notify;
        lock (_lifecycleLock)
        {
            notify = !_disposed && !_terminationRequested && ReferenceEquals(_process, process);
            if (notify)
            {
                _ready = null;
            }
        }
        if (!notify)
        {
            return;
        }

        int? exitCode = null;
        try
        {
            exitCode = process.ExitCode;
        }
        catch (InvalidOperationException)
        {
            // The exit notification itself is authoritative; the code is optional.
        }
        UnexpectedExit?.Invoke(this, new SupervisorUnexpectedExitEventArgs(exitCode));
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
        try
        {
            File.AppendAllText(
                _options.LogPath,
                $"[{DateTimeOffset.Now:O}] [{channel}] {line}{Environment.NewLine}");
        }
        catch
        {
            // Diagnostics must not destabilize the process owner.
        }
    }
}
