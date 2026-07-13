using System.ComponentModel;
using System.Runtime.CompilerServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.ViewModels;
using VibeOCR.Platform.Bootstrap;

namespace VibeOCR.App.Views;

public sealed partial class DiagnosticsPage : Page, INotifyPropertyChanged
{
    private readonly PortableLayout _layout;
    private string _exportStatus = string.Empty;

    public DiagnosticsPage(DiagnosticsViewModel viewModel, PortableLayout layout)
    {
        ViewModel = viewModel;
        _layout = layout;
        InitializeComponent();
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public DiagnosticsViewModel ViewModel { get; }

    public string ExportStatus
    {
        get => _exportStatus;
        private set
        {
            _exportStatus = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(ExportStatus)));
        }
    }

    private async void OnRepairClicked(object sender, RoutedEventArgs args)
    {
        if ((sender as Button)?.Tag is not PrerequisiteKind kind)
        {
            return;
        }

        PrerequisiteStatus item = ViewModel.Prerequisites.Single(status => status.Kind == kind);
        if (item.IsInstalled)
        {
            ExportStatus = $"{kind} 已就绪，无需修复。";
            return;
        }

        try
        {
            await ViewModel.RepairAsync(kind, CancellationToken.None);
            ExportStatus = $"已打开 {kind} 的显式修复入口。";
        }
        catch (Exception error)
        {
            ExportStatus = error.Message;
        }
    }

    private async void OnExportClicked(object sender, RoutedEventArgs args)
    {
        string path = Path.Combine(_layout.DataRoot, "diagnostics", "diagnostics.json");
        try
        {
            await ViewModel.ExportAsync(path, CancellationToken.None);
            ExportStatus = $"已导出：{path}";
        }
        catch (Exception error)
        {
            ExportStatus = error.Message;
        }
    }
}
