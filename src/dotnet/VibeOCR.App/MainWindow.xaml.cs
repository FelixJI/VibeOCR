using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Windows.Graphics;
using VibeOCR.App.Features.Batch;
using VibeOCR.App.Features.Pdf;
using VibeOCR.App.Features.QrCode;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.Features.Settings;
using VibeOCR.App.Services;
using VibeOCR.App.ViewModels;
using VibeOCR.App.Views;
using VibeOCR.Platform.Bootstrap;
using WinRT.Interop;

namespace VibeOCR.App;

public sealed partial class MainWindow : Window
{
    private const int DefaultWidth = 900;
    private const int DefaultHeight = 600;
    private const int MinWidth = 720;
    private const int MinHeight = 480;

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool IsZoomed(IntPtr hWnd);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    private readonly DiagnosticsViewModel _diagnostics;
    private readonly PortableLayout _layout;
    private readonly Func<RecognitionViewModel> _recognitionFactory;
    private readonly Func<BatchViewModel> _batchFactory;
    private readonly Func<QrCodePage> _qrCodePageFactory;
    private readonly Func<PdfPage> _pdfPageFactory;
    private readonly Func<SettingsPage> _settingsPageFactory;
    private readonly Func<AboutPage> _aboutPageFactory;
    private readonly WindowLayoutStore _layoutStore;
    private RecognitionViewModel? _recognition;
    private BatchViewModel? _batch;
    private QrCodePage? _qrCodePage;
    private PdfPage? _pdfPage;
    private SettingsPage? _settingsPage;

    public MainWindow(DiagnosticsViewModel diagnostics, PortableLayout layout, Func<RecognitionViewModel> recognitionFactory, Func<BatchViewModel> batchFactory, Func<QrCodePage> qrCodePageFactory, Func<PdfPage> pdfPageFactory, Func<SettingsPage> settingsPageFactory, Func<AboutPage> aboutPageFactory, WindowLayoutStore layoutStore)
    {
        _diagnostics = diagnostics; _layout = layout; _recognitionFactory = recognitionFactory; _batchFactory = batchFactory; _qrCodePageFactory = qrCodePageFactory; _pdfPageFactory = pdfPageFactory; _settingsPageFactory = settingsPageFactory; _aboutPageFactory = aboutPageFactory; _layoutStore = layoutStore;
        InitializeComponent(); Title = "VibeOCR WinUI"; RootNavigation.SelectedItem = RootNavigation.MenuItems[0]; ShowHome();
        ApplyPersistedOrDefaultGeometry();
    }

    private void ApplyPersistedOrDefaultGeometry()
    {
        IntPtr hwnd = WindowNative.GetWindowHandle(this);
        WindowMinSizeEnforcer.Apply(hwnd, MinWidth, MinHeight);

        WindowGeometry? saved = _layoutStore.Load();
        var presenter = (OverlappedPresenter)AppWindow.Presenter;
        if (saved is { } geometry)
        {
            AppWindow.MoveAndResize(new RectInt32(geometry.X, geometry.Y, geometry.Width, geometry.Height));
            if (geometry.IsMaximized)
            {
                presenter.Maximize();
            }
        }
        else
        {
            // 默认 900x600 居中到主显示器工作区。
            DisplayArea area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Nearest);
            RectInt32 work = area.WorkArea;
            int x = work.X + Math.Max(0, (work.Width - DefaultWidth) / 2);
            int y = work.Y + Math.Max(0, (work.Height - DefaultHeight) / 2);
            AppWindow.MoveAndResize(new RectInt32(x, y, DefaultWidth, DefaultHeight));
        }
    }

    internal WindowGeometry? CaptureGeometry()
    {
        IntPtr hwnd = WindowNative.GetWindowHandle(this);
        if (IsIconic(hwnd))
        {
            // 最小化时不写回，避免下次以最小化尺寸恢复。
            return null;
        }
        bool maximized = IsZoomed(hwnd);
        GetWindowRect(hwnd, out RECT rect);
        return new WindowGeometry(rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top, maximized);
    }

    private void OnSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        string? destination = (args.SelectedItemContainer as NavigationViewItem)?.Tag as string;
        if (destination == "diagnostics") { ContentFrame.Content = new DiagnosticsPage(_diagnostics, _layout); return; }
        if (destination == "recognition") { _recognition ??= _recognitionFactory(); ContentFrame.Content = new RecognitionPage(_recognition); return; }
        if (destination == "batch") { _batch ??= _batchFactory(); ContentFrame.Content = new BatchPage(_batch); return; }
        if (destination == "qrcode") { _qrCodePage ??= _qrCodePageFactory(); ContentFrame.Content = _qrCodePage; return; }
        if (destination == "pdf") { _pdfPage ??= _pdfPageFactory(); ContentFrame.Content = _pdfPage; return; }
        if (destination == "settings") { _settingsPage ??= _settingsPageFactory(); ContentFrame.Content = _settingsPage; return; }
        if (destination == "about") { ContentFrame.Content = _aboutPageFactory(); return; }
        ShowHome();
    }

    private void ShowHome() => ContentFrame.Content = new Grid { Children = { new TextBlock { Text = "VibeOCR", FontSize = 28, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center } } };

    internal async Task RecognizeScreenshotAsync()
    {
        NavigationViewItem item = RootNavigation.MenuItems
            .OfType<NavigationViewItem>()
            .Single(candidate => Equals(candidate.Tag, "recognition"));
        RootNavigation.SelectedItem = item;
        _recognition ??= _recognitionFactory();
        ContentFrame.Content = new RecognitionPage(_recognition);
        await _recognition.RecognizeScreenshotAsync(CancellationToken.None);
    }
}
