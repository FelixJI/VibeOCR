using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.Features.QrCode;

namespace VibeOCR.App.Views;

public sealed partial class QrCodePage : Page
{
    private readonly QrCodeSaveCommands _saveCommands;

    public QrCodePage(QrCodeViewModel viewModel, QrCodeSaveCommands saveCommands)
    {
        ViewModel = viewModel;
        _saveCommands = saveCommands;
        InitializeComponent();
    }

    public QrCodeViewModel ViewModel { get; }

    private async void OnPickFileClicked(object sender, RoutedEventArgs args)
        => await ViewModel.DecodeAsync(QrCodeInputKind.File, CancellationToken.None);

    private async void OnPasteClicked(object sender, RoutedEventArgs args)
        => await ViewModel.DecodeAsync(QrCodeInputKind.Clipboard, CancellationToken.None);

    private void OnClearClicked(object sender, RoutedEventArgs args)
    {
        ViewModel.Cancel();
        ViewModel.Codes.Clear();
    }

    private void OnOpenUrlClicked(object sender, RoutedEventArgs args)
    {
        if (sender is FrameworkElement element && element.DataContext is QrCodeResult code &&
            code.IsUrl is true && Uri.TryCreate(code.Data, UriKind.Absolute, out Uri? uri) &&
            uri.Scheme is "http" or "https")
        {
            _ = Windows.System.Launcher.LaunchUriAsync(uri);
        }
    }

    private async void OnGenerateClicked(object sender, RoutedEventArgs args)
        => await ViewModel.GenerateAsync(CancellationToken.None);

    private async void OnSaveClicked(object sender, RoutedEventArgs args)
    {
        string? base64 = ViewModel.GeneratedImageBase64;
        if (string.IsNullOrEmpty(base64)) return;
        await _saveCommands.SaveAsync(base64, "qrcode.png", CancellationToken.None);
    }

    private void OnPivotSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_generateActions is null || _decodeActions is null) return;
        bool isDecode = e.AddedItems.Count > 0 && (e.AddedItems[0] as PivotItem)?.Header is string header && header is "识别";
        _generateActions.Visibility = isDecode ? Visibility.Collapsed : Visibility.Visible;
        _decodeActions.Visibility = isDecode ? Visibility.Visible : Visibility.Collapsed;
    }
}
