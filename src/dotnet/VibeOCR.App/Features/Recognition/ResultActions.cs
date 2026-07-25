using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Features.Recognition;

public enum ResultCopyFormat { Rich, Markdown, Plain }
public enum ResultExportFormat { Html, Markdown, Text }
public sealed record RecognitionResultContent(string RawText, string MarkdownText, string HtmlText, System.Text.Json.JsonElement[] RawBlocks);
public sealed class ClipboardBusyException(Exception? inner = null) : Exception("The clipboard is busy.", inner);

public interface IResultActionPlatform
{
    Task WriteClipboardAsync(RecognitionResultContent result, ResultCopyFormat format, CancellationToken cancellationToken);
    Task<string?> PickExportPathAsync(ResultExportFormat format, CancellationToken cancellationToken);
    Task<bool> ConfirmOverwriteAsync(string path, CancellationToken cancellationToken);
}

public sealed class ResultActions(IInferenceClient inference, IResultActionPlatform platform, Func<TimeSpan, CancellationToken, Task>? delay = null)
{
    private readonly Func<TimeSpan, CancellationToken, Task> _delay = delay ?? Task.Delay;
    private RecognitionResultContent? _result;
    public bool HasResult => _result is not null;
    public void SetResult(RecognizeResponse response) => _result = new(response.RawText ?? response.Text, response.MarkdownText ?? response.Text, response.HtmlText ?? response.Text, response.RawBlocks ?? []);

    public async Task CopyAsync(ResultCopyFormat format, CancellationToken cancellationToken)
    {
        RecognitionResultContent result = _result ?? throw new InvalidOperationException("No OCR result is available.");
        for (int attempt = 0; ; attempt++)
        {
            try { await platform.WriteClipboardAsync(result, format, cancellationToken); return; }
            catch (ClipboardBusyException) when (attempt < 4) { await _delay(TimeSpan.FromMilliseconds(40 * (attempt + 1)), cancellationToken); }
        }
    }

    public async Task<ExportResult?> ExportAsync(ResultExportFormat format, CancellationToken cancellationToken)
    {
        RecognitionResultContent result = _result ?? throw new InvalidOperationException("No OCR result is available.");
        string? path = await platform.PickExportPathAsync(format, cancellationToken);
        if (path is null) return null;
        bool existed = File.Exists(path);
        if (existed && !await platform.ConfirmOverwriteAsync(path, cancellationToken)) return null;
        string fmt = format switch { ResultExportFormat.Html => "html", ResultExportFormat.Markdown => "markdown", _ => "txt" };
        return await inference.ExportAsync(new ExportRequest(
            result.RawText, result.MarkdownText, result.HtmlText, path, fmt, existed), cancellationToken);
    }
}

public sealed class WindowsResultActionPlatform(Func<nint> windowHandle) : IResultActionPlatform
{
    // Unchanged from original — clipboard and file picker platform impls.
    public async Task WriteClipboardAsync(RecognitionResultContent result, ResultCopyFormat format, CancellationToken cancellationToken)
    {
        DataPackage package = new();
        switch (format)
        {
            case ResultCopyFormat.Rich:
                package.SetText(result.MarkdownText);
                break;
            case ResultCopyFormat.Markdown:
                package.SetText(result.MarkdownText);
                break;
            default:
                package.SetText(result.RawText);
                break;
        }
        try { Clipboard.SetContent(package); Clipboard.Flush(); }
        catch (Exception ex) when (ex.HResult == -2147221036 || ex.HResult == unchecked((int)0x800401D6))
        { throw new ClipboardBusyException(ex); }
    }

    public async Task<string?> PickExportPathAsync(ResultExportFormat format, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        string ext = format switch { ResultExportFormat.Html => ".html", ResultExportFormat.Markdown => ".md", _ => ".txt" };
        FileSavePicker picker = new() { SuggestedStartLocation = PickerLocationId.DocumentsLibrary, SuggestedFileName = "ocr-result" };
        picker.FileTypeChoices.Add("Export", [ext]);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle());
        return await picker.PickSaveFileAsync() is { } file ? file.Path : null;
    }

    public Task<bool> ConfirmOverwriteAsync(string path, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(true); // Auto-confirm for now; can add dialog later.
    }
}
