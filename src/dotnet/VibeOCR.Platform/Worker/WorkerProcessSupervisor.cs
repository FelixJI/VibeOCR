using System.Diagnostics;

namespace VibeOCR.Platform.Worker;

public sealed record WorkerProcessOptions(
    string FileName,
    IReadOnlyList<string> Arguments,
    string WorkingDirectory,
    string LogPath,
    long MaxLogBytes = 1 << 20);

public sealed class WorkerProcessSupervisor : IAsyncDisposable
{
    private readonly WorkerProcessOptions _options;
    private readonly object _logLock = new();
    private Process? _process;

    public WorkerProcessSupervisor(WorkerProcessOptions options)
    {
        _options = options ?? throw new ArgumentNullException(nameof(options));
        ArgumentException.ThrowIfNullOrWhiteSpace(options.FileName);
        ArgumentException.ThrowIfNullOrWhiteSpace(options.WorkingDirectory);
        ArgumentException.ThrowIfNullOrWhiteSpace(options.LogPath);
        if (options.MaxLogBytes <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(options), "MaxLogBytes must be positive.");
        }
    }

    public int RestartCount { get; private set; }

    public async Task<int> RunOnceAsync(CancellationToken cancellationToken)
    {
        if (_process is { HasExited: false })
        {
            throw new InvalidOperationException("Worker process is already running.");
        }

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

        var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, eventArgs) => AppendLog("stdout", eventArgs.Data);
        process.ErrorDataReceived += (_, eventArgs) => AppendLog("stderr", eventArgs.Data);
        if (!process.Start())
        {
            throw new InvalidOperationException("Failed to start WorkerHost process.");
        }

        _process = process;
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        process.WaitForExit();
        int exitCode = process.ExitCode;
        AppendLog("process", $"exit={exitCode}");
        process.Dispose();
        _process = null;
        return exitCode;
    }

    public async Task<int?> TryRestartReadOnlyAsync(CancellationToken cancellationToken)
    {
        if (RestartCount >= 1)
        {
            return null;
        }

        RestartCount++;
        return await RunOnceAsync(cancellationToken).ConfigureAwait(false);
    }

    private void AppendLog(string source, string? line)
    {
        if (line is null)
        {
            return;
        }

        lock (_logLock)
        {
            var info = new FileInfo(_options.LogPath);
            if (info.Exists && info.Length >= _options.MaxLogBytes)
            {
                File.Move(_options.LogPath, _options.LogPath + ".1", overwrite: true);
            }

            File.AppendAllText(
                _options.LogPath,
                $"{DateTimeOffset.UtcNow:O} [{source}] {line}{Environment.NewLine}");
        }
    }

    public async ValueTask DisposeAsync()
    {
        Process? process = _process;
        if (process is null)
        {
            return;
        }

        if (!process.HasExited)
        {
            process.Kill(entireProcessTree: true);
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            try
            {
                await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }

        process.Dispose();
        _process = null;
    }
}
