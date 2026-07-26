// Phase 7B: WinUI inference supervisor client surface.
//
// The plan (§7B) requires:
//   * a new IInferenceClient/InferenceHttpClient based on HttpClient, typed
//     DTOs and multipart streaming;
//   * an InferenceSupervisorProcess reusing log/Job Object/whole-tree
//     termination and startup-error presentation.
//
// This file declares the transport-neutral IInferenceClient interface. The
// concrete InferenceHttpClient (HttpClient + HttpV2 DTOs) lives alongside;
// InferenceSupervisorProcess owns the child process lifecycle.
using VibeOCR.Contracts.HttpV2;

namespace VibeOCR.Platform.Inference;

/// <summary>
/// Transport-neutral v2 supervisor client used by WinUI ViewModels.
/// Mirrors the Python SupervisorClient surface so the two front-ends share
/// one contract.
/// </summary>
public interface IInferenceClient : IAsyncDisposable
{
    /// <summary>Supervisor base URL (always loopback).</summary>
    Uri BaseUrl { get; }

    /// <summary>
    /// Submit one logical job. Multipart uploads are keyed by the attachment
    /// names referenced by <see cref="SubmitItem.Source"/>.
    /// </summary>
    Task<JobRef> SubmitAsync(
        SubmitRequest request,
        IReadOnlyDictionary<string, SubmitUpload> uploads,
        CancellationToken cancellationToken);

    /// <summary>
    /// Atomically observe the current snapshot, ordered events and typed
    /// outcomes after <paramref name="afterSequence"/>.
    /// </summary>
    Task<JobUpdate> ObserveAsync(
        string jobId, int afterSequence, CancellationToken cancellationToken);

    /// <summary>Cancel, retry or forget a job through the generic command seam.</summary>
    Task<JobCommandResult> CommandAsync(JobCommand command, CancellationToken cancellationToken);

    /// <summary>Residency status (model TTL/pin/LRU/VRAM).</summary>
    Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken);

    /// <summary>Backend settings snapshot.</summary>
    Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken);

    /// <summary>Export OCR result to a file (txt/markdown/html) via the supervisor.</summary>
    Task<ExportResult> ExportAsync(ExportRequest request, CancellationToken cancellationToken);

    // PDF session operations (v2 — proxied through supervisor)
    Task<PdfSessionOpenResult> OpenPdfSessionAsync(string path, string? password, CancellationToken ct);
    Task<byte[]> RenderPdfPageAsync(string sessionId, int page, int size, CancellationToken ct);
    Task<PdfMutateResult> RotatePdfPagesAsync(string sessionId, int[] pages, int angle, CancellationToken ct);
    Task<PdfMutateResult> DeletePdfPagesAsync(string sessionId, int[] pages, CancellationToken ct);
    Task<string> SavePdfAsync(string sessionId, string outputPath, CancellationToken ct);
    Task ClosePdfSessionAsync(string sessionId, CancellationToken ct);
}

/// <summary>
/// Multipart content for one attachment referenced by a
/// <see cref="SubmitItem"/>. Display names belong to the manifest rather than
/// the transport payload.
/// </summary>
public sealed record SubmitUpload(string? ContentType, IReadOnlyList<byte> Content);

/// <summary>
/// Result of a generic job command. Cancel returns <see cref="CancelMode"/>,
/// retry returns <see cref="JobRef"/>, and forget returns neither.
/// </summary>
public sealed record JobCommandResult(
    string CommandId,
    JobCommandKind Kind,
    CancelMode? CancelMode,
    JobRef? JobRef);

/// <summary>Export request mirroring the v2 /v2/export endpoint.</summary>
public sealed record ExportRequest(
    string RawText, string MarkdownText, string HtmlText,
    string OutputPath, string Format, bool Overwrite);

/// <summary>Export result from the v2 supervisor.</summary>
public sealed record ExportResult(string OutputPath, long BytesWritten);
