// Deferred inference gateway used while the Supervisor is starting.
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Inference;

/// <summary>
/// An <see cref="IInferenceClient"/> whose calls delegate to an attached inner
/// client once <see cref="Attach"/> is called; before that, every call throws.
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
            "The inference Supervisor client is not attached. "
            + "Wait for Supervisor startup to complete or inspect diagnostics.");

    public Task<JobRef> SubmitAsync(
        SubmitRequest request,
        IReadOnlyDictionary<string, SubmitUpload> uploads,
        CancellationToken cancellationToken)
        => Current.SubmitAsync(request, uploads, cancellationToken);

    public Task<JobUpdate> ObserveAsync(
        string jobId, int afterSequence, CancellationToken cancellationToken)
        => Current.ObserveAsync(jobId, afterSequence, cancellationToken);

    public Task<JobCommandResult> CommandAsync(
        JobCommand command, CancellationToken cancellationToken)
        => Current.CommandAsync(command, cancellationToken);

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
