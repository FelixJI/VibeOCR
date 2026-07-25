// Phase 8 wiring: a deferred IInferenceClient that mirrors DeferredWorkerHostClient.
//
// Until the Phase 8 atomic switch actually starts the supervisor process and
// attaches a real InferenceHttpClient, this client throws
// InvalidOperationException on any call. It lets the composition root wire the
// v2 constructors of Recognition/Batch/Settings/Pdf ViewModels NOW (so the
// production ViewModels are v2-capable instances) without flipping execution
// to the supervisor path. The final switch is a single Attach() call plus the
// supervisor-process lifecycle, done in the same change that deletes the
// legacy worker.
//
// This keeps the rewrite branch internally consistent: ViewModels hold a real
// IInferenceClient reference (exercising the v2 seam in tests), but production
// still runs the legacy path until Attach() — matching the plan's "no runtime
// dual-stack, atomic switch" rule (the branch is not released until Phase 8
// completes, so a deferred-throw client is the safe pre-switch state).
using System.Text.Json;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Inference;

/// <summary>
/// An <see cref="IInferenceClient"/> whose calls delegate to an attached inner
/// client once <see cref="Attach"/> is called; before that, every call throws.
/// Mirrors <c>DeferredWorkerHostClient</c>.
/// </summary>
public sealed class DeferredInferenceClient : IInferenceClient
{
    private IInferenceClient? _inner;
    private readonly object _lock = new();

    public bool IsAttached => Volatile.Read(ref _inner) is not null;

    public Uri BaseUrl => Current.BaseUrl;

    public void Attach(IInferenceClient client)
    {
        ArgumentNullException.ThrowIfNull(client);
        lock (_lock)
        {
            _inner = client;
        }
    }

    public void Detach(IInferenceClient client)
    {
        lock (_lock)
        {
            if (ReferenceEquals(_inner, client))
            {
                _inner = null;
            }
        }
    }

    private IInferenceClient Current =>
        Volatile.Read(ref _inner)
        ?? throw new InvalidOperationException(
            "v2 inference supervisor client is not attached yet; the atomic switch (Phase 8) "
            + "has not started the supervisor process. Use the legacy worker path until then.");

    public Task<JobRef> SubmitRecognitionAsync(
        IReadOnlyList<RecognitionUpload> uploads, JobPriority priority, CancellationToken cancellationToken)
        => Current.SubmitRecognitionAsync(uploads, priority, cancellationToken);

    public Task<JobSnapshot> GetJobAsync(string jobId, CancellationToken cancellationToken)
        => Current.GetJobAsync(jobId, cancellationToken);

    public Task<IReadOnlyList<StageEvent>> GetEventsAsync(
        string jobId, int afterSequence, CancellationToken cancellationToken)
        => Current.GetEventsAsync(jobId, afterSequence, cancellationToken);

    public Task<IReadOnlyList<ResultEntry>> GetResultAsync(string jobId, CancellationToken cancellationToken)
        => Current.GetResultAsync(jobId, cancellationToken);

    public Task<CancelMode> CancelAsync(string jobId, CancellationToken cancellationToken)
        => Current.CancelAsync(jobId, cancellationToken);

    public Task DeleteJobAsync(string jobId, CancellationToken cancellationToken)
        => Current.DeleteJobAsync(jobId, cancellationToken);

    public Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken)
        => Current.GetResidencyAsync(cancellationToken);

    public Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken)
        => Current.GetSettingsAsync(cancellationToken);

    public Task<ExportResult> ExportAsync(ExportRequest request, CancellationToken cancellationToken)
        => Current.ExportAsync(request, cancellationToken);

    public Task<PdfSessionOpenResult> OpenPdfSessionAsync(string path, string? password, CancellationToken ct)
        => Current.OpenPdfSessionAsync(path, password, ct);
    public Task<byte[]> RenderPdfPageAsync(string sessionId, int page, int size, CancellationToken ct)
        => Current.RenderPdfPageAsync(sessionId, page, size, ct);
    public Task<PdfMutateResult> RotatePdfPagesAsync(string sessionId, int[] pages, int angle, CancellationToken ct)
        => Current.RotatePdfPagesAsync(sessionId, pages, angle, ct);
    public Task<PdfMutateResult> DeletePdfPagesAsync(string sessionId, int[] pages, CancellationToken ct)
        => Current.DeletePdfPagesAsync(sessionId, pages, ct);
    public Task<string> SavePdfAsync(string sessionId, string outputPath, CancellationToken ct)
        => Current.SavePdfAsync(sessionId, outputPath, ct);
    public Task ClosePdfSessionAsync(string sessionId, CancellationToken ct)
        => Current.ClosePdfSessionAsync(sessionId, ct);

    public ValueTask DisposeAsync()
    {
        IInferenceClient? inner = Interlocked.Exchange(ref _inner, null);
        if (inner is not null)
        {
            return inner.DisposeAsync();
        }

        return ValueTask.CompletedTask;
    }
}
