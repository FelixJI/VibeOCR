// Phase 7B tests: BatchViewModel v2 supervisor path.
//
// Verifies plan §7B: "BatchViewModel 一次提交逻辑 job, 不在 UI 切 GPU 微批".
// The v2 path submits ALL pending inputs in ONE SubmitRecognitionAsync call
// (one logical job), then maps the stable-ordered ResultEntry[] back onto the
// per-item observable collection. The legacy per-item SemaphoreSlim loop stays
// covered by BatchViewModelTests; this file is additive.
using System.Collections.ObjectModel;
using System.Text.Json;
using VibeOCR.App.Features.Batch;
using VibeOCR.Contracts;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class BatchViewModelSupervisorTests
{
    [Fact]
    public async Task SupervisorPathSubmitsAllInputsAsOneJobAndMapsPerItemResults()
    {
        var files = new FakeBatchFileSource();
        var fake = new FakeBatchInferenceClient();
        var viewModel = new BatchViewModel(fake, files);

        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b"), CreateTempPng("c")]);
        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        // Exactly one submit carrying all three inputs.
        Assert.Equal(1, fake.SubmitCalls);
        Assert.NotNull(fake.LastUploads);
        Assert.Equal(3, fake.LastUploads!.Count);
        Assert.Equal(JobPriority.Background, fake.LastPriority);
        // Per-item results mapped back in input order, text derived from each
        // item's actual display name (stem of the temp file).
        Assert.Equal(3, viewModel.CompletedCount);
        Assert.Equal(0, viewModel.FailedCount);
        Assert.Equal(BatchItemState.Completed, viewModel.Items[0].State);
        Assert.Equal($"ocr-{Path.GetFileNameWithoutExtension(viewModel.Items[0].Name)}", viewModel.Items[0].Result?.Text);
        Assert.Equal($"ocr-{Path.GetFileNameWithoutExtension(viewModel.Items[1].Name)}", viewModel.Items[1].Result?.Text);
        Assert.Equal($"ocr-{Path.GetFileNameWithoutExtension(viewModel.Items[2].Name)}", viewModel.Items[2].Result?.Text);
        Assert.False(viewModel.IsRunning);
    }

    [Fact]
    public async Task SupervisorPathContinuesOnPerItemFailure()
    {
        // Item 1 fails (ErrorCode set); items 0 and 2 still complete.
        var files = new FakeBatchFileSource();
        var fake = new FakeBatchInferenceClient(perItemFailures: new HashSet<int> { 1 });
        var viewModel = new BatchViewModel(fake, files);

        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b"), CreateTempPng("c")]);
        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        Assert.Equal(2, viewModel.CompletedCount);
        Assert.Equal(1, viewModel.FailedCount);
        Assert.Equal(BatchItemState.Completed, viewModel.Items[0].State);
        Assert.Equal(BatchItemState.Failed, viewModel.Items[1].State);
        Assert.NotNull(viewModel.Items[1].Error);
        Assert.Equal(BatchItemState.Completed, viewModel.Items[2].State);
    }

    [Fact]
    public async Task SupervisorPathMarksItemsCancelledWhenJobCancelled()
    {
        var files = new FakeBatchFileSource();
        var fake = new FakeBatchInferenceClient(terminalState: JobState.Cancelled);
        var viewModel = new BatchViewModel(fake, files);

        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b")]);
        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        Assert.Equal(BatchItemState.Cancelled, viewModel.Items[0].State);
        Assert.Equal(BatchItemState.Cancelled, viewModel.Items[1].State);
        Assert.Equal(0, viewModel.CompletedCount);
        Assert.False(viewModel.IsRunning);
    }

    [Fact]
    public async Task SupervisorPathDoesNotSliceIntoMultipleSubmits()
    {
        // The plan explicitly forbids the UI from slicing a batch into
        // per-item microbatches. Assert exactly one submit regardless of input count.
        var files = new FakeBatchFileSource();
        var fake = new FakeBatchInferenceClient();
        var viewModel = new BatchViewModel(fake, files);

        viewModel.AddFiles(Enumerable.Range(0, 8).Select(i => CreateTempPng($"f{i}")).ToArray());
        await viewModel.StartAsync(TestContext.Current.CancellationToken);

        Assert.Equal(1, fake.SubmitCalls);
        Assert.Equal(8, fake.LastUploads!.Count);
        Assert.Equal(8, viewModel.CompletedCount);
    }

    [Fact]
    public async Task EmptyBatchReturnsImmediately()
    {
        var viewModel = new BatchViewModel(new FakeBatchInferenceClient(), new FakeBatchFileSource());
        await viewModel.StartAsync(CancellationToken.None); // No items -> returns immediately
        Assert.False(viewModel.IsRunning);
    }

    // ------------------------------------------------------------------
    // Fakes + helpers
    // ------------------------------------------------------------------

    private static string CreateTempPng(string stem)
    {
        string path = Path.Combine(Path.GetTempPath(), $"vibeocr-batch-sup-{stem}-{Guid.NewGuid():N}.png");
        File.WriteAllBytes(path, [(byte)stem[0], 1, 2]);
        return path;
    }

    private sealed class FakeBatchFileSource : IBatchFileSource
    {
        public Task<IReadOnlyList<string>> PickFilesAsync(CancellationToken cancellationToken)
            => Task.FromResult<IReadOnlyList<string>>(Array.Empty<string>());

        public Task<(byte[] Data, string MediaType)> ReadAsync(string path, CancellationToken cancellationToken)
            => Task.FromResult((File.ReadAllBytes(path), "image/png"));
    }

    /// <summary>
    /// Fake v2 supervisor for batch. The first GetJobAsync returns terminal;
    /// GetResultAsync returns one ResultEntry per upload, in input order. Each
    /// entry's text is derived from its display name; entries in
    /// <c>perItemFailures</c> carry a non-null ErrorCode (continue-on-failure).
    /// </summary>
    private sealed class FakeBatchInferenceClient : IInferenceClient
    {
        private readonly JobState _terminalState;
        private readonly IReadOnlySet<int> _failures;
        private IReadOnlyList<RecognitionUpload>? _uploads;

        public FakeBatchInferenceClient(
            JobState terminalState = JobState.Completed,
            IReadOnlySet<int>? perItemFailures = null)
        {
            _terminalState = terminalState;
            _failures = perItemFailures ?? new HashSet<int>();
        }

        public int SubmitCalls { get; private set; }
        public IReadOnlyList<RecognitionUpload>? LastUploads => _uploads;
        public JobPriority LastPriority { get; private set; }
        public Uri BaseUrl => new("http://127.0.0.1:1");

        public Task<JobRef> SubmitRecognitionAsync(
            IReadOnlyList<RecognitionUpload> uploads, JobPriority priority, CancellationToken cancellationToken)
        {
            SubmitCalls++;
            _uploads = uploads;
            LastPriority = priority;
            return Task.FromResult(new JobRef { JobId = $"batch-{SubmitCalls}" });
        }

        public Task<JobSnapshot> GetJobAsync(string jobId, CancellationToken cancellationToken)
            => Task.FromResult(new JobSnapshot
            {
                JobId = jobId,
                Kind = JobKind.Recognition,
                Priority = JobPriority.Background,
                State = _terminalState,
                SchemaVersion = 2,
                Stage = _terminalState is JobState.Completed ? "done" : "running",
                ProgressCurrent = _terminalState is JobState.Completed ? (_uploads?.Count ?? 0) : 0,
                ProgressTotal = _uploads?.Count ?? 0,
                EventSequence = 1,
                ResultAvailable = _terminalState is JobState.Completed,
            });

        public Task<IReadOnlyList<StageEvent>> GetEventsAsync(
            string jobId, int afterSequence, CancellationToken cancellationToken)
            => Task.FromResult<IReadOnlyList<StageEvent>>(Array.Empty<StageEvent>());

        public Task<IReadOnlyList<ResultEntry>> GetResultAsync(string jobId, CancellationToken cancellationToken)
        {
            if (_uploads is null || _uploads.Count == 0)
            {
                return Task.FromResult<IReadOnlyList<ResultEntry>>(Array.Empty<ResultEntry>());
            }

            var results = new ResultEntry[_uploads.Count];
            for (int i = 0; i < _uploads.Count; i++)
            {
                // Derive a deterministic text from the display name (strip ".png").
                string stem = _uploads[i].FileName;
                if (stem.EndsWith(".png", StringComparison.OrdinalIgnoreCase))
                {
                    stem = stem[..^4];
                }

                string text = $"ocr-{stem}";
                var payload = new Dictionary<string, JsonElement>
                {
                    ["text"] = JsonSerializer.SerializeToElement(text),
                };
                results[i] = new ResultEntry
                {
                    ItemId = $"it-{i}",
                    DisplayName = _uploads[i].FileName,
                    Payload = payload,
                    ErrorCode = _failures.Contains(i) ? "OUT_OF_MEMORY" : null,
                };
            }

            return Task.FromResult<IReadOnlyList<ResultEntry>>(results);
        }

        public Task<CancelMode> CancelAsync(string jobId, CancellationToken cancellationToken)
            => Task.FromResult(CancelMode.Cooperative);

        public Task DeleteJobAsync(string jobId, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken)
            => Task.FromResult(new ResidencyStatus());

        public Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken)
            => Task.FromResult(new SettingsSnapshot());

        public Task<PdfSessionOpenResult> OpenPdfSessionAsync(string path, string? password, CancellationToken ct) => throw new NotImplementedException();
        public Task<byte[]> RenderPdfPageAsync(string sessionId, int page, int size, CancellationToken ct) => throw new NotImplementedException();
        public Task<PdfMutateResult> RotatePdfPagesAsync(string sessionId, int[] pages, int angle, CancellationToken ct) => throw new NotImplementedException();
        public Task<PdfMutateResult> DeletePdfPagesAsync(string sessionId, int[] pages, CancellationToken ct) => throw new NotImplementedException();
        public Task<string> SavePdfAsync(string sessionId, string outputPath, CancellationToken ct) => throw new NotImplementedException();
        public Task ClosePdfSessionAsync(string sessionId, CancellationToken ct) => throw new NotImplementedException();
                public Task<ExportResult> ExportAsync(ExportRequest request, CancellationToken ct) => throw new NotImplementedException();
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

}
