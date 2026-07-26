// Phase 7B tests: BatchViewModel v2 supervisor path.
//
// Verifies plan §7B: "BatchViewModel 一次提交逻辑 job, 不在 UI 切 GPU 微批".
// The v2 path submits ALL pending inputs in ONE generic recognition job, then
// maps typed outcomes by client item key rather than response position.
using System.Text.Json;
using VibeOCR.App.Features.Batch;
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
        Assert.Equal(JobKind.Recognition, fake.LastRequest?.Kind);
        Assert.Equal(JobPriority.Background, fake.LastRequest?.Priority);
        Assert.Equal("OCR", fake.LastRequest?.Pipeline.PipelineId);
        // The fake returns outcomes in reverse order. Correct UI order proves
        // mapping uses client_item_key through the JobRef item mapping.
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
    public async Task LocalCancellationUsesOneGenericCancelCommand()
    {
        var files = new FakeBatchFileSource();
        var fake = new FakeBatchInferenceClient();
        var viewModel = new BatchViewModel(fake, files);
        viewModel.AddFiles([CreateTempPng("a"), CreateTempPng("b")]);
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await viewModel.StartAsync(cancellation.Token);

        Assert.NotNull(fake.LastCommand);
        Assert.Equal(JobCommandKind.Cancel, fake.LastCommand!.Kind);
        Assert.Equal("batch-1", fake.LastCommand.JobId);
        Assert.All(viewModel.Items, item => Assert.Equal(BatchItemState.Cancelled, item.State));
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
    /// Fake v2 supervisor for batch. ObserveAsync returns all terminal outcomes
    /// in reverse order so tests detect positional result mapping.
    /// </summary>
    private sealed class FakeBatchInferenceClient : InferenceClientStub
    {
        private readonly JobState _terminalState;
        private readonly IReadOnlySet<int> _failures;
        private IReadOnlyList<JobItem> _items = Array.Empty<JobItem>();

        public FakeBatchInferenceClient(
            JobState terminalState = JobState.Completed,
            IReadOnlySet<int>? perItemFailures = null)
        {
            _terminalState = terminalState;
            _failures = perItemFailures ?? new HashSet<int>();
        }

        public int SubmitCalls { get; private set; }
        public SubmitRequest? LastRequest { get; private set; }
        public IReadOnlyDictionary<string, SubmitUpload>? LastUploads { get; private set; }
        public JobCommand? LastCommand { get; private set; }

        public override Task<JobRef> SubmitAsync(
            SubmitRequest request,
            IReadOnlyDictionary<string, SubmitUpload> uploads,
            CancellationToken cancellationToken)
        {
            SubmitCalls++;
            LastRequest = request;
            LastUploads = uploads;
            _items = request.Items.Select((item, index) => new JobItem
            {
                ItemId = $"it-{index}",
                ClientItemKey = item.ClientItemKey,
                Ordinal = item.Ordinal,
                DisplayName = item.DisplayName,
                State = ItemState.Queued,
            }).ToArray();
            return Task.FromResult(new JobRef
            {
                JobId = $"batch-{SubmitCalls}",
                Items = _items,
            });
        }

        public override Task<JobUpdate> ObserveAsync(
            string jobId,
            int afterSequence,
            CancellationToken cancellationToken)
        {
            JobState state = _terminalState is JobState.Completed && _failures.Count > 0
                ? JobState.CompletedWithErrors
                : _terminalState;
            ItemOutcome[] outcomes = _items
                .Reverse()
                .Select(item =>
                {
                    bool failed = _failures.Contains(item.Ordinal);
                    ItemState itemState = state is JobState.Cancelled
                        ? ItemState.Cancelled
                        : failed ? ItemState.Failed : ItemState.Succeeded;
                    string stem = Path.GetFileNameWithoutExtension(item.DisplayName);
                    return new ItemOutcome
                    {
                        ItemId = item.ItemId,
                        State = itemState,
                        Attempt = 1,
                        PayloadType = itemState is ItemState.Succeeded ? "ocr.v1" : null,
                        Payload = itemState is ItemState.Succeeded
                            ? new Dictionary<string, JsonElement>
                            {
                                ["raw_text"] = JsonSerializer.SerializeToElement($"ocr-{stem}"),
                            }
                            : null,
                        ErrorCode = itemState is ItemState.Failed ? "OUT_OF_MEMORY" : null,
                    };
                })
                .ToArray();
            return Task.FromResult(new JobUpdate
            {
                Snapshot = new JobSnapshot
                {
                    JobId = jobId,
                    Kind = JobKind.Recognition,
                    Priority = JobPriority.Background,
                    State = state,
                    Items = _items,
                    EventSequence = afterSequence + 1,
                },
                Events = Array.Empty<StageEvent>(),
                Outcomes = outcomes,
                ThroughSequence = afterSequence + 1,
            });
        }

        public override Task<JobCommandResult> CommandAsync(
            JobCommand command,
            CancellationToken cancellationToken)
        {
            LastCommand = command;
            return Task.FromResult(new JobCommandResult(
                command.CommandId,
                command.Kind,
                CancelMode.Cooperative,
                null));
        }

        public override Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken)
            => Task.FromResult(new ResidencyStatus());

        public override Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken)
            => Task.FromResult(new SettingsSnapshot());
    }

}
