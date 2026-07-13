using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class WorkerProcessSupervisorTests
{
    [Fact]
    public async Task CapturesNonZeroExitAndBothDiagnosticStreams()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-supervisor-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        string log = Path.Combine(root, "worker.log");
        var options = new WorkerProcessOptions(
            "cmd.exe",
            ["/d", "/c", "echo stdout-line & echo stderr-line 1>&2 & exit /b 7"],
            root,
            log);
        await using var supervisor = new WorkerProcessSupervisor(options);

        int exitCode = await supervisor.RunOnceAsync(TestContext.Current.CancellationToken);

        Assert.Equal(7, exitCode);
        string diagnostics = await File.ReadAllTextAsync(
            log,
            TestContext.Current.CancellationToken);
        Assert.Contains("stdout-line", diagnostics);
        Assert.Contains("stderr-line", diagnostics);
    }

    [Fact]
    public async Task ReadOnlyRestartIsLimitedToOneAttempt()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-restart-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var options = new WorkerProcessOptions(
            "cmd.exe",
            ["/d", "/c", "exit /b 3"],
            root,
            Path.Combine(root, "worker.log"));
        await using var supervisor = new WorkerProcessSupervisor(options);

        Assert.Equal(3, await supervisor.TryRestartReadOnlyAsync(TestContext.Current.CancellationToken));
        Assert.Null(await supervisor.TryRestartReadOnlyAsync(TestContext.Current.CancellationToken));
        Assert.Equal(1, supervisor.RestartCount);
    }
}
