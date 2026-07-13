using System.Security.Cryptography;
using VibeOCR.App.Features.Settings;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class SettingsViewModelTests
{
    [Fact]
    public async Task LoadSnapshotPopulatesBackendAndPreloadPipelines()
    {
        var worker = new FakeSettingsWorker(backend: "gpu", preload: ["OCR", "TABLE_RECOGNITION"], ttl: 600);
        var viewModel = new SettingsViewModel(worker);

        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);

        Assert.Equal("gpu", viewModel.Backend);
        Assert.Equal("gpu", viewModel.PendingBackend);
        Assert.Equal(600, viewModel.TtlSeconds);
        Assert.Equal(2, viewModel.PreloadPipelines.Count);
        Assert.False(viewModel.IsBusy);
    }

    [Fact]
    public async Task SwitchToSameBackendIsANoOp()
    {
        var worker = new FakeSettingsWorker(backend: "cpu");
        var viewModel = new SettingsViewModel(worker);
        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);

        await viewModel.SwitchBackendAsync("cpu", TestContext.Current.CancellationToken);

        Assert.Equal("已是该后端", viewModel.Status);
        Assert.False(viewModel.RestartRequired);
    }

    [Fact]
    public async Task SwitchToGpuWithoutGpuDetectedIsRejected()
    {
        var worker = new FakeSettingsWorker(backend: "cpu");
        var viewModel = new SettingsViewModel(worker);
        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);
        viewModel.DetectGpu(available: false);

        await viewModel.SwitchBackendAsync("gpu", TestContext.Current.CancellationToken);

        Assert.Equal("未检测到可用 GPU", viewModel.Status);
        Assert.Equal("cpu", viewModel.Backend);
    }

    [Fact]
    public async Task SwitchToGpuWithGpuAvailableSucceedsAndRequiresRestart()
    {
        var worker = new FakeSettingsWorker(backend: "cpu");
        var viewModel = new TestableSettingsViewModel(worker)
        {
            SwitchCoreHandler = _ => Task.CompletedTask,
        };
        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);
        viewModel.DetectGpu(available: true);

        await viewModel.SwitchBackendAsync("gpu", TestContext.Current.CancellationToken);

        Assert.Equal("gpu", viewModel.Backend);
        Assert.True(viewModel.RestartRequired);
        Assert.Contains("需重启", viewModel.Status);
    }

    [Fact]
    public async Task SwitchBackendNetworkErrorDoesNotChangeBackendOrRetry()
    {
        int calls = 0;
        var worker = new FakeSettingsWorker(backend: "cpu");
        var viewModel = new TestableSettingsViewModel(worker)
        {
            SwitchCoreHandler = _ =>
            {
                calls++;
                return Task.FromException(new WorkerRpcException(new RpcErrorBody
                {
                    Code = ErrorCode.WorkerUnavailable,
                    Message = "mirror unreachable",
                    Retryable = true,
                }));
            },
        };
        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);
        viewModel.DetectGpu(available: true);

        await viewModel.SwitchBackendAsync("gpu", TestContext.Current.CancellationToken);

        // Backend must NOT change; the error is surfaced; no auto-retry.
        Assert.Equal("cpu", viewModel.Backend);
        Assert.Equal(1, calls);
        Assert.Contains("Worker", viewModel.Status);
    }

    [Fact]
    public async Task WorkerErrorDuringLoadIsLocalized()
    {
        var worker = new FakeSettingsWorker(loadError: ErrorCode.DependencyMissing);
        var viewModel = new SettingsViewModel(worker);

        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);

        Assert.Equal("依赖尚未安装", viewModel.Status);
        Assert.False(viewModel.IsBusy);
    }

    [Fact]
    public async Task DetectGpuFalseResetsPendingGpuToCpu()
    {
        var worker = new FakeSettingsWorker(backend: "gpu");
        var viewModel = new SettingsViewModel(worker);
        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);
        Assert.Equal("gpu", viewModel.PendingBackend);

        viewModel.DetectGpu(available: false);

        Assert.Equal("cpu", viewModel.PendingBackend);
    }

    /// <summary>
    /// Subclass that lets tests inject the protected backend-switch core,
    /// which in production delegates to the dependency manager (not yet a
    /// protocol method).
    /// </summary>
    private sealed class TestableSettingsViewModel(IWorkerHostClient worker) : SettingsViewModel(worker)
    {
        public Func<string, Task>? SwitchCoreHandler { get; set; }

        protected override async Task OnSwitchBackendCoreAsync(string target, CancellationToken cancellationToken)
        {
            if (SwitchCoreHandler is not null) await SwitchCoreHandler(target);
        }
    }

    private sealed class FakeSettingsWorker(
        string backend = "cpu",
        string[]? preload = null,
        int ttl = 300,
        ErrorCode? loadError = null) : IWorkerHostClient
    {
        public SharedPayloadRef CreatePayload(ReadOnlySpan<byte> data, string mediaType, TimeSpan ttl) => new()
        {
            Name = $@"Local\VibeOCR-settings-{Guid.NewGuid():D}-{Guid.NewGuid():D}",
            Size = data.Length,
            MediaType = mediaType,
            Sha256 = Convert.ToHexStringLower(SHA256.HashData(data)),
            Owner = SharedPayloadOwner.Client,
            ExpiresUnixMs = DateTimeOffset.UtcNow.Add(ttl).ToUnixTimeMilliseconds(),
        };

        public bool ReleasePayload(string name) => true;

        public byte[] ReadPayload(SharedPayloadRef reference, TimeSpan timeout, CancellationToken cancellationToken) => [];

        public Task<TResponse> CallAsync<TRequest, TResponse>(
            string method, TRequest request, CancellationToken cancellationToken)
            where TRequest : IProtocolValidatable
            where TResponse : IProtocolValidatable
        {
            Assert.Equal(RpcMethods.SettingsSnapshot, method);
            if (loadError is { } code)
            {
                throw new WorkerRpcException(new RpcErrorBody { Code = code, Message = "err", Retryable = false });
            }
            object response = new SettingsSnapshotResponse
            {
                Backend = backend,
                PreloadPipelines = preload ?? ["OCR"],
                TtlSeconds = ttl,
            };
            return Task.FromResult((TResponse)response);
        }
    }
}
