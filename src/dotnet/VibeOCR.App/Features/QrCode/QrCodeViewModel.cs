using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Features.QrCode;

public enum QrCodeInputKind { File, Clipboard, DroppedFile }
public sealed record QrCodeInput(byte[] Data, string MediaType, string DisplayName);
public interface IQrCodeInput
{
    Task<QrCodeInput?> PickFileAsync(CancellationToken ct);
    Task<QrCodeInput?> ReadClipboardAsync(CancellationToken ct);
    Task<QrCodeInput?> ReadDroppedFileAsync(string path, CancellationToken ct);
}

public sealed class QrCodeViewModel(IQrCodeClient qrClient, IQrCodeInput input) : INotifyPropertyChanged
{
    private CancellationTokenSource? _activeRun;
    private long _generation;
    private bool _isBusy;
    private string _decodeStatus = "请粘贴或选择图片";
    private string? _generatedImageBase64;
    private string _generateStatus = string.Empty;
    private string _generateText = string.Empty;
    private string _generateFormat = "qrcode";

    public event PropertyChangedEventHandler? PropertyChanged;
    public ObservableCollection<QrCodeResult> Codes { get; } = [];
    public bool IsBusy { get => _isBusy; private set => SetField(ref _isBusy, value); }
    public string DecodeStatus { get => _decodeStatus; private set => SetField(ref _decodeStatus, value); }
    public string? GeneratedImageBase64 { get => _generatedImageBase64; private set => SetField(ref _generatedImageBase64, value); }
    public string GenerateStatus { get => _generateStatus; private set => SetField(ref _generateStatus, value); }
    public string GenerateText { get => _generateText; set => SetField(ref _generateText, value); }
    public string GenerateFormat { get => _generateFormat; set => SetField(ref _generateFormat, value); }
    public bool HasCodes => Codes.Count > 0;

    public Task DecodeAsync(QrCodeInputKind kind, CancellationToken ct) => kind switch
    {
        QrCodeInputKind.File => DecodeAsync(input.PickFileAsync, ct),
        QrCodeInputKind.Clipboard => DecodeAsync(input.ReadClipboardAsync, ct),
        QrCodeInputKind.DroppedFile => throw new ArgumentOutOfRangeException(nameof(kind), "DroppedFile requires a path."),
        _ => throw new ArgumentOutOfRangeException(nameof(kind)),
    };

    public Task DecodeDroppedFileAsync(string path, CancellationToken ct) =>
        DecodeAsync(token => input.ReadDroppedFileAsync(path, token), ct);

    public void Cancel() => _activeRun?.Cancel();
    public IReadOnlyList<QrCodeResult> OpenableUrls() => Codes.Where(c => c.IsUrl is true).ToArray();
    public string CopyAll() => string.Join("\n", Codes.Select(c => c.Data));

    public async Task GenerateAsync(CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(GenerateText)) { GenerateStatus = "请输入要编码的内容"; return; }
        long generation = Interlocked.Increment(ref _generation);
        CancellationTokenSource? previous = Interlocked.Exchange(ref _activeRun, CancellationTokenSource.CreateLinkedTokenSource(cancellationToken));
        previous?.Cancel(); previous?.Dispose();
        CancellationTokenSource run = _activeRun;
        try
        {
            GenerateStatus = "正在生成";
            QrCodeGeneratedImage generated = await qrClient.GenerateAsync(GenerateText, GenerateFormat, run.Token);
            if (generation == Volatile.Read(ref _generation)) { GeneratedImageBase64 = generated.Base64Png; GenerateStatus = "已生成"; }
        }
        catch (OperationCanceledException) { if (generation == Volatile.Read(ref _generation)) GenerateStatus = "已取消"; }
        catch (InferenceClientException error) { if (generation == Volatile.Read(ref _generation)) GenerateStatus = error.Code is HttpV2ErrorCode.BackendUnavailable or HttpV2ErrorCode.TransientBackend ? "Supervisor 暂不可用" : "生成失败"; }
        catch (Exception) when (generation == Volatile.Read(ref _generation)) { GenerateStatus = "Supervisor 已断开，请重试"; }
        finally { if (generation == Volatile.Read(ref _generation) && ReferenceEquals(Interlocked.CompareExchange(ref _activeRun, null, run), run)) run.Dispose(); }
    }

    public void ReleaseGeneratedImage() { GeneratedImageBase64 = null; }

    private async Task DecodeAsync(Func<CancellationToken, Task<QrCodeInput?>> loadInput, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(loadInput);
        long generation = Interlocked.Increment(ref _generation);
        CancellationTokenSource? previous = Interlocked.Exchange(ref _activeRun, CancellationTokenSource.CreateLinkedTokenSource(cancellationToken));
        previous?.Cancel(); previous?.Dispose();
        CancellationTokenSource run = _activeRun;
        if (generation == Volatile.Read(ref _generation)) { IsBusy = true; DecodeStatus = "正在读取输入"; }
        try
        {
            QrCodeInput? imageInput = await loadInput(run.Token);
            if (imageInput is null) { if (generation == Volatile.Read(ref _generation)) DecodeStatus = "已取消选择"; return; }
            if (generation == Volatile.Read(ref _generation)) DecodeStatus = "正在识别";
            string base64Image = Convert.ToBase64String(imageInput.Data);
            IReadOnlyList<QrCodeDecodedItem> decoded = await qrClient.DecodeAsync(base64Image, run.Token);
            if (generation != Volatile.Read(ref _generation)) return;
            Codes.Clear();
            foreach (QrCodeDecodedItem item in decoded) Codes.Add(new QrCodeResult { Data = item.Data, Format = item.Format, IsUrl = item.IsUrl });
            DecodeStatus = Codes.Count == 0 ? "未识别到二维码/条形码" : $"识别到 {Codes.Count} 条结果";
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasCodes)));
        }
        catch (OperationCanceledException) { if (generation == Volatile.Read(ref _generation)) DecodeStatus = "已取消"; }
        catch (InferenceClientException error) { if (generation == Volatile.Read(ref _generation)) DecodeStatus = error.Code is HttpV2ErrorCode.BackendUnavailable or HttpV2ErrorCode.TransientBackend ? "Supervisor 暂不可用" : "识别失败"; }
        catch (Exception error) when (error is InvalidDataException or UnauthorizedAccessException or FileNotFoundException) { if (generation == Volatile.Read(ref _generation)) DecodeStatus = "无法读取输入图片"; }
        catch (Exception) when (generation == Volatile.Read(ref _generation)) { DecodeStatus = "Supervisor 已断开，请重试"; }
        finally { if (generation == Volatile.Read(ref _generation)) { IsBusy = false; if (ReferenceEquals(Interlocked.CompareExchange(ref _activeRun, null, run), run)) run.Dispose(); } }
    }

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null) { if (EqualityComparer<T>.Default.Equals(field, value)) return; field = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name)); }
}
