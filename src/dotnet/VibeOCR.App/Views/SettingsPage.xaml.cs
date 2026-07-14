using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.Features.Settings;
using VibeOCR.App.Features.Shell;

namespace VibeOCR.App.Views;

public sealed partial class SettingsPage : Page
{
    public SettingsPage(SettingsViewModel viewModel, ShellViewModel shell)
    {
        ViewModel = viewModel;
        Shell = shell;
        InitializeComponent();
        _ = viewModel.LoadSnapshotAsync(CancellationToken.None);
    }

    public SettingsViewModel ViewModel { get; }
    public ShellViewModel Shell { get; }

    private async void OnRefreshClicked(object sender, RoutedEventArgs e)
        => await ViewModel.LoadSnapshotAsync(CancellationToken.None);

    private async void OnSwitchBackendClicked(object sender, RoutedEventArgs e)
        => await ViewModel.SwitchBackendAsync(ViewModel.PendingBackend, CancellationToken.None);

    // 以下从 AboutPage 迁移而来（可工作的 handler，保留绑定）：
    private void OnApplyHotkeyClicked(object sender, RoutedEventArgs e) => Shell.ApplyHotkey();
    private void OnStartupClicked(object sender, RoutedEventArgs e)
        => Shell.SetStartWithSystem(Shell.StartWithSystem);
    private void OnHideToTrayClicked(object sender, RoutedEventArgs e) => Shell.HideToTray();
}
