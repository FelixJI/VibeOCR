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

    /// <summary>Submit a recognition job (one or many inputs). Returns the JobRef.</summary>
    Task<JobRef> SubmitRecognitionAsync(
        IReadOnlyList<RecognitionUpload> uploads,
        JobPriority priority,
        CancellationToken cancellationToken);

    /// <summary>Fetch the current snapshot of a job.</summary>
    Task<JobSnapshot> GetJobAsync(string jobId, CancellationToken cancellationToken);

    /// <summary>Long-poll events strictly after <paramref name="afterSequence"/>.</summary>
    Task<IReadOnlyList<StageEvent>> GetEventsAsync(
        string jobId, int afterSequence, CancellationToken cancellationToken);

    /// <summary>Fetch the stable-ordered result entries for a job.</summary>
    Task<IReadOnlyList<ResultEntry>> GetResultAsync(string jobId, CancellationToken cancellationToken);

    /// <summary>Request cancellation; returns the actual cancel mode the supervisor will apply.</summary>
    Task<CancelMode> CancelAsync(string jobId, CancellationToken cancellationToken);

    /// <summary>Release a terminal job's staging/results.</summary>
    Task DeleteJobAsync(string jobId, CancellationToken cancellationToken);

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
/// One input for a recognition submission. <see cref="Content"/> is the raw
/// image/document bytes; <see cref="FileName"/> is a display-only name (the
/// supervisor generates internal ids and never trusts it as a path).
/// </summary>
public sealed record RecognitionUpload(string FileName, string ContentType, IReadOnlyList<byte> Content);

/// <summary>Export request mirroring the v2 /v2/export endpoint.</summary>
public sealed record ExportRequest(
    string RawText, string MarkdownText, string HtmlText,
    string OutputPath, string Format, bool Overwrite);

/// <summary>Export result from the v2 supervisor.</summary>
public sealed record ExportResult(string OutputPath, long BytesWritten);
