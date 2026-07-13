using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.Features.Settings;

namespace VibeOCR.App.Views;

public sealed partial class SettingsPage : Page
{
    public SettingsPage(SettingsViewModel viewModel)
    {
        ViewModel = viewModel;
        InitializeComponent();
        _ = viewModel.LoadSnapshotAsync(CancellationToken.None);
    }

    public SettingsViewModel ViewModel { get; }

    private async void OnRefreshClicked(object sender, RoutedEventArgs e)
        => await ViewModel.LoadSnapshotAsync(CancellationToken.None);

    private async void OnSwitchBackendClicked(object sender, RoutedEventArgs e)
        => await ViewModel.SwitchBackendAsync(ViewModel.PendingBackend, CancellationToken.None);
}
