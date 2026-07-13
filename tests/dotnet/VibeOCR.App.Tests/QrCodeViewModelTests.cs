using System.Security.Cryptography;
using VibeOCR.App.Features.QrCode;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class QrCodeViewModelTests
{
    [Theory]
    [InlineData(QrCodeInputKind.File)]
    [InlineData(QrCodeInputKind.Clipboard)]
    public async Task DecodeUsesSharedPayloadAndPublishesCodes(QrCodeInputKind kind)
    {
        var worker = new FakeWorkerClient(
            decodeResponder: (_, _) => Task.FromResult(DecodeResponse(
                new QrCodeResult { Data = "https://example.com", Format = "QR_CODE", IsUrl = true })));
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());

        await viewModel.DecodeAsync(kind, TestContext.Current.CancellationToken);

        Assert.Single(viewModel.Codes);
        Assert.Equal("https://example.com", viewModel.Codes[0].Data);
        Assert.True(viewModel.Codes[0].IsUrl);
        Assert.Equal("识别到 1 条结果", viewModel.DecodeStatus);
        Assert.False(viewModel.IsBusy);
        // Decode image payload must be released after the call.
        Assert.Single(worker.ReleasedPayloads);
    }

    [Fact]
    public async Task DecodeMultipleCodesPreservesOrderAndIsUrlFlag()
    {
        var worker = new FakeWorkerClient(
            decodeResponder: (_, _) => Task.FromResult(DecodeResponse(
                new QrCodeResult { Data = "https://vibeocr.example", Format = "QR_CODE", IsUrl = true },
                new QrCodeResult { Data = "plain text", Format = "QR_CODE", IsUrl = false },
                new QrCodeResult { Data = "12345", Format = "CODE_128" })));
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());

        await viewModel.DecodeAsync(QrCodeInputKind.File, TestContext.Current.CancellationToken);

        Assert.Equal(3, viewModel.Codes.Count);
        Assert.Equal("https://vibeocr.example", viewModel.Codes[0].Data);
        Assert.True(viewModel.Codes[0].IsUrl);
        Assert.False(viewModel.Codes[1].IsUrl!.Value);
        Assert.Null(viewModel.Codes[2].IsUrl);
        Assert.Equal("识别到 3 条结果", viewModel.DecodeStatus);
        Assert.True(viewModel.HasCodes);
    }

    [Fact]
    public async Task DecodeNoResultSurfacesFriendlyStatus()
    {
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(DecodeResponse()));
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());

        await viewModel.DecodeAsync(QrCodeInputKind.File, TestContext.Current.CancellationToken);

        Assert.Empty(viewModel.Codes);
        Assert.False(viewModel.HasCodes);
        Assert.Equal("未识别到二维码/条形码", viewModel.DecodeStatus);
        Assert.Single(worker.ReleasedPayloads);
    }

    [Fact]
    public async Task OpenableUrlsReturnsOnlyStrictHttpUrls()
    {
        // The Python service flags is_url; the view model trusts that flag and never
        // re-evaluates it, so a non-URL marked is_url=false or null is never openable.
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(DecodeResponse(
            new QrCodeResult { Data = "https://safe.example", Format = "QR_CODE", IsUrl = true },
            new QrCodeResult { Data = "javascript:alert(1)", Format = "QR_CODE", IsUrl = false },
            new QrCodeResult { Data = "file:///secret", Format = "QR_CODE" })));
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());

        await viewModel.DecodeAsync(QrCodeInputKind.File, TestContext.Current.CancellationToken);

        IReadOnlyList<QrCodeResult> openable = viewModel.OpenableUrls();
        Assert.Single(openable);
        Assert.Equal("https://safe.example", openable[0].Data);
    }

    [Fact]
    public async Task CopyAllJoinsDataWithNewlines()
    {
        var worker = new FakeWorkerClient((_, _) => Task.FromResult(DecodeResponse(
            new QrCodeResult { Data = "alpha", Format = "QR_CODE" },
            new QrCodeResult { Data = "beta", Format = "QR_CODE" })));
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());
        await viewModel.DecodeAsync(QrCodeInputKind.File, TestContext.Current.CancellationToken);

        Assert.Equal("alpha\nbeta", viewModel.CopyAll());
    }

    [Fact]
    public async Task CancelStopsDecodeAndResetsBusyState()
    {
        var worker = new FakeWorkerClient(async (_, ct) =>
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            return DecodeResponse();
        });
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());
        Task run = viewModel.DecodeAsync(QrCodeInputKind.File, TestContext.Current.CancellationToken);
        await worker.WaitForDecodeAsync(1);

        viewModel.Cancel();
        await run;

        Assert.False(viewModel.IsBusy);
        Assert.Equal("已取消", viewModel.DecodeStatus);
        Assert.Single(worker.ReleasedPayloads);
    }

    [Theory]
    [InlineData("qrcode")]
    [InlineData("barcode")]
    public async Task GenerateSendsDataAndFormatToWorker(string format)
    {
        SharedPayloadRef generated = SharedPayload("generated");
        var worker = new FakeWorkerClient(generateResponder: req =>
        {
            Assert.Equal(format, req.Format);
            return Task.FromResult(new GenerateQrCodeResponse { Image = generated });
        });
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput())
        {
            GenerateText = "encode me",
            GenerateFormat = format,
        };

        await viewModel.GenerateAsync(TestContext.Current.CancellationToken);

        Assert.Equal("encode me", worker.LastGenerateRequest?.Data);
        Assert.Equal(format, worker.LastGenerateRequest?.Format);
        Assert.Equal(generated.Name, viewModel.GeneratedImage?.Name);
        Assert.Equal("已生成", viewModel.GenerateStatus);
    }

    [Fact]
    public async Task GenerateEmptyTextKeepsStatusAndCallsNoWorker()
    {
        var worker = new FakeWorkerClient();
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput()) { GenerateText = "   " };

        await viewModel.GenerateAsync(TestContext.Current.CancellationToken);

        Assert.Null(worker.LastGenerateRequest);
        Assert.Equal("请输入要编码的内容", viewModel.GenerateStatus);
    }

    [Fact]
    public async Task GenerateReleasesPreviousImageWhenRegenerating()
    {
        SharedPayloadRef first = SharedPayload("first");
        SharedPayloadRef second = SharedPayload("second");
        int call = 0;
        var worker = new FakeWorkerClient(generateResponder: _ =>
        {
            return Task.FromResult(new GenerateQrCodeResponse { Image = Interlocked.Increment(ref call) == 1 ? first : second });
        });
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput())
        {
            GenerateText = "first",
            GenerateFormat = "qrcode",
        };
        await viewModel.GenerateAsync(TestContext.Current.CancellationToken);
        Assert.Equal(first.Name, viewModel.GeneratedImage?.Name);

        viewModel.GenerateText = "second";
        await viewModel.GenerateAsync(TestContext.Current.CancellationToken);

        Assert.Equal(second.Name, viewModel.GeneratedImage?.Name);
        Assert.Contains(first.Name, worker.ReleasedPayloads);
    }

    [Fact]
    public async Task SaveReadsGeneratedImageFromSharedMemoryAndWritesFile()
    {
        SharedPayloadRef generated = SharedPayload("generated");
        byte[] imageBytes = [1, 2, 3, 4];
        var worker = new FakeWorkerClient(
            generateResponder: _ => Task.FromResult(new GenerateQrCodeResponse { Image = generated }),
            readPayloadResponder: _ => imageBytes);
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput())
        {
            GenerateText = "x",
            GenerateFormat = "qrcode",
        };
        await viewModel.GenerateAsync(TestContext.Current.CancellationToken);

        var platform = new FakeSavePlatform { Path = Path.GetTempFileName() };
        var commands = new QrCodeSaveCommands(worker, platform);
        try
        {
            bool saved = await commands.SaveAsync(generated, "qr.png", TestContext.Current.CancellationToken);

            Assert.True(saved);
            Assert.Equal(imageBytes, platform.LastWrittenBytes);
        }
        finally
        {
            if (File.Exists(platform.Path)) File.Delete(platform.Path);
        }
    }

    [Fact]
    public async Task SaveReturnsFalseWhenUserCancelsPicker()
    {
        var worker = new FakeWorkerClient();
        var platform = new FakeSavePlatform { Path = null };
        var commands = new QrCodeSaveCommands(worker, platform);

        bool saved = await commands.SaveAsync(SharedPayload("img"), "qr.png", TestContext.Current.CancellationToken);

        Assert.False(saved);
        Assert.Null(platform.LastWrittenBytes);
    }

    [Fact]
    public async Task SaveReturnsFalseWhenUserDeclinesOverwrite()
    {
        var worker = new FakeWorkerClient(readPayloadResponder: _ => [9]);
        string existing = Path.GetTempFileName();
        try
        {
            var platform = new FakeSavePlatform { Path = existing, Confirm = false };
            var commands = new QrCodeSaveCommands(worker, platform);

            bool saved = await commands.SaveAsync(SharedPayload("img"), "qr.png", TestContext.Current.CancellationToken);

            Assert.False(saved);
            Assert.Null(platform.LastWrittenBytes);
        }
        finally
        {
            File.Delete(existing);
        }
    }

    [Fact]
    public async Task WorkerErrorDuringDecodeIsLocalized()
    {
        var worker = new FakeWorkerClient(decodeResponder: (_, _) => throw new WorkerRpcException(
            new RpcErrorBody { Code = ErrorCode.DependencyMissing, Message = "pyzbar missing", Retryable = false }));
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());

        await viewModel.DecodeAsync(QrCodeInputKind.File, TestContext.Current.CancellationToken);

        Assert.Equal("识别依赖尚未安装", viewModel.DecodeStatus);
        Assert.False(viewModel.IsBusy);
    }

    [Fact]
    public async Task WorkerCrashDuringDecodeShowsDisconnectStatus()
    {
        var worker = new FakeWorkerClient(decodeResponder: (_, _) => throw new IOException("pipe closed"));
        var viewModel = new QrCodeViewModel(worker, new FakeQrInput());

        await viewModel.DecodeAsync(QrCodeInputKind.File, TestContext.Current.CancellationToken);

        Assert.Equal("Worker 已断开，请重试", viewModel.DecodeStatus);
    }

    private static DecodeQrCodeResponse DecodeResponse(params QrCodeResult[] codes) => new() { Codes = codes };

    private static SharedPayloadRef SharedPayload(string label) => new()
    {
        Name = $@"Local\VibeOCR-qr-{label}-{Guid.NewGuid():D}-{Guid.NewGuid():D}",
        Size = 4,
        MediaType = "image/png",
        Sha256 = new string('a', 64),
        Owner = SharedPayloadOwner.Worker,
        ExpiresUnixMs = DateTimeOffset.UtcNow.AddMinutes(5).ToUnixTimeMilliseconds(),
    };

    private sealed class FakeQrInput : IQrCodeInput
    {
        public Task<QrCodeInput?> PickFileAsync(CancellationToken cancellationToken) => Image();
        public Task<QrCodeInput?> ReadClipboardAsync(CancellationToken cancellationToken) => Image();
        public Task<QrCodeInput?> ReadDroppedFileAsync(string path, CancellationToken cancellationToken) => Image();

        private static Task<QrCodeInput?> Image() =>
            Task.FromResult<QrCodeInput?>(new QrCodeInput([1, 2, 3, 4], "image/png", "input.png"));
    }

    private sealed class FakeSavePlatform : IQrCodeSavePlatform
    {
        public string? Path { get; init; }
        public bool Confirm { get; init; } = true;
        public byte[]? LastWrittenBytes { get; private set; }

        public Task<string?> PickSavePathAsync(string suggestedName, CancellationToken cancellationToken)
            => Task.FromResult(Path);

        public Task<bool> ConfirmOverwriteAsync(string path, CancellationToken cancellationToken)
            => Task.FromResult(Confirm);

        public Task WriteFileAsync(string path, byte[] data, CancellationToken cancellationToken)
        {
            LastWrittenBytes = data;
            return Task.CompletedTask;
        }
    }

    private sealed class FakeWorkerClient(
        Func<DecodeQrCodeRequest?, CancellationToken, Task<DecodeQrCodeResponse>>? decodeResponder = null,
        Func<GenerateQrCodeRequest, Task<GenerateQrCodeResponse>>? generateResponder = null,
        Func<SharedPayloadRef, byte[]>? readPayloadResponder = null) : IWorkerHostClient
    {
        private readonly TaskCompletionSource _decodeArrived = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private int _decodeCalls;

        public List<string> ReleasedPayloads { get; } = [];
        public GenerateQrCodeRequest? LastGenerateRequest { get; private set; }
        public SharedPayloadRef? LastDecodeImage { get; private set; }

        public SharedPayloadRef CreatePayload(ReadOnlySpan<byte> data, string mediaType, TimeSpan ttl) => new()
        {
            Name = $@"Local\VibeOCR-qr-{Guid.NewGuid():D}-{Guid.NewGuid():D}",
            Size = data.Length,
            MediaType = mediaType,
            Sha256 = Convert.ToHexStringLower(SHA256.HashData(data)),
            Owner = SharedPayloadOwner.Client,
            ExpiresUnixMs = DateTimeOffset.UtcNow.Add(ttl).ToUnixTimeMilliseconds(),
        };

        public bool ReleasePayload(string name)
        {
            ReleasedPayloads.Add(name);
            return true;
        }

        public byte[] ReadPayload(SharedPayloadRef reference, TimeSpan timeout, CancellationToken cancellationToken)
            => readPayloadResponder is not null ? readPayloadResponder(reference) : [];

        public async Task<TResponse> CallAsync<TRequest, TResponse>(
            string method,
            TRequest request,
            CancellationToken cancellationToken)
            where TRequest : IProtocolValidatable
            where TResponse : IProtocolValidatable
        {
            if (method == RpcMethods.GenerateQrCode)
            {
                LastGenerateRequest = Assert.IsType<GenerateQrCodeRequest>(request);
                GenerateQrCodeResponse response = generateResponder is not null
                    ? await generateResponder(LastGenerateRequest)
                    : new GenerateQrCodeResponse { Image = SharedPayload("default") };
                return (TResponse)(object)response;
            }

            Assert.Equal(RpcMethods.DecodeQrCode, method);
            var decodeRequest = Assert.IsType<DecodeQrCodeRequest>(request);
            LastDecodeImage = decodeRequest.Image;
            Interlocked.Increment(ref _decodeCalls);
            _decodeArrived.TrySetResult();
            DecodeQrCodeResponse decode = decodeResponder is not null
                ? await decodeResponder(decodeRequest, cancellationToken)
                : throw new InvalidOperationException("no decode responder configured");
            return (TResponse)(object)decode;
        }

        public async Task WaitForDecodeAsync(int expected)
        {
            while (Volatile.Read(ref _decodeCalls) < expected)
            {
                await _decodeArrived.Task.WaitAsync(TimeSpan.FromSeconds(5));
                await Task.Yield();
            }
        }
    }
}
