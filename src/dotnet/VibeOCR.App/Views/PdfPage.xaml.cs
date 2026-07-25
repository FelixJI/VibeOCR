using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.Features.Pdf;

namespace VibeOCR.App.Views;

public sealed partial class PdfPage : Page
{
    public PdfPage(PdfViewModel viewModel)
    {
        ViewModel = viewModel;
        InitializeComponent();
    }

    public PdfViewModel ViewModel { get; }

    private int[] SelectedIndices()
    {
        if (PageGrid.SelectedItems.Count == 0 && ViewModel.PageCount > 0)
            return Enumerable.Range(0, ViewModel.PageCount).ToArray();
        return PageGrid.SelectedItems
            .OfType<PdfPageViewModel>()
            .Select(p => p.Index)
            .ToArray();
    }

    private void OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var selected = PageGrid.SelectedItems.OfType<PdfPageViewModel>().ToArray();
        ViewModel.SelectedPage = selected.Length > 0 ? selected[0].Index : -1;
    }

    private async void OnOpenClicked(object sender, RoutedEventArgs e)
        => await ViewModel.OpenAsync(CancellationToken.None);

    private void OnCloseClicked(object sender, RoutedEventArgs e)
        => ViewModel.CloseSession();

    private async void OnRotateCwClicked(object sender, RoutedEventArgs e)
        => await ViewModel.RotateAsync(SelectedIndices(), 90, CancellationToken.None);

    private async void OnRotateCcwClicked(object sender, RoutedEventArgs e)
        => await ViewModel.RotateAsync(SelectedIndices(), -90, CancellationToken.None);

    private async void OnRotateAllClicked(object sender, RoutedEventArgs e)
    {
        if (ViewModel.PageCount == 0) return;
        await ViewModel.RotateAsync(Enumerable.Range(0, ViewModel.PageCount).ToArray(), 90, CancellationToken.None);
    }

    private async void OnDeletePagesClicked(object sender, RoutedEventArgs e)
        => await ViewModel.DeletePagesAsync(SelectedIndices(), CancellationToken.None);

    private async void OnAddTextLayerClicked(object sender, RoutedEventArgs e)
        => await ViewModel.StartOcrAsync(SelectedIndices(), overwrite: false, CancellationToken.None);

    // DeleteTextLayersAsync was removed from the v2 PdfViewModel; no-op until re-introduced.
    private async void OnDeleteTextLayersClicked(object sender, RoutedEventArgs e)
        => await Task.CompletedTask;

    private async void OnSaveClicked(object sender, RoutedEventArgs e)
        => await ViewModel.SaveAsync(path: null!, CancellationToken.None);
}
