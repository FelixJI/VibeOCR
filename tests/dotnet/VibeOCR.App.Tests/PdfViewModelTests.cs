using System.Collections.Concurrent;
using System.Security.Cryptography;
using VibeOCR.App.Features.Pdf;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class PdfViewModelTests
{
    [Fact]
    public async Task OpenPopulatesPagesAndStatus()
    {
        var worker = new FakePdfWorker(openPageCount: 5);
        var files = new FakePdfFileSource("C:/data/sample.pdf");
        var viewModel = new PdfViewModel(worker, files);

        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        Assert.Equal("sess-1", viewModel.SessionId);
        Assert.Equal(5, viewModel.PageCount);
        Assert.Equal(5, viewModel.Pages.Count);
        Assert.Equal("已打开 5 页", viewModel.Status);
        Assert.False(viewModel.IsBusy);
        Assert.True(viewModel.HasSession);
    }

    [Fact]
    public async Task OpenCancelledSelectionSurfacesStatus()
    {
        var worker = new FakePdfWorker();
        var files = new FakePdfFileSource(null);
        var viewModel = new PdfViewModel(worker, files);

        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        Assert.Null(viewModel.SessionId);
        Assert.Equal("已取消选择", viewModel.Status);
    }

    [Fact]
    public async Task RotateWithoutSessionPromptsUser()
    {
        var viewModel = new PdfViewModel(new FakePdfWorker(), new FakePdfFileSource(null));

        await viewModel.RotateAsync([0], 90, TestContext.Current.CancellationToken);

        Assert.Equal("请先选中要旋转的页面", viewModel.Status);
    }

    [Fact]
    public async Task RotateSendsAngleAndUpdatesPageCount()
    {
        var worker = new FakePdfWorker(openPageCount: 3);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        await viewModel.RotateAsync([0, 1], 90, TestContext.Current.CancellationToken);

        Assert.NotNull(worker.LastRotate);
        Assert.Equal(90, worker.LastRotate.Angle);
        Assert.Equal([0, 1], worker.LastRotate.PageIndices);
        Assert.Equal("完成", viewModel.Status);
    }

    [Fact]
    public async Task RotateRejectsEmptySelection()
    {
        var worker = new FakePdfWorker(openPageCount: 3);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        await viewModel.RotateAsync([], 90, TestContext.Current.CancellationToken);

        Assert.Equal("请先选中要旋转的页面", viewModel.Status);
    }

    [Fact]
    public async Task StartOcrMarksPagesDoneAndReportsCompletion()
    {
        var worker = new FakePdfWorker(openPageCount: 3);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        await viewModel.StartOcrAsync([0, 1, 2], overwrite: false, TestContext.Current.CancellationToken);

        Assert.All(viewModel.Pages, p => Assert.Equal(PdfPageState.Done, p.State));
        Assert.Contains("成功 3 页", viewModel.Status);
        Assert.NotNull(worker.LastStartOcr);
        Assert.Equal("C:/data/sample.pdf", worker.LastStartOcr.FilePath);
        Assert.False(worker.LastStartOcr.Overwrite);
    }

    [Fact]
    public async Task StartOcrSurfacesAggregatedWriteErrors()
    {
        var worker = new FakePdfWorker(openPageCount: 2, ocrWriteErrors: ["disk full", "disk full"]);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        await viewModel.StartOcrAsync([0, 1], overwrite: false, TestContext.Current.CancellationToken);

        Assert.Contains("写层错误", viewModel.Status);
    }

    [Fact]
    public async Task SaveInPlaceSendsNullOutputPath()
    {
        var worker = new FakePdfWorker(openPageCount: 1);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        await viewModel.SaveAsync(null, TestContext.Current.CancellationToken);

        Assert.NotNull(worker.LastSave);
        Assert.Null(worker.LastSave.OutputPath);
        Assert.Contains("已保存", viewModel.Status);
    }

    [Fact]
    public async Task DeleteTextLayersReturnsCompletedStatus()
    {
        var worker = new FakePdfWorker(openPageCount: 3);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        await viewModel.DeleteTextLayersAsync([0, 1], TestContext.Current.CancellationToken);

        Assert.NotNull(worker.LastDeleteTextLayers);
        Assert.Equal([0, 1], worker.LastDeleteTextLayers.PageIndices);
        Assert.Equal("完成", viewModel.Status);
    }

    [Fact]
    public async Task WorkerErrorDuringOpenIsLocalized()
    {
        var worker = new FakePdfWorker(openError: ErrorCode.DependencyMissing);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));

        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        Assert.Equal("PDF 依赖尚未安装", viewModel.Status);
        Assert.False(viewModel.IsBusy);
    }

    [Fact]
    public async Task CloseSessionClearsState()
    {
        var worker = new FakePdfWorker(openPageCount: 3);
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        viewModel.CloseSession();

        Assert.Null(viewModel.SessionId);
        Assert.Empty(viewModel.Pages);
        Assert.Equal(0, viewModel.PageCount);
        Assert.False(viewModel.HasSession);
    }

    [Fact]
    public async Task RenderThumbnailReturnsImageBytesWhenSessionOpen()
    {
        var worker = new FakePdfWorker(openPageCount: 2) { ThumbnailBytes = [1, 2, 3, 4] };
        var viewModel = new PdfViewModel(worker, new FakePdfFileSource("C:/data/sample.pdf"));
        await viewModel.OpenAsync(TestContext.Current.CancellationToken);

        byte[]? thumb = await viewModel.RenderThumbnailAsync(0, TestContext.Current.CancellationToken);

        Assert.NotNull(thumb);
        Assert.Equal([1, 2, 3, 4], thumb);
    }

    private sealed class FakePdfFileSource(string? path) : IPdfFileSource
    {
        public Task<string?> PickFileAsync(CancellationToken cancellationToken) => Task.FromResult(path);
    }

    private sealed class FakePdfWorker(
        int openPageCount = 0,
        ErrorCode? openError = null,
        string[]? ocrWriteErrors = null) : IWorkerHostClient
    {
        public RotatePdfRequest? LastRotate { get; private set; }
        public StartPdfOcrRequest? LastStartOcr { get; private set; }
        public SavePdfRequest? LastSave { get; private set; }
        public DeletePdfTextLayersRequest? LastDeleteTextLayers { get; private set; }
        public byte[] ThumbnailBytes { get; set; } = [];

        public SharedPayloadRef CreatePayload(ReadOnlySpan<byte> data, string mediaType, TimeSpan ttl) => new()
        {
            Name = $@"Local\VibeOCR-pdf-{Guid.NewGuid():D}-{Guid.NewGuid():D}",
            Size = data.Length,
            MediaType = mediaType,
            Sha256 = Convert.ToHexStringLower(SHA256.HashData(data)),
            Owner = SharedPayloadOwner.Client,
            ExpiresUnixMs = DateTimeOffset.UtcNow.Add(ttl).ToUnixTimeMilliseconds(),
        };

        public bool ReleasePayload(string name) => true;

        public byte[] ReadPayload(SharedPayloadRef reference, TimeSpan timeout, CancellationToken cancellationToken)
            => ThumbnailBytes;

        public Task<TResponse> CallAsync<TRequest, TResponse>(
            string method, TRequest request, CancellationToken cancellationToken)
            where TRequest : IProtocolValidatable
            where TResponse : IProtocolValidatable
        {
            object response = method switch
            {
                RpcMethods.OpenPdf => openError is { } code
                    ? throw new WorkerRpcException(new RpcErrorBody { Code = code, Message = "err", Retryable = false })
                    : new OpenPdfResponse { SessionId = "sess-1", FilePath = Cast<OpenPdfRequest>(request).FilePath, PageCount = openPageCount },
                RpcMethods.RenderPdfPage => new RenderPdfPageResponse
                {
                    Image = new SharedPayloadRef
                    {
                        Name = "Local\\VibeOCR-pdf-thumb",
                        Size = 4,
                        MediaType = "image/png",
                        Sha256 = new string('a', 64),
                        Owner = SharedPayloadOwner.Worker,
                        ExpiresUnixMs = 1,
                    },
                },
                RpcMethods.RotatePdf => RegisterRotate(Cast<RotatePdfRequest>(request)),
                RpcMethods.DeletePdfPages => new DeletePdfPagesResponse { PageCount = openPageCount },
                RpcMethods.StartPdfOcr => RegisterStartOcr(Cast<StartPdfOcrRequest>(request), ocrWriteErrors),
                RpcMethods.DeletePdfTextLayers => RegisterDeleteTextLayers(Cast<DeletePdfTextLayersRequest>(request)),
                RpcMethods.SavePdf => RegisterSave(Cast<SavePdfRequest>(request)),
                _ => throw new InvalidOperationException($"unexpected method {method}"),
            };
            return Task.FromResult((TResponse)response);
        }

        private static T Cast<T>(object request) where T : class => (T)request;

        private RotatePdfResponse RegisterRotate(RotatePdfRequest request)
        {
            LastRotate = request;
            return new RotatePdfResponse { PageCount = openPageCount };
        }

        private StartPdfOcrResponse RegisterStartOcr(StartPdfOcrRequest request, string[]? errors)
        {
            LastStartOcr = request;
            int completed = request.PageIndices.Length;
            return new StartPdfOcrResponse
            {
                Completed = completed,
                Failed = 0,
                Cancelled = false,
                Compressed = true,
                WriteErrors = errors,
            };
        }

        private DeletePdfTextLayersResponse RegisterDeleteTextLayers(DeletePdfTextLayersRequest request)
        {
            LastDeleteTextLayers = request;
            return new DeletePdfTextLayersResponse { DeletedCount = request.PageIndices.Length, ResidualPages = [] };
        }

        private SavePdfResponse RegisterSave(SavePdfRequest request)
        {
            LastSave = request;
            return new SavePdfResponse { SavedPath = request.OutputPath ?? "C:/data/sample.pdf" };
        }
    }
}
