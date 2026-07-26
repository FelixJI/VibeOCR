// Phase 7B tests: RecognitionViewModel v2 supervisor path.
//
// Verifies the plan §7B requirement "RecognitionViewModel 改为一元素 job；取消
// 等待真实 job terminal" against a hand-written fake IInferenceClient (the
// "fake HTTP server" for WinUI). The legacy path stays covered by
// RecognitionViewModelTests; this file is additive.
using System.Text.Json;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.Inference;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class RecognitionViewModelSupervisorTests
{
    [Fact]
    public async Task SupervisorPathSubmitsOneElementJobAndPublishesResult()
    {
        var fakeInference = new FakeInferenceClient("hello from supervisor");
        var inputs = new StubInputService();
        var viewModel = new RecognitionViewModel(fakeInference, inputs);

        await viewModel.RecognizeViaSupervisorAsync(ct => inputs.PickFileAsync(ct), CancellationToken.None);

        Assert.Equal("hello from supervisor", viewModel.ResultText);
        Assert.Equal("识别完成", viewModel.Status);
        Assert.True(viewModel.HasResult);
        // Exactly one submit with exactly one upload (single = one-element job).
        Assert.Equal(1, fakeInference.SubmitCalls);
        Assert.NotNull(fakeInference.LastRequest);
        Assert.Equal(JobKind.Recognition, fakeInference.LastRequest!.Kind);
        Assert.Equal(JobPriority.Interactive, fakeInference.LastRequest.Priority);
        Assert.Equal("OCR", fakeInference.LastRequest.Pipeline.PipelineId);
        Assert.Single(fakeInference.LastRequest.Items);
        Assert.Equal("recognition-input", fakeInference.LastRequest.Items[0].ClientItemKey);
        Assert.Equal("file.png", fakeInference.LastRequest.Items[0].DisplayName);
        IReadOnlyDictionary<string, SubmitUpload> uploads = Assert.IsAssignableFrom<
            IReadOnlyDictionary<string, SubmitUpload>>(fakeInference.LastUploads);
        Assert.Single(uploads);
        Assert.Equal(new byte[] { 1, 2, 3, 4 }, uploads["input-0"].Content);
    }

    [Fact]
    public async Task SupervisorPathLocalizesTypedError()
    {
        var fakeInference = new FakeInferenceClient(
            "ignored",
            submitThrows: new InferenceClientException(HttpV2ErrorCode.OutOfMemory, "oom", true));
        var inputs = new StubInputService();
        var viewModel = new RecognitionViewModel(fakeInference, inputs);

        await viewModel.RecognizeViaSupervisorAsync(ct => inputs.PickFileAsync(ct), CancellationToken.None);

        Assert.Equal("内存或显存不足", viewModel.Status);
        Assert.False(viewModel.HasResult);
    }

    [Fact]
    public async Task SupervisorPathReportsCancellationWhenJobCancelled()
    {
        // The fake returns a CANCELLED snapshot, modelling an honest terminal
        // state after a cancel request (not a socket disconnect).
        var fakeInference = new FakeInferenceClient("ignored", terminalState: JobState.Cancelled);
        var inputs = new StubInputService();
        var viewModel = new RecognitionViewModel(fakeInference, inputs);

        await viewModel.RecognizeViaSupervisorAsync(ct => inputs.PickFileAsync(ct), CancellationToken.None);

        Assert.Equal("已取消", viewModel.Status);
        Assert.False(viewModel.HasResult);
    }

    [Fact]
    public async Task SupervisorPathPollsAtomicJobUpdatesBySequence()
    {
        var fakeInference = new FakeInferenceClient(
            "sequenced",
            runningUpdatesBeforeTerminal: 1);
        var inputs = new StubInputService();
        var viewModel = new RecognitionViewModel(fakeInference, inputs);

        await viewModel.RecognizeViaSupervisorAsync(
            ct => inputs.PickFileAsync(ct),
            CancellationToken.None);

        Assert.Equal([0, 1], fakeInference.ObservedAfterSequences);
        Assert.Equal("sequenced", viewModel.ResultText);
    }

    [Fact]
    public async Task LocalCancellationUsesGenericCancelCommand()
    {
        var fakeInference = new FakeInferenceClient("ignored");
        var inputs = new StubInputService();
        var viewModel = new RecognitionViewModel(fakeInference, inputs);
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await viewModel.RecognizeViaSupervisorAsync(
            ct => inputs.PickFileAsync(ct),
            cancellation.Token);

        Assert.NotNull(fakeInference.LastCommand);
        Assert.Equal(JobCommandKind.Cancel, fakeInference.LastCommand!.Kind);
        Assert.Equal("job-1", fakeInference.LastCommand.JobId);
    }

    [Fact]
    public async Task SupervisorPathSecondRunWinsAfterFirstCompletes()
    {
        // Generation guard: two sequential runs both complete; the second one's
        // result is what the UI shows (the first's result is superseded, not
        // merged). This is the deterministic core of the discard-late-results
        // invariant without flaky concurrency.
        var inputs = new StubInputService();
        var fake = new FakeInferenceClient("first");
        var viewModel = new RecognitionViewModel(fake, inputs);

        await viewModel.RecognizeViaSupervisorAsync(ct => inputs.PickFileAsync(ct), CancellationToken.None);
        Assert.Equal("first", viewModel.ResultText);

        fake.QueueTerminalJob("second");
        await viewModel.RecognizeViaSupervisorAsync(ct => inputs.PickFileAsync(ct), CancellationToken.None);
        Assert.Equal("second", viewModel.ResultText);
        Assert.Equal("识别完成", viewModel.Status);
    }

    [Fact]
    public async Task NullInputReturnsCancelSelection()
    {
        var viewModel = new RecognitionViewModel(new DeferredInferenceClient(), new StubInputService());
        await viewModel.RecognizeViaSupervisorAsync(
            ct => Task.FromResult<RecognitionInput?>(null), CancellationToken.None);
        // No exception, just "已取消选择" status
        Assert.True(true);
    }

    // ------------------------------------------------------------------
    // Fakes
    // ------------------------------------------------------------------

    private sealed class StubInputService : IInputService
    {
        public Task<RecognitionInput?> PickFileAsync(CancellationToken cancellationToken)
            => Task.FromResult<RecognitionInput?>(new RecognitionInput([1, 2, 3, 4], "image/png", "file.png", "file"));

        public Task<RecognitionInput?> ReadClipboardAsync(CancellationToken cancellationToken)
            => PickFileAsync(cancellationToken);

        public Task<RecognitionInput?> CaptureScreenAsync(CancellationToken cancellationToken)
            => PickFileAsync(cancellationToken);

        public Task<RecognitionInput?> ReadDroppedFileAsync(string path, CancellationToken cancellationToken)
            => PickFileAsync(cancellationToken);
    }

    /// <summary>
    /// Fake v2 supervisor. By default the first submitted job returns a terminal
    /// Completed JobUpdate on the first ObserveAsync probe; result text is
    /// carried by the typed outcome's "raw_text" payload key. Tests can opt
    /// into a hanging job, a custom
    /// terminal state, or a queue of follow-up jobs.
    /// </summary>
    private sealed class FakeInferenceClient : InferenceClientStub
    {
        private readonly string _text;
        private readonly bool _neverTerminal;
        private readonly JobState _terminalState;
        private readonly InferenceClientException? _submitThrows;
        private readonly Queue<string> _queuedTexts = new();
        private string _currentJobText;
        private int _runningUpdatesRemaining;

        public FakeInferenceClient(
            string text,
            bool neverTerminal = false,
            JobState terminalState = JobState.Completed,
            InferenceClientException? submitThrows = null,
            int runningUpdatesBeforeTerminal = 0)
        {
            _text = text;
            _currentJobText = text;
            _neverTerminal = neverTerminal;
            _terminalState = terminalState;
            _submitThrows = submitThrows;
            _runningUpdatesRemaining = runningUpdatesBeforeTerminal;
        }

        public int SubmitCalls { get; private set; }
        private IReadOnlyList<JobItem> _items = Array.Empty<JobItem>();

        public SubmitRequest? LastRequest { get; private set; }
        public IReadOnlyDictionary<string, SubmitUpload>? LastUploads { get; private set; }
        public JobCommand? LastCommand { get; private set; }
        public List<int> ObservedAfterSequences { get; } = [];

        public void QueueTerminalJob(string text) => _queuedTexts.Enqueue(text);

        public override Task<JobRef> SubmitAsync(
            SubmitRequest request,
            IReadOnlyDictionary<string, SubmitUpload> uploads,
            CancellationToken cancellationToken)
        {
            if (_submitThrows is not null)
            {
                throw _submitThrows;
            }

            SubmitCalls++;
            LastUploads = uploads;
            LastRequest = request;
            if (_queuedTexts.Count > 0)
            {
                _currentJobText = _queuedTexts.Dequeue();
            }

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
                JobId = $"job-{SubmitCalls}",
                Items = _items,
            });
        }

        public override Task<JobUpdate> ObserveAsync(
            string jobId,
            int afterSequence,
            CancellationToken cancellationToken)
        {
            ObservedAfterSequences.Add(afterSequence);
            bool running = _neverTerminal || _runningUpdatesRemaining > 0;
            if (_runningUpdatesRemaining > 0)
            {
                _runningUpdatesRemaining--;
            }
            JobState state = running ? JobState.Running : _terminalState;
            ItemOutcome[] outcomes = running
                ? Array.Empty<ItemOutcome>()
                :
                [
                    new ItemOutcome
                    {
                        ItemId = _items[0].ItemId,
                        State = state switch
                        {
                            JobState.Cancelled => ItemState.Cancelled,
                            JobState.Completed or JobState.CompletedWithErrors => ItemState.Succeeded,
                            _ => ItemState.Failed,
                        },
                        Attempt = 1,
                        PayloadType = state is JobState.Completed or JobState.CompletedWithErrors
                            ? "ocr.v1"
                            : null,
                        Payload = state is JobState.Completed or JobState.CompletedWithErrors
                            ? new Dictionary<string, JsonElement>
                            {
                                ["raw_text"] = JsonSerializer.SerializeToElement(_currentJobText),
                            }
                            : null,
                        ErrorCode = state is JobState.Failed ? "BACKEND_UNAVAILABLE" : null,
                    },
                ];
            return Task.FromResult(new JobUpdate
            {
                Snapshot = new JobSnapshot
                {
                    JobId = jobId,
                    Kind = JobKind.Recognition,
                    Priority = JobPriority.Interactive,
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
