using VibeOCR.App.Features.Pdf;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class PdfViewModelSupervisorTests
{
    [Fact]
    public async Task NoSessionReturnsEarly()
    {
        var viewModel = new PdfViewModel(new StubPdfInference(), new StubPdfSource());
        await viewModel.StartOcrAsync([0], false, CancellationToken.None);
        Assert.Equal("请先打开 PDF", viewModel.Status);
    }

    private sealed class StubPdfSource : IPdfFileSource
    {
        public Task<string?> PickFileAsync(CancellationToken ct) => Task.FromResult<string?>("test.pdf");
    }

    private sealed class StubPdfInference : IInferenceClient
    {
        public Uri BaseUrl => new("http://127.0.0.1:1");
        public Task<JobRef> SubmitRecognitionAsync(IReadOnlyList<RecognitionUpload> uploads, JobPriority priority, CancellationToken ct) => throw new NotImplementedException();
        public Task<JobSnapshot> GetJobAsync(string jobId, CancellationToken ct) => throw new NotImplementedException();
        public Task<IReadOnlyList<StageEvent>> GetEventsAsync(string jobId, int afterSequence, CancellationToken ct) => throw new NotImplementedException();
        public Task<IReadOnlyList<ResultEntry>> GetResultAsync(string jobId, CancellationToken ct) => throw new NotImplementedException();
        public Task<CancelMode> CancelAsync(string jobId, CancellationToken ct) => throw new NotImplementedException();
        public Task DeleteJobAsync(string jobId, CancellationToken ct) => throw new NotImplementedException();
        public Task<ResidencyStatus> GetResidencyAsync(CancellationToken ct) => throw new NotImplementedException();
        public Task<SettingsSnapshot> GetSettingsAsync(CancellationToken ct) => throw new NotImplementedException();
        public Task<ExportResult> ExportAsync(ExportRequest request, CancellationToken ct) => throw new NotImplementedException();
        public Task<PdfSessionOpenResult> OpenPdfSessionAsync(string path, string? password, CancellationToken ct) => throw new NotImplementedException();
        public Task<byte[]> RenderPdfPageAsync(string sessionId, int page, int size, CancellationToken ct) => throw new NotImplementedException();
        public Task<PdfMutateResult> RotatePdfPagesAsync(string sessionId, int[] pages, int angle, CancellationToken ct) => throw new NotImplementedException();
        public Task<PdfMutateResult> DeletePdfPagesAsync(string sessionId, int[] pages, CancellationToken ct) => throw new NotImplementedException();
        public Task<string> SavePdfAsync(string sessionId, string outputPath, CancellationToken ct) => throw new NotImplementedException();
        public Task ClosePdfSessionAsync(string sessionId, CancellationToken ct) => throw new NotImplementedException();
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
