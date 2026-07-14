using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace VibeOCR.App.Features.Update;

/// <summary>
/// Update view model: check, download, verify, hand off to the independent
/// updater, and close the running application once the updater is ready.
/// </summary>
public sealed class UpdateViewModel(IUpdateSource source, Action? requestShutdown = null)
    : INotifyPropertyChanged
{
    private readonly IUpdateSource _source = source ?? throw new ArgumentNullException(nameof(source));
    private readonly Action _requestShutdown = requestShutdown ?? (() => { });
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
            bool verified = await _source.DownloadVerifyAsync(run.Token);
            if (!verified)
            {
                Status = "校验失败，请重试";
                return;
            }
            Status = "正在启动更新器";
            bool launched = await _source.LaunchUpdaterAsync(run.Token);
            if (!launched)
            {
                Status = "更新器启动失败，请重试";
                return;
            }
            Status = "更新器已就绪，应用即将退出";
            _requestShutdown();
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
    Task<bool> LaunchUpdaterAsync(CancellationToken cancellationToken);
}
