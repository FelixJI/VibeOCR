using Microsoft.UI.Xaml;
using VibeOCR.Platform.Inference;
using Microsoft.UI.Xaml.Controls;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.Services;
using VibeOCR.App.Web;

namespace VibeOCR.App.Views;

public sealed partial class RecognitionPage : Page
{
    private readonly WebMessageRouter _router;
    private readonly PreviewHost _previewHost;
    private bool _bridgeTerminal;
    private bool _bridgeReady;
    private readonly ResultActions _resultActions;

    public RecognitionPage(RecognitionViewModel viewModel)
    {
        ViewModel = viewModel ?? throw new ArgumentNullException(nameof(viewModel));
        _resultActions = ViewModel.CreateResultActions(new WindowsResultActionPlatform(GetActiveWindow));
        _router = new WebMessageRouter();
        _previewHost = new PreviewHost(_router);
        _router.MessageReceived += OnWebMessageReceived;
        _previewHost.ProtocolViolation += OnProtocolViolation;
        _previewHost.StateChanged += OnPreviewStateChanged;
        ViewModel.PropertyChanged += OnViewModelPropertyChanged;
        InitializeComponent();
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    public RecognitionViewModel ViewModel { get; }

    private void OnWebMessageReceived(WebBridgeMessage message)
    {
        if (message.Type == "preview.ready")
        {
            _bridgeTerminal = true;
            _bridgeReady = true;
            PreviewBridgeStatus.Text = "预览已就绪";
            AppLog.Info("Bridge: preview.ready received");
            _ = SendInputAsync();
            _ = SendResultAsync();
        }
        else if (message.Type == "editor.changed")
        {
            AppLog.Info($"Bridge: editor.changed payload={message.Payload}");
        }
    }

    private void OnProtocolViolation(WebBridgeProtocolException error)
    {
        _bridgeTerminal = true;
        PreviewBridgeStatus.Text = "Web bridge 协议拒绝";
    }

    private void OnPreviewStateChanged(string state)
    {
        if (_bridgeTerminal) return;
        PreviewBridgeStatus.Text = state switch
        {
            "dom-content-loaded" => "Web DOM 已加载",
            "navigation-complete" => "Web 页面已加载",
            _ => $"Web 预览失败：{state}",
        };
    }

    private async void OnLoaded(object sender, RoutedEventArgs args)
    {
        try
        {
            await _previewHost.InitializeAsync(PreviewWebView, Path.Combine(AppContext.BaseDirectory, "WebAssets"));
        }
        catch (Exception error)
        {
            PreviewError.Text = $"预览不可用：{error.GetType().Name}";
            PreviewErrorPanel.Visibility = Visibility.Visible;
        }
    }

    private void OnUnloaded(object sender, RoutedEventArgs args)
    {
        _router.MessageReceived -= OnWebMessageReceived;
        _previewHost.ProtocolViolation -= OnProtocolViolation;
        _previewHost.StateChanged -= OnPreviewStateChanged;
        ViewModel.PropertyChanged -= OnViewModelPropertyChanged;
        _previewHost.Dispose();
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs args)
    {
        if (args.PropertyName == nameof(RecognitionViewModel.CurrentInput) && _bridgeReady) _ = SendInputAsync();
        if (args.PropertyName == nameof(RecognitionViewModel.ResultText) && _bridgeReady) _ = SendResultAsync();
        if (args.PropertyName == nameof(RecognitionViewModel.Result) && ViewModel.Result is not null) _resultActions.SetResult(ViewModel.Result);
    }

    private async Task SendInputAsync()
    {
        RecognitionInput? input = ViewModel.CurrentInput;
        if (input is null) return;
        AppLog.Info($"SendInputAsync: dataLen={input.Data.Length} mediaType={input.MediaType} bridgeReady={_bridgeReady}");

        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        try
        {
            Uri source = await _previewHost.SetPreviewImageAsync(
                input.Data,
                input.MediaType,
                timeout.Token);
            AppLog.Info($"SendInputAsync: image URL={source.AbsoluteUri}");
            await _previewHost.RequestAsync(
                "preview.setImage",
                new { url = source.AbsoluteUri, name = input.DisplayName, origin = input.Origin },
                timeout.Token);
            AppLog.Info("SendInputAsync: preview.setImage sent OK");
        }
        catch (Exception error) when (error is OperationCanceledException or WebBridgeProtocolException or InvalidOperationException)
        {
            AppLog.Error("SendInputAsync: failed", error);
            PreviewBridgeStatus.Text = "图片预览同步失败";
        }
    }

    private async Task SendResultAsync()
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        try
        {
            await _previewHost.RequestAsync("preview.setResult", new { format = "plain", text = ViewModel.ResultText }, timeout.Token);
        }
        catch (Exception error) when (error is OperationCanceledException or WebBridgeProtocolException or InvalidOperationException)
        {
            PreviewBridgeStatus.Text = "结果预览同步失败";
        }
    }

    private async void OnFileClicked(object sender, RoutedEventArgs args) => await ViewModel.RecognizeFileAsync(CancellationToken.None);
    private async void OnClipboardClicked(object sender, RoutedEventArgs args) => await ViewModel.RecognizeClipboardAsync(CancellationToken.None);
    private async void OnScreenshotClicked(object sender, RoutedEventArgs args) => await ViewModel.RecognizeScreenshotAsync(CancellationToken.None);
    private void OnCancelClicked(object sender, RoutedEventArgs args) => ViewModel.Cancel();
    private async void OnCopyRichClicked(object sender, RoutedEventArgs args) => await RunResultActionAsync(ct => _resultActions.CopyAsync(ResultCopyFormat.Rich, ct));
    private async void OnCopyMarkdownClicked(object sender, RoutedEventArgs args) => await RunResultActionAsync(ct => _resultActions.CopyAsync(ResultCopyFormat.Markdown, ct));
    private async void OnCopyPlainClicked(object sender, RoutedEventArgs args) => await RunResultActionAsync(ct => _resultActions.CopyAsync(ResultCopyFormat.Plain, ct));
    private async void OnExportHtmlClicked(object sender, RoutedEventArgs args) => await RunResultActionAsync(ct => _resultActions.ExportAsync(ResultExportFormat.Html, ct));
    private async void OnExportMarkdownClicked(object sender, RoutedEventArgs args) => await RunResultActionAsync(ct => _resultActions.ExportAsync(ResultExportFormat.Markdown, ct));
    private async void OnExportTextClicked(object sender, RoutedEventArgs args) => await RunResultActionAsync(ct => _resultActions.ExportAsync(ResultExportFormat.Text, ct));

    private async Task RunResultActionAsync(Func<CancellationToken, Task> action)
    {
        try { await action(CancellationToken.None); PreviewBridgeStatus.Text = "结果操作完成"; }
        catch (InvalidOperationException) { PreviewBridgeStatus.Text = "请先完成识别"; }
        catch (Exception error) when (error is InferenceClientException or IOException or ClipboardBusyException) { PreviewBridgeStatus.Text = "结果操作失败，请重试"; }
    }

    private Task RunResultActionAsync<T>(Func<CancellationToken, Task<T>> action) => RunResultActionAsync(async ct => { await action(ct); });

    private void OnDragOver(object sender, DragEventArgs args)
    {
        if (args.DataView.Contains(StandardDataFormats.StorageItems)) args.AcceptedOperation = DataPackageOperation.Copy;
    }

    private async void OnDrop(object sender, DragEventArgs args)
    {
        if (!args.DataView.Contains(StandardDataFormats.StorageItems)) return;
        IReadOnlyList<IStorageItem> items = await args.DataView.GetStorageItemsAsync();
        if (items.FirstOrDefault() is StorageFile file) await ViewModel.RecognizeDroppedFileAsync(file.Path, CancellationToken.None);
    }

    [DllImport("user32.dll")]
    private static extern nint GetActiveWindow();
}
