using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Tests;

/// <summary>
/// Generic-job test adapter. Individual tests override only the seams they
/// exercise; no legacy recognition/status/events/result facade exists here.
/// </summary>
internal abstract class InferenceClientStub : IInferenceClient
{
    public virtual Uri BaseUrl => new("http://127.0.0.1:1");

    public virtual Task<JobRef> SubmitAsync(
        SubmitRequest request,
        IReadOnlyDictionary<string, SubmitUpload> uploads,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<JobUpdate> ObserveAsync(
        string jobId,
        int afterSequence,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<JobCommandResult> CommandAsync(
        JobCommand command,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<ExportResult> ExportAsync(
        ExportRequest request,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<PdfSessionOpenResult> OpenPdfSessionAsync(
        string path,
        string? password,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<byte[]> RenderPdfPageAsync(
        string sessionId,
        int page,
        int size,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<PdfMutateResult> RotatePdfPagesAsync(
        string sessionId,
        int[] pages,
        int angle,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<PdfMutateResult> DeletePdfPagesAsync(
        string sessionId,
        int[] pages,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task<string> SavePdfAsync(
        string sessionId,
        string outputPath,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual Task ClosePdfSessionAsync(
        string sessionId,
        CancellationToken cancellationToken) =>
        throw new NotImplementedException();

    public virtual ValueTask DisposeAsync() => ValueTask.CompletedTask;
}
