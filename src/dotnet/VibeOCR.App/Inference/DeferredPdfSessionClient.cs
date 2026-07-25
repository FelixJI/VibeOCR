// Deferred PDF session client mirroring DeferredInferenceClient.
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Inference;

public sealed class DeferredPdfSessionClient : IPdfSessionClient
{
    private IPdfSessionClient? _inner;

    public bool IsAttached => Volatile.Read(ref _inner) is not null;

    public void Attach(IPdfSessionClient client)
    {
        ArgumentNullException.ThrowIfNull(client);
        Interlocked.Exchange(ref _inner, client);
    }

    public void Detach() => Interlocked.Exchange(ref _inner, null);

    private IPdfSessionClient Current =>
        Volatile.Read(ref _inner)
        ?? throw new InvalidOperationException("v2 PDF session client not attached; Phase 8 pending.");

    public Task<PdfSessionOpenResult> OpenAsync(string path, string? password, CancellationToken ct)
        => Current.OpenAsync(path, password, ct);
    public Task<byte[]> RenderAsync(string sessionId, int page, int size, CancellationToken ct)
        => Current.RenderAsync(sessionId, page, size, ct);
    public Task<PdfMutateResult> RotateAsync(string sessionId, int[] pages, int angle, CancellationToken ct)
        => Current.RotateAsync(sessionId, pages, angle, ct);
    public Task<PdfMutateResult> DeletePagesAsync(string sessionId, int[] pages, CancellationToken ct)
        => Current.DeletePagesAsync(sessionId, pages, ct);
    public Task<string> SaveAsync(string sessionId, string outputPath, CancellationToken ct)
        => Current.SaveAsync(sessionId, outputPath, ct);
    public Task CloseAsync(string sessionId, CancellationToken ct)
        => Current.CloseAsync(sessionId, ct);
    public ValueTask DisposeAsync()
    {
        Interlocked.Exchange(ref _inner, null);
        return ValueTask.CompletedTask;
    }
}
