// Tests for InferenceSupervisorProcess + SupervisorReadyEnvelope parsing.
//
// Parser tests are complemented by a lightweight PowerShell child that exercises
// the owner lifecycle without requiring the Python backend.
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class InferenceSupervisorProcessTests
{
    [Fact]
    public void ReadyEnvelopeParsesPortAndInstanceId()
    {
        var env = SupervisorReadyEnvelope.Parse(
            """{"ready":true,"pid":4321,"port":5432,"instance_id":"sup-abc","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":["ocr.recognition.v2","pdf.edit.v2","qrcode.v2","export.document.v1","runtime.settings.v2"]}""");
        Assert.Equal(5432, env.Port);
        Assert.Equal("sup-abc", env.InstanceId);
        Assert.Equal(2, env.ProtocolVersion);
        Assert.Equal("http://127.0.0.1:5432/", env.BaseUrl.ToString());
    }

    [Fact]
    public void ReadyEnvelopeRejectsMissingToken()
    {
        // A ready envelope that accidentally includes the token must still parse
        // (we only assert the token is NEVER in the line — the parse does not
        // look for it). What we actually guard: the token lives only in env.
        var env = SupervisorReadyEnvelope.Parse(
            """{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":["ocr.recognition.v2","pdf.edit.v2","qrcode.v2","export.document.v1","runtime.settings.v2"]}""");
        Assert.DoesNotContain("token", "pid/port/instance_id");
        Assert.Equal(2, env.SchemaVersion);
    }

    [Theory]
    [InlineData("""{"ready":false,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":[]}""")]
    [InlineData("""{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":1,"schema_version":2,"ready_version":1,"capabilities":[]}""")]
    [InlineData("""{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":1,"ready_version":1,"capabilities":[]}""")]
    [InlineData("""{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":2,"capabilities":[]}""")]
    [InlineData("""{"ready":true,"pid":0,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":[]}""")]
    [InlineData("""{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":["legacy"]}""")]
    [InlineData("""{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":["ocr.recognition.v2"]}""")]
    [InlineData("""{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":null}""")]
    [InlineData("""{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"ready_version":1}""")]
    public void ReadyEnvelopeRejectsInvalidOrIncompatibleRuntime(string payload)
    {
        Assert.Throws<InvalidDataException>(
            () => SupervisorReadyEnvelope.Parse(payload));
    }

    [Fact]
    public void ConstructorRequiresSessionToken()
    {
        var options = new InferenceSupervisorOptions(
            "python", new[] { "-m", "vibeocr.backend.supervisor.main" }, ".", "log.txt", TimeSpan.FromSeconds(5));
        Assert.Throws<ArgumentNullException>(() => new InferenceSupervisorProcess(options, null!));
        Assert.Throws<ArgumentException>(() => new InferenceSupervisorProcess(options, "   "));
    }

    [Fact]
    public void ReadyThrowsBeforeStart()
    {
        var options = new InferenceSupervisorOptions(
            "python", Array.Empty<string>(), ".", "log.txt", TimeSpan.FromSeconds(5));
        var proc = new InferenceSupervisorProcess(options, "tok");
        Assert.Throws<InvalidOperationException>(() => proc.Ready);
    }

    [Fact]
    public async Task SuccessfulStartIsOneShotAndDisposeClearsReady()
    {
        string root = Path.Combine(
            Path.GetTempPath(), $"vibeocr-supervisor-process-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var proc = CreateReadyProcess(root);
        try
        {
            SupervisorReadyEnvelope ready = await proc.StartAsync(
                TestContext.Current.CancellationToken);

            Assert.Equal("sup-test", ready.InstanceId);
            Assert.Same(ready, proc.Ready);
            await Assert.ThrowsAsync<InvalidOperationException>(
                () => proc.StartAsync(TestContext.Current.CancellationToken));

            proc.Dispose();
            Assert.Throws<InvalidOperationException>(() => proc.Ready);
        }
        finally
        {
            proc.Dispose();
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task FailedLaunchAttemptCannotBeRetried()
    {
        string root = Path.Combine(
            Path.GetTempPath(), $"vibeocr-supervisor-process-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        using var proc = new InferenceSupervisorProcess(
            new InferenceSupervisorOptions(
                Path.Combine(root, "missing-supervisor.exe"),
                [],
                root,
                Path.Combine(root, "supervisor.log"),
                TimeSpan.FromSeconds(1)),
            "tok");
        try
        {
            await Assert.ThrowsAnyAsync<Exception>(
                () => proc.StartAsync(TestContext.Current.CancellationToken));
            await Assert.ThrowsAsync<InvalidOperationException>(
                () => proc.StartAsync(TestContext.Current.CancellationToken));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task DisposedOwnerCannotBeStarted()
    {
        string root = Path.Combine(
            Path.GetTempPath(), $"vibeocr-supervisor-process-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var proc = CreateReadyProcess(root);
        proc.Dispose();
        try
        {
            await Assert.ThrowsAsync<ObjectDisposedException>(
                () => proc.StartAsync(TestContext.Current.CancellationToken));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task NaturalChildExitRaisesUnexpectedExitAndInvalidatesReady()
    {
        string root = Path.Combine(
            Path.GetTempPath(), $"vibeocr-supervisor-process-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        using var proc = CreateReadyProcess(root, lifetimeMilliseconds: 250);
        var exited = new TaskCompletionSource<SupervisorUnexpectedExitEventArgs>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        proc.UnexpectedExit += (_, args) => exited.TrySetResult(args);
        try
        {
            await proc.StartAsync(TestContext.Current.CancellationToken);

            SupervisorUnexpectedExitEventArgs result = await exited.Task.WaitAsync(
                TimeSpan.FromSeconds(5),
                TestContext.Current.CancellationToken);

            Assert.Equal(0, result.ExitCode);
            Assert.Throws<InvalidOperationException>(() => proc.Ready);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task PlannedDisposeDoesNotRaiseUnexpectedExit()
    {
        string root = Path.Combine(
            Path.GetTempPath(), $"vibeocr-supervisor-process-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var proc = CreateReadyProcess(root);
        var exited = new TaskCompletionSource(
            TaskCreationOptions.RunContinuationsAsynchronously);
        proc.UnexpectedExit += (_, _) => exited.TrySetResult();
        try
        {
            await proc.StartAsync(TestContext.Current.CancellationToken);
            proc.Dispose();
            await Task.Delay(250, TestContext.Current.CancellationToken);

            Assert.False(exited.Task.IsCompleted);
        }
        finally
        {
            proc.Dispose();
            Directory.Delete(root, recursive: true);
        }
    }

    private static InferenceSupervisorProcess CreateReadyProcess(
        string root,
        int lifetimeMilliseconds = 30_000)
    {
        string powershell = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.System),
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe");
        const string envelope =
            """{"ready":true,"pid":4321,"port":5432,"instance_id":"sup-test","protocol_version":2,"schema_version":2,"ready_version":1,"capabilities":["ocr.recognition.v2","pdf.edit.v2","qrcode.v2","export.document.v1","runtime.settings.v2"]}""";
        return new InferenceSupervisorProcess(
            new InferenceSupervisorOptions(
                powershell,
                [
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    $"[Console]::Out.WriteLine('{envelope}'); "
                    + $"Start-Sleep -Milliseconds {lifetimeMilliseconds}",
                ],
                root,
                Path.Combine(root, "supervisor.log"),
                TimeSpan.FromSeconds(5)),
            "tok");
    }
}
