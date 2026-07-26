// Phase 8 blocker fix: PDF session client for v2 supervisor PDF endpoints.
// Mirrors the supervisor's /v2/pdf/sessions/* routes so PdfViewModel can
// open/render/rotate/delete/save/close through the Supervisor HTTP contract.
namespace VibeOCR.Platform.Inference;

public sealed record PdfSessionOpenResult(string SessionId, int PageCount, string FilePath);
public sealed record PdfMutateResult(int PageCount);

/// <summary>
/// PDF session operations via the v2 supervisor. The supervisor owns the PDF
/// child process; these methods proxy through it.
/// </summary>
public interface IPdfSessionClient : IAsyncDisposable
{
    Task<PdfSessionOpenResult> OpenAsync(string path, string? password, CancellationToken ct);
    Task<byte[]> RenderAsync(string sessionId, int page, int size, CancellationToken ct);
    Task<PdfMutateResult> RotateAsync(string sessionId, int[] pages, int angle, CancellationToken ct);
    Task<PdfMutateResult> DeletePagesAsync(string sessionId, int[] pages, CancellationToken ct);
    Task<string> SaveAsync(string sessionId, string outputPath, CancellationToken ct);
    Task CloseAsync(string sessionId, CancellationToken ct);
}
