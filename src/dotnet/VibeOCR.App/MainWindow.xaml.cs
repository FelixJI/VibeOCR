using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
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
    private RecognitionViewModel? _recognition;

    public MainWindow(
        DiagnosticsViewModel diagnostics,
        PortableLayout layout,
        Func<RecognitionViewModel> recognitionFactory)
    {
        _diagnostics = diagnostics;
        _layout = layout;
        _recognitionFactory = recognitionFactory;
        InitializeComponent();
        Title = "VibeOCR WinUI 预览";
        RootNavigation.SelectedItem = RootNavigation.MenuItems[0];
        ShowHome();
    }

    private void OnSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        string? destination = (args.SelectedItemContainer as NavigationViewItem)?.Tag as string;
        if (destination == "diagnostics")
        {
            ContentFrame.Content = new DiagnosticsPage(_diagnostics, _layout);
            return;
        }

        if (destination == "recognition")
        {
            _recognition ??= _recognitionFactory();
            ContentFrame.Content = new RecognitionPage(_recognition);
            return;
        }

        ShowHome();
    }

    private void ShowHome()
    {
        ContentFrame.Content = new Grid
        {
            Children =
            {
                new TextBlock
                {
                    Text = "VibeOCR WinUI 旁路预览",
                    FontSize = 28,
                    HorizontalAlignment = HorizontalAlignment.Center,
                    VerticalAlignment = VerticalAlignment.Center,
                },
            },
        };
    }
}
