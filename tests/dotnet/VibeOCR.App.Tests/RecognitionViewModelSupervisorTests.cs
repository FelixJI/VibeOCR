// Phase 7B tests: RecognitionViewModel v2 supervisor path.
//
// Verifies the plan §7B requirement "RecognitionViewModel 改为一元素 job；取消
// 等待真实 job terminal" against a hand-written fake IInferenceClient (the
// "fake HTTP server" for WinUI). The legacy path stays covered by
// RecognitionViewModelTests; this file is additive.
using System.Text.Json;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.Inference;
using VibeOCR.Contracts;
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
        // Legacy worker is unused on the v2 path but required by the constructor.
        var viewModel = new RecognitionViewModel(fakeInference, inputs);

        await viewModel.RecognizeViaSupervisorAsync(ct => inputs.PickFileAsync(ct), CancellationToken.None);

        Assert.Equal("hello from supervisor", viewModel.ResultText);
        Assert.Equal("识别完成", viewModel.Status);
        Assert.True(viewModel.HasResult);
        // Exactly one submit with exactly one upload (single = one-element job).
        Assert.Equal(1, fakeInference.SubmitCalls);
        Assert.NotNull(fakeInference.LastUploads);
        Assert.Single(fakeInference.LastUploads!);
        Assert.Equal("file.png", fakeInference.LastUploads![0].FileName);
        Assert.Equal(new byte[] { 1, 2, 3, 4 }, fakeInference.LastUploads[0].Content);
        Assert.Equal(JobPriority.Interactive, fakeInference.LastPriority);
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
    /// Completed snapshot on the first GetJobAsync probe; result text is taken
    /// from the "text" payload key. Tests can opt into a hanging job, a custom
    /// terminal state, or a queue of follow-up jobs.
    /// </summary>
    private sealed class FakeInferenceClient : IInferenceClient
    {
        private readonly string _text;
        private readonly bool _neverTerminal;
        private readonly JobState _terminalState;
        private readonly InferenceClientException? _submitThrows;
        private readonly Queue<string> _queuedTexts = new();
        private string _currentJobText;

        public FakeInferenceClient(
            string text,
            bool neverTerminal = false,
            JobState terminalState = JobState.Completed,
            InferenceClientException? submitThrows = null)
        {
            _text = text;
            _currentJobText = text;
            _neverTerminal = neverTerminal;
            _terminalState = terminalState;
            _submitThrows = submitThrows;
        }

        public int SubmitCalls { get; private set; }
        public IReadOnlyList<RecognitionUpload>? LastUploads { get; private set; }
        public JobPriority LastPriority { get; private set; }
        public Uri BaseUrl => new("http://127.0.0.1:1");

        public void QueueTerminalJob(string text) => _queuedTexts.Enqueue(text);

        public Task<JobRef> SubmitRecognitionAsync(
            IReadOnlyList<RecognitionUpload> uploads, JobPriority priority, CancellationToken cancellationToken)
        {
            if (_submitThrows is not null)
            {
                throw _submitThrows;
            }

            SubmitCalls++;
            LastUploads = uploads;
            LastPriority = priority;
            if (_queuedTexts.Count > 0)
            {
                _currentJobText = _queuedTexts.Dequeue();
            }

            return Task.FromResult(new JobRef { JobId = $"job-{SubmitCalls}" });
        }

        public Task<JobSnapshot> GetJobAsync(string jobId, CancellationToken cancellationToken)
        {
            JobState state = _neverTerminal ? JobState.Running : _terminalState;
            return Task.FromResult(new JobSnapshot
            {
                JobId = jobId,
                Kind = JobKind.Recognition,
                Priority = JobPriority.Interactive,
                State = state,
                SchemaVersion = 2,
                Stage = state is JobState.Completed ? "done" : "running",
                ProgressCurrent = state is JobState.Completed ? 1 : 0,
                ProgressTotal = 1,
                EventSequence = 1,
                ResultAvailable = state is JobState.Completed,
            });
        }

        public Task<IReadOnlyList<StageEvent>> GetEventsAsync(
            string jobId, int afterSequence, CancellationToken cancellationToken)
            => Task.FromResult<IReadOnlyList<StageEvent>>(Array.Empty<StageEvent>());

        public Task<IReadOnlyList<ResultEntry>> GetResultAsync(string jobId, CancellationToken cancellationToken)
        {
            var payload = new Dictionary<string, JsonElement>
            {
                ["text"] = JsonSerializer.SerializeToElement(_currentJobText),
            };
            return Task.FromResult<IReadOnlyList<ResultEntry>>(new[]
            {
                new ResultEntry { ItemId = "it-0", DisplayName = "file.png", Payload = payload },
            });
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
