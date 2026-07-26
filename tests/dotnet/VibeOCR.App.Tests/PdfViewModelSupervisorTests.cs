using System.Text.Json;
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
        var viewModel = new PdfViewModel(new FakePdfInference(), new StubPdfSource());

        await viewModel.StartOcrAsync([0], false, CancellationToken.None);

        Assert.Equal("请先打开 PDF", viewModel.Status);
    }

    [Fact]
    public async Task OcrRendersPagesThenSubmitsOneGenericRecognitionJob()
    {
        var fake = new FakePdfInference();
        var viewModel = new PdfViewModel(fake, new StubPdfSource());
        await viewModel.OpenPathAsync("test.pdf", CancellationToken.None);

        await viewModel.StartOcrAsync([0, 1], false, CancellationToken.None);

        Assert.Equal(2, fake.RenderCalls);
        Assert.Equal(1, fake.SubmitCalls);
        Assert.Equal(JobKind.Recognition, fake.LastRequest?.Kind);
        Assert.Equal(JobPriority.Background, fake.LastRequest?.Priority);
        Assert.Equal("OCR", fake.LastRequest?.Pipeline.PipelineId);
        Assert.Equal(["page-0", "page-1"],
            fake.LastRequest!.Items.Select(item => item.ClientItemKey));
        Assert.Equal(2, fake.LastUploads?.Count);
        Assert.Equal(PdfPageState.Done, viewModel.Pages[0].State);
        Assert.Equal("ocr-page-0", viewModel.Pages[0].OcrText);
        Assert.Equal(PdfPageState.Done, viewModel.Pages[1].State);
        Assert.Equal("ocr-page-1", viewModel.Pages[1].OcrText);
        Assert.Equal("OCR 完成：成功 2 页，失败 0 页", viewModel.Status);
    }

    private sealed class StubPdfSource : IPdfFileSource
    {
        public Task<string?> PickFileAsync(CancellationToken ct) =>
            Task.FromResult<string?>("test.pdf");
    }

    private sealed class FakePdfInference : InferenceClientStub
    {
        private IReadOnlyList<JobItem> _items = Array.Empty<JobItem>();

        public int RenderCalls { get; private set; }
        public int SubmitCalls { get; private set; }
        public SubmitRequest? LastRequest { get; private set; }
        public IReadOnlyDictionary<string, SubmitUpload>? LastUploads { get; private set; }

        public override Task<PdfSessionOpenResult> OpenPdfSessionAsync(
            string path,
            string? password,
            CancellationToken ct) =>
            Task.FromResult(new PdfSessionOpenResult("pdf-1", 2, path));

        public override Task<byte[]> RenderPdfPageAsync(
            string sessionId,
            int page,
            int size,
            CancellationToken ct)
        {
            RenderCalls++;
            return Task.FromResult(new byte[] { (byte)page, 1, 2 });
        }

        public override Task<JobRef> SubmitAsync(
            SubmitRequest request,
            IReadOnlyDictionary<string, SubmitUpload> uploads,
            CancellationToken cancellationToken)
        {
            SubmitCalls++;
            LastRequest = request;
            LastUploads = uploads;
            _items = request.Items.Select((item, index) => new JobItem
            {
                ItemId = $"pdf-item-{index}",
                ClientItemKey = item.ClientItemKey,
                Ordinal = item.Ordinal,
                DisplayName = item.DisplayName,
                State = ItemState.Queued,
            }).ToArray();
            return Task.FromResult(new JobRef
            {
                JobId = "pdf-ocr-1",
                Items = _items,
            });
        }

        public override Task<JobUpdate> ObserveAsync(
            string jobId,
            int afterSequence,
            CancellationToken cancellationToken)
        {
            // Reverse wire order to prove page mapping uses client item keys.
            ItemOutcome[] outcomes = _items.Reverse().Select(item => new ItemOutcome
            {
                ItemId = item.ItemId,
                State = ItemState.Succeeded,
                Attempt = 1,
                PayloadType = "ocr.v1",
                Payload = new Dictionary<string, JsonElement>
                {
                    ["raw_text"] = JsonSerializer.SerializeToElement(
                        $"ocr-{item.ClientItemKey}"),
                },
            }).ToArray();
            return Task.FromResult(new JobUpdate
            {
                Snapshot = new JobSnapshot
                {
                    JobId = jobId,
                    Kind = JobKind.Recognition,
                    Priority = JobPriority.Background,
                    State = JobState.Completed,
                    Items = _items,
                },
                Events = Array.Empty<StageEvent>(),
                Outcomes = outcomes,
                ThroughSequence = afterSequence + 1,
            });
        }
    }
}
