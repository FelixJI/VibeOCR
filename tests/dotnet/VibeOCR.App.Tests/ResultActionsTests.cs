using System.Security.Cryptography;
using VibeOCR.App.Features.Recognition;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class ResultActionsTests
{
    [Fact]
    public async Task ClipboardBusyRetriesAndPreservesRequestedFormat()
    {
        var platform = new FakePlatform { BusyAttempts = 2 };
        var actions = Create(platform, new FakeWorker(), static (_, _) => Task.CompletedTask);
        await actions.CopyAsync(ResultCopyFormat.Markdown, TestContext.Current.CancellationToken);
        Assert.Equal(3, platform.CopyCalls);
        Assert.Equal(ResultCopyFormat.Markdown, platform.LastCopyFormat);
        Assert.Equal("# 结果 ✓", platform.LastContent?.MarkdownText);
    }

    [Fact]
    public async Task ExportMapsUnicodePathAndFormatToPythonFacade()
    {
        var worker = new FakeWorker();
        var platform = new FakePlatform { Path = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "识别 结果.md")) };
        var actions = Create(platform, worker);
        ExportOcrResponse? response = await actions.ExportAsync(ResultExportFormat.Markdown, TestContext.Current.CancellationToken);
        Assert.Equal(platform.Path, response?.OutputPath);
        Assert.Equal("markdown", worker.LastExport?.Format);
        Assert.Equal("正文 ✓", worker.LastExport?.RawText);
    }

    [Fact]
    public async Task ExistingFileRequiresExplicitOverwriteConfirmation()
    {
        string path = Path.Combine(Path.GetTempPath(), $"vibeocr-{Guid.NewGuid():N}.txt");
        await File.WriteAllTextAsync(path, "old", TestContext.Current.CancellationToken);
        try
        {
            var worker = new FakeWorker();
            var platform = new FakePlatform { Path = path, Confirm = false };
            var actions = Create(platform, worker);
            Assert.Null(await actions.ExportAsync(ResultExportFormat.Text, TestContext.Current.CancellationToken));
            Assert.Null(worker.LastExport);
        }
        finally { File.Delete(path); }
    }

    private static ResultActions Create(FakePlatform platform, FakeWorker worker, Func<TimeSpan, CancellationToken, Task>? delay = null)
    {
        var actions = new ResultActions(worker, platform, delay);
        actions.SetResult(new RecognizeResponse { Text = "正文 ✓", MarkdownText = "# 结果 ✓", HtmlText = "<h1>结果 ✓</h1>", Pipeline = "OCR", RawBlocks = [] });
        return actions;
    }

    private sealed class FakePlatform : IResultActionPlatform
    {
        public int BusyAttempts { get; init; }
        public int CopyCalls { get; private set; }
        public string? Path { get; init; }
        public bool Confirm { get; init; } = true;
        public ResultCopyFormat? LastCopyFormat { get; private set; }
        public RecognitionResultContent? LastContent { get; private set; }
        public Task WriteClipboardAsync(RecognitionResultContent result, ResultCopyFormat format, CancellationToken cancellationToken)
        {
            CopyCalls++; LastCopyFormat = format; LastContent = result;
            if (CopyCalls <= BusyAttempts) throw new ClipboardBusyException();
            return Task.CompletedTask;
        }
        public Task<string?> PickExportPathAsync(ResultExportFormat format, CancellationToken cancellationToken) => Task.FromResult(Path);
        public Task<bool> ConfirmOverwriteAsync(string path, CancellationToken cancellationToken) => Task.FromResult(Confirm);
    }

    private sealed class FakeWorker : IWorkerHostClient
    {
        public ExportOcrRequest? LastExport { get; private set; }
        public SharedPayloadRef CreatePayload(ReadOnlySpan<byte> data, string mediaType, TimeSpan ttl) => new() { Name = $@"Local\VibeOCR-{Guid.NewGuid():D}-{Guid.NewGuid():D}", Size = data.Length, MediaType = mediaType, Sha256 = Convert.ToHexStringLower(SHA256.HashData(data)), Owner = SharedPayloadOwner.Client, ExpiresUnixMs = 1 };
        public bool ReleasePayload(string name) => true;
        public Task<TResponse> CallAsync<TRequest, TResponse>(string method, TRequest request, CancellationToken cancellationToken) where TRequest : IProtocolValidatable where TResponse : IProtocolValidatable
        {
            Assert.Equal(RpcMethods.ExportOcr, method); LastExport = Assert.IsType<ExportOcrRequest>(request);
            return Task.FromResult((TResponse)(object)new ExportOcrResponse { OutputPath = LastExport.OutputPath, BytesWritten = 10 });
        }
    }
}
