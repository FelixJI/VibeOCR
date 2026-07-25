using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage;
using Windows.Storage.Pickers;
using VibeOCR.App.Features.Batch;

namespace VibeOCR.App.Views;

public sealed partial class BatchPage : Page
{
    public BatchPage(BatchViewModel viewModel) { ViewModel = viewModel; InitializeComponent(); }
    public BatchViewModel ViewModel { get; }
    private async void OnAddClicked(object sender, RoutedEventArgs args) => await ViewModel.PickFilesAsync(CancellationToken.None);
    private async void OnStartClicked(object sender, RoutedEventArgs args) => await ViewModel.StartAsync(CancellationToken.None);
    private void OnCancelAllClicked(object sender, RoutedEventArgs args) => ViewModel.CancelAll();
    private void OnClearClicked(object sender, RoutedEventArgs args) => ViewModel.ResetTemporaryQueue();
    private void OnMoveUpClicked(object sender, RoutedEventArgs args) => WithItem(sender, item => ViewModel.Move(item.Id, -1));
    private void OnMoveDownClicked(object sender, RoutedEventArgs args) => WithItem(sender, item => ViewModel.Move(item.Id, 1));
    // CancelItem(Guid) was removed from the v2 BatchViewModel; per-item cancel is a no-op for now.
    private void OnCancelItemClicked(object sender, RoutedEventArgs args) { }
    private void OnRemoveClicked(object sender, RoutedEventArgs args) => WithItem(sender, item => ViewModel.Remove(item.Id));
    private async void OnExportAllClicked(object sender, RoutedEventArgs args)
    {
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.DocumentsLibrary };
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, GetActiveWindow());
        StorageFolder? folder = await picker.PickSingleFolderAsync();
        if (folder is not null) await ViewModel.ExportAllAsync(folder.Path, "markdown", CancellationToken.None);
    }
    private static void WithItem(object sender, Action<BatchItemViewModel> action) { if ((sender as FrameworkElement)?.DataContext is BatchItemViewModel item) action(item); }
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern nint GetActiveWindow();
}
