using System.Security.Cryptography;
using VibeOCR.App.Features.Recognition;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class RecognitionViewModelTests
{
    [Theory]
    [InlineData("file")]
    [InlineData("clipboard")]
    [InlineData("screenshot")]
    [InlineData("drop")]
    public async Task EveryInputOriginUsesSharedPayloadAndPublishesResult(string origin)
    {
        var inputs = new FakeInputService();
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(Result("recognized")));
        var viewModel = new RecognitionViewModel(worker, inputs);

        await InvokeAsync(viewModel, origin);

        Assert.Equal("recognized", viewModel.ResultText);
        Assert.Equal("识别完成", viewModel.Status);
        Assert.Equal([1, 2, 3, 4], worker.LastPayloadBytes);
        Assert.Equal("image/png", worker.LastRequest?.Image.MediaType);
        Assert.Equal(origin, viewModel.CurrentInput?.Origin);
        Assert.True(viewModel.HasResult);
        Assert.Single(worker.ReleasedPayloads);
        Assert.Equal(1, inputs.Calls[origin]);
    }

    [Fact]
    public void ScreenSelectionMapsOverlayCoordinatesToVirtualDesktopPixels()
    {
        var desktop = new VibeOCR.Platform.Windows.PhysicalRectangle(-1920, -200, 5760, 2360);

        VibeOCR.Platform.Windows.PhysicalRectangle selected = ScreenRegionPicker.ScaleSelection(
            desktop,
            left: 320,
            top: 100,
            width: 640,
            height: 400,
            canvasWidth: 1920,
            canvasHeight: 1080);

        Assert.Equal(-960, selected.X);
        Assert.Equal(19, selected.Y);
        Assert.Equal(1920, selected.Width);
        Assert.Equal(874, selected.Height);
    }

    [Fact]
    public async Task RepeatedStartCancelsOldGenerationAndDropsLateResult()
    {
        var first = new TaskCompletionSource<RecognizeResponse>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var second = new TaskCompletionSource<RecognizeResponse>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var worker = new FakeWorkerClient((call, _) =>
            call == 1 ? first.Task : second.Task);
        var viewModel = new RecognitionViewModel(worker, new FakeInputService());

        Task oldRun = viewModel.RecognizeFileAsync(TestContext.Current.CancellationToken);
        await worker.WaitForCallsAsync(1);
        Task newRun = viewModel.RecognizeClipboardAsync(TestContext.Current.CancellationToken);
        await worker.WaitForCallsAsync(2);
        second.SetResult(Result("new"));
        await newRun;
        first.SetResult(Result("stale"));
        await oldRun;

        Assert.Equal("new", viewModel.ResultText);
        Assert.Equal("识别完成", viewModel.Status);
        Assert.Equal(2, worker.ReleasedPayloads.Count);
    }

    [Fact]
    public async Task CancelStopsCurrentCallAndResetsBusyState()
    {
        var worker = new FakeWorkerClient(async (_, cancellationToken) =>
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            return Result("unreachable");
        });
        var viewModel = new RecognitionViewModel(worker, new FakeInputService());
        Task run = viewModel.RecognizeScreenshotAsync(TestContext.Current.CancellationToken);
        await worker.WaitForCallsAsync(1);

        viewModel.Cancel();
        await run;

        Assert.False(viewModel.IsBusy);
        Assert.Equal("已取消", viewModel.Status);
        Assert.Single(worker.ReleasedPayloads);
    }

    [Theory]
    [InlineData(ErrorCode.DependencyMissing, "识别依赖尚未安装")]
    [InlineData(ErrorCode.ProtocolMismatch, "Worker 协议不兼容")]
    [InlineData(ErrorCode.ResourceExhausted, "内存或显存不足")]
    [InlineData(ErrorCode.InternalError, "识别失败")]
    public async Task WorkerErrorsAreLocalized(ErrorCode code, string expected)
    {
        var worker = new FakeWorkerClient((_, _) => throw new WorkerRpcException(
            new RpcErrorBody
            {
                Code = code,
                Message = "sensitive backend detail",
                Retryable = false,
                Detail = @"C:\Users\alice\secret.log",
            }));
        var viewModel = new RecognitionViewModel(worker, new FakeInputService());

        await viewModel.RecognizeFileAsync(TestContext.Current.CancellationToken);

        Assert.Equal(expected, viewModel.Status);
        Assert.DoesNotContain("alice", viewModel.Status, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task WorkerCrashBecomesStableLocalizedFailure()
    {
        var worker = new FakeWorkerClient((_, _) => throw new IOException("pipe closed"));
        var viewModel = new RecognitionViewModel(worker, new FakeInputService());

        await viewModel.RecognizeFileAsync(TestContext.Current.CancellationToken);

        Assert.False(viewModel.IsBusy);
        Assert.Equal("Worker 已断开，请重试", viewModel.Status);
    }

    [Fact]
    public async Task InvalidInputBecomesLocalizedFailure()
    {
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(Result("unused")));
        var viewModel = new RecognitionViewModel(worker, new FailingInputService());

        await viewModel.RecognizeClipboardAsync(TestContext.Current.CancellationToken);

        Assert.False(viewModel.IsBusy);
        Assert.Equal("无法读取输入图片", viewModel.Status);
        Assert.Empty(worker.ReleasedPayloads);
    }

    [Fact]
    public async Task RealDroppedFilePreservesBytesAndMediaType()
    {
        string path = Path.Combine(Path.GetTempPath(), $"vibeocr-input-{Guid.NewGuid():N}.png");
        try
        {
            await File.WriteAllBytesAsync(
                path,
                [9, 8, 7],
                TestContext.Current.CancellationToken);
            var service = new InputService(() => 0);

            RecognitionInput? input = await service.ReadDroppedFileAsync(
                path,
                TestContext.Current.CancellationToken);

            Assert.NotNull(input);
            Assert.Equal([9, 8, 7], input.Data);
            Assert.Equal("image/png", input.MediaType);
            Assert.Equal("drop", input.Origin);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public async Task RecoverableGatewayRetriesOcrExactlyOnceAfterCrash()
    {
        var crashed = new FakeWorkerClient((_, _) => throw new IOException("worker exited"));
        var recovered = new FakeWorkerClient((_, _) => Task.FromResult(Result("after restart")));
        await using var gateway = new DeferredWorkerHostClient();
        gateway.Attach(crashed);
        int restarts = 0;
        gateway.ConfigureRecovery(_ =>
        {
            restarts++;
            return Task.FromResult<IWorkerHostClient>(recovered);
        });
        var viewModel = new RecognitionViewModel(gateway, new FakeInputService());

        await viewModel.RecognizeFileAsync(TestContext.Current.CancellationToken);

        Assert.Equal("after restart", viewModel.ResultText);
        Assert.Equal("识别完成", viewModel.Status);
        Assert.Equal(1, restarts);
    }

    private static Task InvokeAsync(RecognitionViewModel viewModel, string origin) => origin switch
    {
        "file" => viewModel.RecognizeFileAsync(TestContext.Current.CancellationToken),
        "clipboard" => viewModel.RecognizeClipboardAsync(TestContext.Current.CancellationToken),
        "screenshot" => viewModel.RecognizeScreenshotAsync(TestContext.Current.CancellationToken),
        "drop" => viewModel.RecognizeDroppedFileAsync(
            @"C:\input files\scan.png",
            TestContext.Current.CancellationToken),
        _ => throw new ArgumentOutOfRangeException(nameof(origin)),
    };

    private static RecognizeResponse Result(string text) => new()
    {
        Text = text,
        Pipeline = "OCR",
        RawBlocks = [],
    };

    private sealed class FakeInputService : IInputService
    {
        public Dictionary<string, int> Calls { get; } = new(StringComparer.Ordinal)
        {
            ["file"] = 0,
            ["clipboard"] = 0,
            ["screenshot"] = 0,
            ["drop"] = 0,
        };

        public Task<RecognitionInput?> PickFileAsync(CancellationToken cancellationToken) =>
            Get("file");

        public Task<RecognitionInput?> ReadClipboardAsync(CancellationToken cancellationToken) =>
            Get("clipboard");

        public Task<RecognitionInput?> CaptureScreenAsync(CancellationToken cancellationToken) =>
            Get("screenshot");

        public Task<RecognitionInput?> ReadDroppedFileAsync(
            string path,
            CancellationToken cancellationToken) => Get("drop");

        private Task<RecognitionInput?> Get(string origin)
        {
            Calls[origin]++;
            return Task.FromResult<RecognitionInput?>(
                new([1, 2, 3, 4], "image/png", $"{origin}.png", origin));
        }
    }

    private sealed class FailingInputService : IInputService
    {
        public Task<RecognitionInput?> PickFileAsync(CancellationToken cancellationToken) => Fail();
        public Task<RecognitionInput?> ReadClipboardAsync(CancellationToken cancellationToken) => Fail();
        public Task<RecognitionInput?> CaptureScreenAsync(CancellationToken cancellationToken) => Fail();
        public Task<RecognitionInput?> ReadDroppedFileAsync(
            string path,
            CancellationToken cancellationToken) => Fail();

        private static Task<RecognitionInput?> Fail() =>
            throw new InvalidDataException("private clipboard detail");
    }

    private sealed class FakeWorkerClient(
        Func<int, CancellationToken, Task<RecognizeResponse>> responder) : IWorkerHostClient
    {
        private readonly TaskCompletionSource _callChanged =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
        private int _calls;

        public byte[]? LastPayloadBytes { get; private set; }
        public RecognizeRequest? LastRequest { get; private set; }
        public List<string> ReleasedPayloads { get; } = [];

        public SharedPayloadRef CreatePayload(
            ReadOnlySpan<byte> data,
            string mediaType,
            TimeSpan ttl)
        {
            LastPayloadBytes = data.ToArray();
            return new SharedPayloadRef
            {
                Name = $@"Local\VibeOCR-{Guid.NewGuid():D}-{Guid.NewGuid():D}",
                Size = data.Length,
                MediaType = mediaType,
                Sha256 = Convert.ToHexStringLower(SHA256.HashData(data)),
                Owner = SharedPayloadOwner.Client,
                ExpiresUnixMs = DateTimeOffset.UtcNow.Add(ttl).ToUnixTimeMilliseconds(),
            };
        }

        public bool ReleasePayload(string name)
        {
            ReleasedPayloads.Add(name);
            return true;
        }

        public byte[] ReadPayload(SharedPayloadRef reference, TimeSpan timeout, CancellationToken cancellationToken) => [];

        public async Task<TResponse> CallAsync<TRequest, TResponse>(
            string method,
            TRequest request,
            CancellationToken cancellationToken)
            where TRequest : IProtocolValidatable
            where TResponse : IProtocolValidatable
        {
            Assert.Equal(RpcMethods.Recognize, method);
            LastRequest = Assert.IsType<RecognizeRequest>(request);
            int call = Interlocked.Increment(ref _calls);
            _callChanged.TrySetResult();
            RecognizeResponse response = await responder(call, cancellationToken);
            return (TResponse)(object)response;
        }

        public async Task WaitForCallsAsync(int expected)
        {
            while (Volatile.Read(ref _calls) < expected)
            {
                await _callChanged.Task.WaitAsync(TimeSpan.FromSeconds(2));
                await Task.Yield();
            }
        }
    }
}
