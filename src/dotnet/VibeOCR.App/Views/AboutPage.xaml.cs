using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.Features.Shell;
using VibeOCR.App.Features.Update;

namespace VibeOCR.App.Views;

public sealed partial class AboutPage : Page
{
    public AboutPage(ShellViewModel shell, UpdateViewModel update)
    {
        Shell = shell;
        Update = update;
        InitializeComponent();
    }

    public ShellViewModel Shell { get; }
    public UpdateViewModel Update { get; }

    private async void OnCheckUpdateClicked(object sender, RoutedEventArgs e)
        => await Update.CheckAsync(CancellationToken.None);

    private async void OnDownloadUpdateClicked(object sender, RoutedEventArgs e)
        => await Update.DownloadAndVerifyAsync(CancellationToken.None);

    private void OnCancelUpdateClicked(object sender, RoutedEventArgs e) => Update.Cancel();
}
