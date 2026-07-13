using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace VibeOCR.App.Features.Update;

/// <summary>
/// Update view model: check for updates, download, verify hash, and cancel.
/// The production updater entry is NOT switched here (Task 5.4); this view
/// model only orchestrates the UI affordance against an injected update source.
/// </summary>
public sealed class UpdateViewModel(IUpdateSource source) : INotifyPropertyChanged
{
    private readonly IUpdateSource _source = source ?? throw new ArgumentNullException(nameof(source));
    private CancellationTokenSource? _activeRun;
    private bool _isBusy;
    private string _status = string.Empty;
    private string? _latestVersion;
    private bool _updateAvailable;

    public event PropertyChangedEventHandler? PropertyChanged;

    public bool IsBusy { get => _isBusy; private set => SetField(ref _isBusy, value); }
    public string Status { get => _status; private set => SetField(ref _status, value); }
    public string? LatestVersion { get => _latestVersion; private set => SetField(ref _latestVersion, value); }
    public bool UpdateAvailable { get => _updateAvailable; private set => SetField(ref _updateAvailable, value); }

    public async Task CheckAsync(CancellationToken cancellationToken)
    {
        if (IsBusy) return;
        using var run = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _activeRun = run;
        IsBusy = true;
        Status = "正在检查更新";
        try
        {
            (string version, bool available) = await _source.FetchLatestAsync(run.Token);
            LatestVersion = version;
            UpdateAvailable = available;
            Status = available ? $"发现新版本 {version}" : "已是最新版本";
        }
        catch (OperationCanceledException)
        {
            Status = "已取消";
        }
        catch (Exception)
        {
            Status = "检查更新失败，请检查网络";
        }
        finally
        {
            IsBusy = false;
        }
    }

    public async Task DownloadAndVerifyAsync(CancellationToken cancellationToken)
    {
        if (IsBusy || !UpdateAvailable) return;
        using var run = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _activeRun = run;
        IsBusy = true;
        Status = "正在下载";
        try
        {
            bool ok = await _source.DownloadVerifyAsync(run.Token);
            Status = ok ? "下载完成，校验通过" : "校验失败，请重试";
        }
        catch (OperationCanceledException)
        {
            Status = "已取消";
        }
        catch (Exception)
        {
            Status = "下载失败，请检查网络";
        }
        finally
        {
            IsBusy = false;
        }
    }

    public void Cancel() => _activeRun?.Cancel();

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}

public interface IUpdateSource
{
    Task<(string Version, bool Available)> FetchLatestAsync(CancellationToken cancellationToken);
    Task<bool> DownloadVerifyAsync(CancellationToken cancellationToken);
}
