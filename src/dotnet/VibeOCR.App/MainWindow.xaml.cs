using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.Features.Batch;
using VibeOCR.App.Features.QrCode;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.ViewModels;
using VibeOCR.App.Views;
using VibeOCR.Platform.Bootstrap;

namespace VibeOCR.App;

public sealed partial class MainWindow : Window
{
    private readonly DiagnosticsViewModel _diagnostics;
    private readonly PortableLayout _layout;
    private readonly Func<RecognitionViewModel> _recognitionFactory;
    private readonly Func<BatchViewModel> _batchFactory;
    private readonly Func<QrCodePage> _qrCodePageFactory;
    private RecognitionViewModel? _recognition;
    private BatchViewModel? _batch;
    private QrCodePage? _qrCodePage;

    public MainWindow(DiagnosticsViewModel diagnostics, PortableLayout layout, Func<RecognitionViewModel> recognitionFactory, Func<BatchViewModel> batchFactory, Func<QrCodePage> qrCodePageFactory)
    {
        _diagnostics = diagnostics; _layout = layout; _recognitionFactory = recognitionFactory; _batchFactory = batchFactory; _qrCodePageFactory = qrCodePageFactory;
        InitializeComponent(); Title = "VibeOCR WinUI"; RootNavigation.SelectedItem = RootNavigation.MenuItems[0]; ShowHome();
    }

    private void OnSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        string? destination = (args.SelectedItemContainer as NavigationViewItem)?.Tag as string;
        if (destination == "diagnostics") { ContentFrame.Content = new DiagnosticsPage(_diagnostics, _layout); return; }
        if (destination == "recognition") { _recognition ??= _recognitionFactory(); ContentFrame.Content = new RecognitionPage(_recognition); return; }
        if (destination == "batch") { _batch ??= _batchFactory(); ContentFrame.Content = new BatchPage(_batch); return; }
        if (destination == "qrcode") { _qrCodePage ??= _qrCodePageFactory(); ContentFrame.Content = _qrCodePage; return; }
        ShowHome();
    }

    private void ShowHome() => ContentFrame.Content = new Grid { Children = { new TextBlock { Text = "VibeOCR WinUI 迁移预览", FontSize = 28, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center } } };
}
