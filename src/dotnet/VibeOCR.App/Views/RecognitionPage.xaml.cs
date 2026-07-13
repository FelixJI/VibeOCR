using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using VibeOCR.App.Features.Recognition;

namespace VibeOCR.App.Views;

public sealed partial class RecognitionPage : Page
{
    public RecognitionPage(RecognitionViewModel viewModel)
    {
        ViewModel = viewModel ?? throw new ArgumentNullException(nameof(viewModel));
        InitializeComponent();
    }

    public RecognitionViewModel ViewModel { get; }

    private async void OnFileClicked(object sender, RoutedEventArgs args) =>
        await ViewModel.RecognizeFileAsync(CancellationToken.None);

    private async void OnClipboardClicked(object sender, RoutedEventArgs args) =>
        await ViewModel.RecognizeClipboardAsync(CancellationToken.None);

    private async void OnScreenshotClicked(object sender, RoutedEventArgs args) =>
        await ViewModel.RecognizeScreenshotAsync(CancellationToken.None);

    private void OnCancelClicked(object sender, RoutedEventArgs args) => ViewModel.Cancel();

    private void OnDragOver(object sender, DragEventArgs args)
    {
        if (args.DataView.Contains(StandardDataFormats.StorageItems))
        {
            args.AcceptedOperation = DataPackageOperation.Copy;
        }
    }

    private async void OnDrop(object sender, DragEventArgs args)
    {
        if (!args.DataView.Contains(StandardDataFormats.StorageItems))
        {
            return;
        }

        IReadOnlyList<IStorageItem> items = await args.DataView.GetStorageItemsAsync();
        if (items.FirstOrDefault() is StorageFile file)
        {
            await ViewModel.RecognizeDroppedFileAsync(file.Path, CancellationToken.None);
        }
    }
}
