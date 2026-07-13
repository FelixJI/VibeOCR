using System.Collections.Concurrent;
using System.Security.Cryptography;
using VibeOCR.App.Features.Batch;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class BatchViewModelTests
{
    [Fact]
    public async Task AddFilesDeduplicatesByFullPathAndPreservesInsertionOrder()
    {
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient();
        var viewModel = new BatchViewModel(worker, files);
        string pathA = StableInputPath("a");
        string pathB = StableInputPath("b");

        viewModel.AddFiles([pathA, pathB, pathA]);

        Assert.Equal(2, viewModel.TotalCount);
        Assert.Equal(Path.GetFullPath(pathA), viewModel.Items[0].Path);
        Assert.Equal(Path.GetFullPath(pathB), viewModel.Items[1].Path);
        Assert.Equal("0/2", viewModel.Progress);
        await Task.CompletedTask;
    }

    [Theory]
    [InlineData(1, new[] { 1, 0 })]
    public void MoveReordersQueueByDeltaAndClampsToBounds(int delta, int[] expectedOrderIndices)
    {
        var files = new FakeBatchFileSource();
        var viewModel = new BatchViewModel(new FakeWorkerClient(), files);
        viewModel.AddFiles([StableInputPath("a"), StableInputPath("b")]);
        Guid movingId = viewModel.Items[0].Id;
        Guid otherId = viewModel.Items[1].Id;

        viewModel.Move(movingId, delta);

        Assert.Equal(movingId, viewModel.Items[expectedOrderIndices[0]].Id);
        Assert.Equal(otherId, viewModel.Items[expectedOrderIndices[1]].Id);
    }

    [Fact]
    public void MoveAtBoundaryClampsAndLeavesOrderUnchanged()
    {
        var files = new FakeBatchFileSource();
        var viewModel = new BatchViewModel(new FakeWorkerClient(), files);
        viewModel.AddFiles([StableInputPath("a"), StableInputPath("b")]);
        Guid firstId = viewModel.Items[0].Id;
        Guid secondId = viewModel.Items[1].Id;

        // Moving the first item up (-1) clamps to 0 and does nothing.
        viewModel.Move(firstId, -1);

        Assert.Equal(firstId, viewModel.Items[0].Id);
        Assert.Equal(secondId, viewModel.Items[1].Id);
    }

    [Fact]
    public void RemoveDropsPendingItemButRefusesRunningItem()
    {
        var files = new FakeBatchFileSource();
        var viewModel = new BatchViewModel(new FakeWorkerClient(), files);
        viewModel.AddFiles([StableInputPath("a"), StableInputPath("b")]);
        Guid first = viewModel.Items[0].Id;

        viewModel.Remove(first);
        Assert.Single(viewModel.Items);
        Assert.DoesNotContain(viewModel.Items, item => item.Id == first);
    }

    [Fact]
    public async Task ConcurrencyBudgetCapsInFlightRecognizeCalls()
    {
        int inFlight = 0;
        int maxInFlight = 0;
        var sync = new object();
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient((_, _) =>
        {
            lock (sync) { inFlight++; maxInFlight = Math.Max(maxInFlight, inFlight); }
            // Yield once so the scheduler can interleave other budget holders, then complete.
            return Task.Run(() =>
            {
                lock (sync) { inFlight--; }
                return Result("ok");
            });
        });
        var viewModel = new BatchViewModel(worker, files) { Concurrency = 2 };
        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b"), CreateTempPng("c"), CreateTempPng("d")]);

        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        Assert.Equal(4, viewModel.CompletedCount);
        Assert.InRange(maxInFlight, 1, 2);
    }

    [Fact]
    public async Task ConcurrencyBudgetFourAllowsAllPendingInParallel()
    {
        int inFlight = 0;
        int maxInFlight = 0;
        var sync = new object();
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient((_, _) =>
        {
            lock (sync) { inFlight++; maxInFlight = Math.Max(maxInFlight, inFlight); }
            return Task.Run(() => { lock (sync) { inFlight--; } return Result("ok"); });
        });
        var viewModel = new BatchViewModel(worker, files) { Concurrency = 4 };
        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b"), CreateTempPng("c"), CreateTempPng("d")]);

        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        Assert.Equal(4, viewModel.CompletedCount);
        Assert.InRange(maxInFlight, 1, 4);
    }

    [Fact]
    public async Task CancelItemStopsItsRecognizeCallAndMarksCancelled()
    {
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient(async (_, ct) =>
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            return Result("unreachable");
        });
        var viewModel = new BatchViewModel(worker, files) { Concurrency = 1 };
        viewModel.AddFiles([CreateTempPng("a")]);

        Task run = viewModel.StartAsync(TestContext.Current.CancellationToken);
        await worker.WaitForCallsAsync(1);
        Guid id = viewModel.Items[0].Id;

        viewModel.CancelItem(id);
        await run;

        Assert.Equal(BatchItemState.Cancelled, viewModel.Items[0].State);
        Assert.False(viewModel.IsRunning);
    }

    [Fact]
    public async Task CancelAllAbortsRunAndMarksItemsCancelled()
    {
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient(async (_, ct) =>
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            return Result("unreachable");
        });
        var viewModel = new BatchViewModel(worker, files) { Concurrency = 1 };
        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b")]);

        Task run = viewModel.StartAsync(TestContext.Current.CancellationToken);
        await worker.WaitForCallsAsync(1);

        viewModel.CancelAll();
        await run;

        Assert.False(viewModel.IsRunning);
        Assert.All(viewModel.Items, item => Assert.Equal(BatchItemState.Cancelled, item.State));
    }

    [Fact]
    public async Task FailedItemKeepsRunningOthersAndSurfacesContinueOnFailure()
    {
        int call = 0;
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient((_, _) => ++call == 1
            ? throw new IOException("worker crashed")
            : Task.FromResult(Result("ok")));
        var viewModel = new BatchViewModel(worker, files) { Concurrency = 1 };
        viewModel.AddFiles([CreateTempPng("crash"), CreateTempPng("ok")]);

        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        Assert.False(viewModel.IsRunning);
        Assert.Equal(1, viewModel.FailedCount);
        Assert.Equal(1, viewModel.CompletedCount);
        Assert.Equal(BatchItemState.Failed, viewModel.Items[0].State);
        Assert.Equal(BatchItemState.Completed, viewModel.Items[1].State);
        Assert.Equal("IOException", viewModel.Items[0].Error);
        Assert.NotNull(viewModel.Items[1].Result);
    }

    [Fact]
    public async Task RestartDoesNotRestoreTemporaryQueue()
    {
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(Result("ok")));
        var viewModel = new BatchViewModel(worker, files);
        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b")]);
        await viewModel.StartAsync(TestContext.Current.CancellationToken);
        Assert.Equal(2, viewModel.CompletedCount);

        viewModel.ResetTemporaryQueue();

        Assert.Empty(viewModel.Items);
        Assert.Equal(0, viewModel.TotalCount);
        Assert.Equal(0, viewModel.CompletedCount);
        Assert.Equal(0, viewModel.FailedCount);
        Assert.False(viewModel.IsRunning);
    }

    [Fact]
    public async Task RepeatedStartReusesCompletedItemsAndRerunsPendingOrFailed()
    {
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(Result("ok")));
        var viewModel = new BatchViewModel(worker, files) { Concurrency = 1 };
        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b")]);
        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        viewModel.AddFiles([CreateTempPng("c")]);
        int recognizeCallsBefore = worker.TotalCalls;
        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        // Completed items must not be re-recognized; only the newly added item runs.
        Assert.Equal(1, worker.TotalCalls - recognizeCallsBefore);
        Assert.Equal(3, viewModel.CompletedCount);
        Assert.All(viewModel.Items, item => Assert.Equal(BatchItemState.Completed, item.State));
    }

    [Fact]
    public async Task ExportAllWritesUniqueOutputPerItemAndDelegatesToWorker()
    {
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(Result("text")));
        var viewModel = new BatchViewModel(worker, files);
        viewModel.AddFiles([CreateTempPng("alpha"), CreateTempPng("beta")]);
        await viewModel.StartAsync(TestContext.Current.CancellationToken);
        Assert.Equal(2, viewModel.CompletedCount);
        Assert.All(viewModel.Items, item => Assert.NotNull(item.Result));

        string directory = Path.Combine(Path.GetTempPath(), $"vibeocr-batch-{Guid.NewGuid():N}");
        try
        {
            IReadOnlyList<ExportOcrResponse> exports = await viewModel.ExportAllAsync(directory, "markdown", TestContext.Current.CancellationToken);

            Assert.Equal(2, exports.Count);
            Assert.Equal(2, worker.ExportCalls.Count);
            Assert.All(worker.ExportCalls, request => Assert.Equal("markdown", request.Format));
            // The Python worker is the write authority; here we only assert the view model
            // produces unique, absolute .md output paths inside the chosen directory.
            Assert.All(exports, response =>
            {
                Assert.True(Path.IsPathFullyQualified(response.OutputPath));
                Assert.StartsWith(directory, response.OutputPath);
                Assert.EndsWith(".md", response.OutputPath);
            });
            // Output paths must not collide.
            Assert.Equal(exports.Select(e => e.OutputPath).Distinct().Count(), exports.Count);
        }
        finally
        {
            if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
        }
    }

    [Fact]
    public void UniqueOutputPathAvoidsCollisionsWithExistingFiles()
    {
        string directory = Path.Combine(Path.GetTempPath(), $"vibeocr-batch-uniq-{Guid.NewGuid():N}");
        Directory.CreateDirectory(directory);
        try
        {
            string first = Path.Combine(directory, "scan.md");
            File.WriteAllText(first, "x");
            var reserved = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            string second = BatchCommands.UniqueOutputPath(directory, Path.Combine(directory, "scan.png"), "markdown", reserved);
            string third = BatchCommands.UniqueOutputPath(directory, Path.Combine(directory, "scan.png"), "markdown", reserved);

            Assert.NotEqual(first, second);
            Assert.NotEqual(second, third);
            Assert.EndsWith(".md", second);
            Assert.EndsWith(".md", third);
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [Fact]
    public async Task WorkerCrashReleasesPayloadAndMarksItemFailed()
    {
        var files = new FakeBatchFileSource();
        var worker = new FakeWorkerClient((_, _) => throw new IOException("pipe closed"));
        var viewModel = new BatchViewModel(worker, files) { Concurrency = 1 };
        viewModel.AddFiles([CreateTempPng("a")]);

        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        Assert.Equal(BatchItemState.Failed, viewModel.Items[0].State);
        Assert.Equal("IOException", viewModel.Items[0].Error);
        Assert.Equal(1, viewModel.FailedCount);
        Assert.Single(worker.ReleasedPayloads);
    }

    private static TaskCompletionSource<RecognizeResponse> NewTcs() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static string CreateTempPng(string stem)
    {
        string path = Path.Combine(Path.GetTempPath(), $"vibeocr-batch-input-{stem}-{Guid.NewGuid():N}.png");
        File.WriteAllBytes(path, [(byte)stem[0], 1, 2]);
        return path;
    }

    private static string StableInputPath(string stem)
    {
        string path = Path.Combine(Path.GetTempPath(), $"vibeocr-batch-stable-{stem}.png");
        if (!File.Exists(path)) File.WriteAllBytes(path, [(byte)stem[0], 1, 2]);
        return path;
    }

    private static RecognizeResponse Result(string text) => new()
    {
        Text = text,
        Pipeline = "OCR",
        RawBlocks = [],
    };

    private sealed class FakeBatchFileSource : IBatchFileSource
    {
        public Task<IReadOnlyList<string>> PickFilesAsync(CancellationToken cancellationToken) =>
            Task.FromResult<IReadOnlyList<string>>(Array.Empty<string>());

        public Task<(byte[] Data, string MediaType)> ReadAsync(string path, CancellationToken cancellationToken)
        {
            if (!File.Exists(path)) throw new FileNotFoundException("input missing", path);
            return Task.FromResult((File.ReadAllBytes(path), "image/png"));
        }
    }

    private sealed class FakeWorkerClient(Func<int, CancellationToken, Task<RecognizeResponse>>? responder = null)
        : IWorkerHostClient
    {
        private readonly Func<int, CancellationToken, Task<RecognizeResponse>> _responder =
            responder ?? ((_, _) => Task.FromResult(new RecognizeResponse { Text = "ok", Pipeline = "OCR", RawBlocks = [] }));
        private readonly TaskCompletionSource _callArrived = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private int _calls;
        private int _inFlightCallsField;

        public int TotalCalls => Volatile.Read(ref _calls);
        public int InFlightCalls => Volatile.Read(ref _inFlightCallsField);
        public List<string> ReleasedPayloads { get; } = [];
        public List<ExportOcrRequest> ExportCalls { get; } = [];

        public SharedPayloadRef CreatePayload(ReadOnlySpan<byte> data, string mediaType, TimeSpan ttl) => new()
        {
            Name = $@"Local\VibeOCR-batch-{Guid.NewGuid():D}-{Guid.NewGuid():D}",
            Size = data.Length,
            MediaType = mediaType,
            Sha256 = Convert.ToHexStringLower(SHA256.HashData(data)),
            Owner = SharedPayloadOwner.Client,
            ExpiresUnixMs = DateTimeOffset.UtcNow.Add(ttl).ToUnixTimeMilliseconds(),
        };

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
            if (method == RpcMethods.ExportOcr)
            {
                ExportCalls.Add(Assert.IsType<ExportOcrRequest>(request));
                var exportRequest = Assert.IsType<ExportOcrRequest>(request);
                return (TResponse)(object)new ExportOcrResponse
                {
                    OutputPath = exportRequest.OutputPath,
                    BytesWritten = 8,
                };
            }

            Assert.Equal(RpcMethods.Recognize, method);
            int call = Interlocked.Increment(ref _calls);
            Interlocked.Increment(ref _inFlightCallsField);
            _callArrived.TrySetResult();
            try
            {
                RecognizeResponse response = await _responder(call, cancellationToken);
                return (TResponse)(object)response;
            }
            finally
            {
                Interlocked.Decrement(ref _inFlightCallsField);
            }
        }

        public async Task WaitForCallsAsync(int expected)
        {
            while (Volatile.Read(ref _calls) < expected)
            {
                await _callArrived.Task.WaitAsync(TimeSpan.FromSeconds(5));
                await Task.Yield();
            }
        }
    }
}
