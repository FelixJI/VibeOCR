using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;

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

public sealed class ResultActions(IWorkerHostClient worker, IResultActionPlatform platform, Func<TimeSpan, CancellationToken, Task>? delay = null)
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

    public async Task<ExportOcrResponse?> ExportAsync(ResultExportFormat format, CancellationToken cancellationToken)
    {
        RecognitionResultContent result = _result ?? throw new InvalidOperationException("No OCR result is available.");
        string? path = await platform.PickExportPathAsync(format, cancellationToken);
        if (path is null) return null;
        bool existed = File.Exists(path);
        if (existed && !await platform.ConfirmOverwriteAsync(path, cancellationToken)) return null;
        return await worker.CallAsync<ExportOcrRequest, ExportOcrResponse>(RpcMethods.ExportOcr, new ExportOcrRequest
        {
            RawText = result.RawText,
            MarkdownText = result.MarkdownText,
            HtmlText = result.HtmlText,
            RawBlocks = result.RawBlocks,
            OutputPath = path,
            Format = format switch { ResultExportFormat.Html => "html", ResultExportFormat.Markdown => "markdown", _ => "txt" },
            Overwrite = existed,
        }, cancellationToken);
    }
}

public sealed class WindowsResultActionPlatform(Func<XamlRoot?> xamlRoot) : IResultActionPlatform
{
    public Task WriteClipboardAsync(RecognitionResultContent result, ResultCopyFormat format, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var package = new DataPackage { RequestedOperation = DataPackageOperation.Copy };
        if (format == ResultCopyFormat.Rich && !string.IsNullOrEmpty(result.HtmlText)) { package.SetHtmlFormat(HtmlFormatHelper.CreateHtmlFormat(result.HtmlText)); package.SetText(result.MarkdownText); }
        else package.SetText(format == ResultCopyFormat.Markdown ? result.MarkdownText : result.RawText);
        try { Clipboard.SetContent(package); Clipboard.Flush(); }
        catch (COMException error) when (error.HResult == unchecked((int)0x800401D0)) { throw new ClipboardBusyException(error); }
        return Task.CompletedTask;
    }

    public async Task<string?> PickExportPathAsync(ResultExportFormat format, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var picker = new FileSavePicker { SuggestedFileName = "VibeOCR-result" };
        string extension = format switch { ResultExportFormat.Html => ".html", ResultExportFormat.Markdown => ".md", _ => ".txt" };
        picker.FileTypeChoices.Add(format.ToString(), [extension]);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, GetActiveWindow());
        Windows.Storage.StorageFile? file = await picker.PickSaveFileAsync();
        return file?.Path;
    }

    public async Task<bool> ConfirmOverwriteAsync(string path, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var dialog = new ContentDialog { XamlRoot = xamlRoot() ?? throw new InvalidOperationException("The page is not loaded."), Title = "覆盖已有文件？", Content = Path.GetFileName(path), PrimaryButtonText = "覆盖", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    [DllImport("user32.dll")]
    private static extern nint GetActiveWindow();
}
