using VibeOCR.App.Inference;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class DeferredInferenceClientTests
{
    [Fact]
    public async Task UnattachedGenericJobCallThrowsAsync()
    {
        var deferred = new DeferredInferenceClient();

        Assert.False(deferred.IsAttached);
        await Assert.ThrowsAsync<InvalidOperationException>(
            () => deferred.ObserveAsync("job-1", 0, CancellationToken.None));
    }

    [Fact]
    public async Task AttachedClientDelegatesGenericJobCallsAsync()
    {
        var deferred = new DeferredInferenceClient();
        var inner = new StubInferenceClient();
        deferred.Attach(inner);
        var request = new SubmitRequest
        {
            RequestId = "request-1",
            Kind = JobKind.Recognition,
            Priority = JobPriority.Interactive,
            Pipeline = new PipelineSelection { PipelineId = "OCR" },
            Items = Array.Empty<SubmitItem>(),
        };
        var uploads = new Dictionary<string, SubmitUpload>();

        JobRef job = await deferred.SubmitAsync(request, uploads, CancellationToken.None);
        JobUpdate update = await deferred.ObserveAsync("job-1", 7, CancellationToken.None);
        JobCommandResult result = await deferred.CommandAsync(
            new JobCommand
            {
                CommandId = "command-1",
                Kind = JobCommandKind.Cancel,
                JobId = "job-1",
            },
            CancellationToken.None);

        Assert.Same(request, inner.LastSubmitRequest);
        Assert.Same(uploads, inner.LastUploads);
        Assert.Equal("job-1", job.JobId);
        Assert.Equal(("job-1", 7), inner.LastObserve);
        Assert.Equal(7, update.ThroughSequence);
        Assert.Equal("command-1", inner.LastCommand?.CommandId);
        Assert.Equal(CancelMode.Cooperative, result.CancelMode);
    }

    [Fact]
    public async Task ExistingNonJobCallsStillDelegateAsync()
    {
        var deferred = new DeferredInferenceClient();
        var inner = new StubInferenceClient(defaultTtl: 600);
        deferred.Attach(inner);

        ResidencyStatus status = await deferred.GetResidencyAsync(CancellationToken.None);

        Assert.Equal(600, status.DefaultTtlSeconds);
    }

    [Fact]
    public async Task DetachRestoresThrowingStateAsync()
    {
        var deferred = new DeferredInferenceClient();
        var inner = new StubInferenceClient();
        deferred.Attach(inner);
        deferred.Detach(inner);

        Assert.False(deferred.IsAttached);
        await Assert.ThrowsAsync<InvalidOperationException>(
            () => deferred.ObserveAsync("job-1", 0, CancellationToken.None));
    }

    [Fact]
    public async Task DisposeAsyncDetachesAndDisposesInner()
    {
        var deferred = new DeferredInferenceClient();
        var inner = new StubInferenceClient();
        deferred.Attach(inner);

        await deferred.DisposeAsync();

        Assert.False(deferred.IsAttached);
        Assert.True(inner.Disposed);
    }

    private sealed class StubInferenceClient : IInferenceClient
    {
        public StubInferenceClient(int defaultTtl = 300) => DefaultTtl = defaultTtl;

        public int DefaultTtl { get; }
        public bool Disposed { get; private set; }
        public SubmitRequest? LastSubmitRequest { get; private set; }
        public IReadOnlyDictionary<string, SubmitUpload>? LastUploads { get; private set; }
        public (string JobId, int AfterSequence)? LastObserve { get; private set; }
        public JobCommand? LastCommand { get; private set; }
        public Uri BaseUrl => new("http://127.0.0.1:1");

        public Task<JobRef> SubmitAsync(
            SubmitRequest request,
            IReadOnlyDictionary<string, SubmitUpload> uploads,
            CancellationToken cancellationToken)
        {
            LastSubmitRequest = request;
            LastUploads = uploads;
            return Task.FromResult(new JobRef { JobId = "job-1" });
        }

        public Task<JobUpdate> ObserveAsync(
            string jobId,
            int afterSequence,
            CancellationToken cancellationToken)
        {
            LastObserve = (jobId, afterSequence);
            return Task.FromResult(new JobUpdate
            {
                Snapshot = new JobSnapshot
                {
                    JobId = jobId,
                    Kind = JobKind.Recognition,
                    Priority = JobPriority.Interactive,
                    State = JobState.Running,
                },
                Events = Array.Empty<StageEvent>(),
                Outcomes = Array.Empty<ItemOutcome>(),
                ThroughSequence = afterSequence,
            });
        }

        public Task<JobCommandResult> CommandAsync(
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

        public Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken)
            => Task.FromResult(new ResidencyStatus { DefaultTtlSeconds = DefaultTtl });

        public Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken)
            => Task.FromResult(new SettingsSnapshot());

        public Task<ExportResult> ExportAsync(ExportRequest request, CancellationToken ct)
            => throw new NotImplementedException();

        public Task<PdfSessionOpenResult> OpenPdfSessionAsync(
            string path,
            string? password,
            CancellationToken ct)
            => throw new NotImplementedException();

        public Task<byte[]> RenderPdfPageAsync(
            string sessionId,
            int page,
            int size,
            CancellationToken ct)
            => throw new NotImplementedException();

        public Task<PdfMutateResult> RotatePdfPagesAsync(
            string sessionId,
            int[] pages,
            int angle,
            CancellationToken ct)
            => throw new NotImplementedException();

        public Task<PdfMutateResult> DeletePdfPagesAsync(
            string sessionId,
            int[] pages,
            CancellationToken ct)
            => throw new NotImplementedException();

        public Task<string> SavePdfAsync(
            string sessionId,
            string outputPath,
            CancellationToken ct)
            => throw new NotImplementedException();

        public Task ClosePdfSessionAsync(string sessionId, CancellationToken ct)
            => throw new NotImplementedException();

        public ValueTask DisposeAsync()
        {
            Disposed = true;
            return ValueTask.CompletedTask;
        }
    }
}
