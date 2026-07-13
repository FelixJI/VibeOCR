using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;

namespace VibeOCR.App.Features.QrCode;

/// <summary>
/// View model for the QR code tab: decodes images via the Python WorkerHost and
/// generates QR/barcode images. URL safety is owned by the Python decode service
/// (strict http/https check); this view model only surfaces <see cref="QrCodeResult.IsUrl"/>
/// to decide whether to offer the safe-open affordance.
/// </summary>
public sealed class QrCodeViewModel(IWorkerHostClient worker, IQrCodeInput input) : INotifyPropertyChanged
{
    private readonly IWorkerHostClient _worker = worker ?? throw new ArgumentNullException(nameof(worker));
    private readonly IQrCodeInput _input = input ?? throw new ArgumentNullException(nameof(input));
    private CancellationTokenSource? _activeRun;
    private long _generation;
    private bool _isBusy;
    private string _decodeStatus = "请粘贴或选择图片";
    private SharedPayloadRef? _generatedImage;
    private string? _generatedImageName;
    private string _generateStatus = string.Empty;
    private string _generateText = string.Empty;
    private string _generateFormat = "qrcode";

    public event PropertyChangedEventHandler? PropertyChanged;

    public ObservableCollection<QrCodeResult> Codes { get; } = [];

    public bool IsBusy { get => _isBusy; private set => SetField(ref _isBusy, value); }
    public string DecodeStatus { get => _decodeStatus; private set => SetField(ref _decodeStatus, value); }
    public SharedPayloadRef? GeneratedImage { get => _generatedImage; private set => SetField(ref _generatedImage, value); }
    public string? GeneratedImageName { get => _generatedImageName; private set => SetField(ref _generatedImageName, value); }
    public string GenerateStatus { get => _generateStatus; private set => SetField(ref _generateStatus, value); }
    public string GenerateText { get => _generateText; set => SetField(ref _generateText, value); }
    public string GenerateFormat { get => _generateFormat; set => SetField(ref _generateFormat, value); }

    /// <summary>True when there is at least one decoded code to copy all from.</summary>
    public bool HasCodes => Codes.Count > 0;

    /// <summary>Task factory used by tests; resolved per-call.</summary>
    public Task DecodeAsync(QrCodeInputKind kind, CancellationToken cancellationToken) =>
        kind switch
        {
            QrCodeInputKind.File => DecodeAsync(_input.PickFileAsync, cancellationToken),
            QrCodeInputKind.Clipboard => DecodeAsync(_input.ReadClipboardAsync, cancellationToken),
            QrCodeInputKind.DroppedFile => throw new ArgumentOutOfRangeException(
                nameof(kind), "DroppedFile requires a path; use DecodeDroppedFileAsync."),
            _ => throw new ArgumentOutOfRangeException(nameof(kind)),
        };

    public Task DecodeDroppedFileAsync(string path, CancellationToken cancellationToken) =>
        DecodeAsync(ct => _input.ReadDroppedFileAsync(path, ct), cancellationToken);

    public void Cancel() => _activeRun?.Cancel();

    /// <summary>
    /// Returns the codes that may be safely opened in a browser. Only strict http/https
    /// URLs flagged by the Python service qualify; everything else is treated as data.
    /// </summary>
    public IReadOnlyList<QrCodeResult> OpenableUrls() =>
        Codes.Where(c => c.IsUrl is true).ToArray();

    public string CopyAll() => string.Join("\n", Codes.Select(c => c.Data));

    public async Task GenerateAsync(CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(GenerateText))
        {
            GenerateStatus = "请输入要编码的内容";
            return;
        }

        long generation = Interlocked.Increment(ref _generation);
        CancellationTokenSource? previous = Interlocked.Exchange(
            ref _activeRun,
            CancellationTokenSource.CreateLinkedTokenSource(cancellationToken));
        previous?.Cancel();
        previous?.Dispose();
        CancellationTokenSource run = _activeRun;
        string? previousImage = GeneratedImageName;
        try
        {
            GenerateStatus = "正在生成";
            var request = new GenerateQrCodeRequest
            {
                Data = GenerateText,
                Format = GenerateFormat,
            };
            GenerateQrCodeResponse response = await _worker.CallAsync<
                GenerateQrCodeRequest,
                GenerateQrCodeResponse>(RpcMethods.GenerateQrCode, request, run.Token);
            if (generation == Volatile.Read(ref _generation))
            {
                GeneratedImage = response.Image;
                GeneratedImageName = response.Image.Name;
                GenerateStatus = "已生成";
            }
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) GenerateStatus = "已取消";
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
                GenerateStatus = Localize(error.Error.Code);
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            GenerateStatus = "Worker 已断开，请重试";
        }
        finally
        {
            if (previousImage is not null && previousImage != GeneratedImageName)
            {
                _worker.ReleasePayload(previousImage);
            }

            if (generation == Volatile.Read(ref _generation) &&
                ReferenceEquals(Interlocked.CompareExchange(ref _activeRun, null, run), run))
            {
                run.Dispose();
            }
        }
    }

    /// <summary>
    /// Releases the currently held generated image payload. Called when the user
    /// navigates away or generates a new image, mirroring the recognition flow.
    /// </summary>
    public void ReleaseGeneratedImage()
    {
        string? name = Interlocked.Exchange(ref _generatedImageName, null);
        if (name is not null) _worker.ReleasePayload(name);
        GeneratedImage = null;
    }

    private async Task DecodeAsync(
        Func<CancellationToken, Task<QrCodeInput?>> loadInput,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(loadInput);
        long generation = Interlocked.Increment(ref _generation);
        CancellationTokenSource? previous = Interlocked.Exchange(
            ref _activeRun,
            CancellationTokenSource.CreateLinkedTokenSource(cancellationToken));
        previous?.Cancel();
        previous?.Dispose();
        CancellationTokenSource run = _activeRun;
        string? payloadName = null;
        if (generation == Volatile.Read(ref _generation))
        {
            IsBusy = true;
            DecodeStatus = "正在读取输入";
        }

        try
        {
            QrCodeInput? imageInput = await loadInput(run.Token);
            if (imageInput is null)
            {
                if (generation == Volatile.Read(ref _generation)) DecodeStatus = "已取消选择";
                return;
            }

            SharedPayloadRef payload = _worker.CreatePayload(
                imageInput.Data, imageInput.MediaType, TimeSpan.FromMinutes(5));
            payloadName = payload.Name;
            if (generation == Volatile.Read(ref _generation)) DecodeStatus = "正在识别";

            DecodeQrCodeResponse response = await _worker.CallAsync<
                DecodeQrCodeRequest,
                DecodeQrCodeResponse>(
                RpcMethods.DecodeQrCode,
                new DecodeQrCodeRequest { Image = payload },
                run.Token);
            if (generation != Volatile.Read(ref _generation)) return;

            Codes.Clear();
            foreach (QrCodeResult code in response.Codes) Codes.Add(code);
            DecodeStatus = Codes.Count == 0
                ? "未识别到二维码/条形码"
                : $"识别到 {Codes.Count} 条结果";
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasCodes)));
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) DecodeStatus = "已取消";
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
                DecodeStatus = Localize(error.Error.Code);
        }
        catch (Exception error) when (
            error is InvalidDataException or UnauthorizedAccessException or FileNotFoundException)
        {
            if (generation == Volatile.Read(ref _generation))
                DecodeStatus = "无法读取输入图片";
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            DecodeStatus = "Worker 已断开，请重试";
        }
        finally
        {
            if (payloadName is not null) _worker.ReleasePayload(payloadName);
            if (generation == Volatile.Read(ref _generation))
            {
                IsBusy = false;
                if (ReferenceEquals(Interlocked.CompareExchange(ref _activeRun, null, run), run))
                {
                    run.Dispose();
                }
            }
        }
    }

    private static string Localize(ErrorCode code) => code switch
    {
        ErrorCode.InvalidRequest => "输入图片无效",
        ErrorCode.DependencyMissing => "识别依赖尚未安装",
        ErrorCode.WorkerUnavailable => "Worker 暂不可用，请重试",
        ErrorCode.TaskCancelled => "已取消",
        ErrorCode.TaskTimeout => "识别超时，请重试",
        ErrorCode.ProtocolMismatch => "Worker 协议不兼容",
        ErrorCode.ResourceExhausted => "内存或显存不足",
        _ => "识别失败",
    };

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}

public enum QrCodeInputKind { File, Clipboard, DroppedFile }

public sealed record QrCodeInput(byte[] Data, string MediaType, string DisplayName);

public interface IQrCodeInput
{
    Task<QrCodeInput?> PickFileAsync(CancellationToken cancellationToken);
    Task<QrCodeInput?> ReadClipboardAsync(CancellationToken cancellationToken);
    Task<QrCodeInput?> ReadDroppedFileAsync(string path, CancellationToken cancellationToken);
}
