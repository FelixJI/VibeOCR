using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;

namespace VibeOCR.App.Features.Settings;

/// <summary>
/// View model for the settings/dependency/backend tab. Reads the WorkerHost
/// settings snapshot and exposes backend switching, dependency installation
/// status, and preload state. Backend switch and dependency install never
/// auto-retry; network/mirror errors surface as localized status.
/// </summary>
public class SettingsViewModel(IWorkerHostClient worker) : INotifyPropertyChanged
{
    private readonly IWorkerHostClient _worker = worker ?? throw new ArgumentNullException(nameof(worker));
    private CancellationTokenSource? _activeRun;
    private long _generation;
    private bool _isBusy;
    private string _status = "正在读取设置";
    private string _backend = "cpu";
    private string _pendingBackend = "cpu";
    private bool _restartRequired;
    private bool _gpuAvailable;

    public event PropertyChangedEventHandler? PropertyChanged;

    public ObservableCollection<string> PreloadPipelines { get; } = [];
    public int TtlSeconds { get; private set; }

    public bool IsBusy { get => _isBusy; private set => SetField(ref _isBusy, value); }
    public string Status { get => _status; private set => SetField(ref _status, value); }
    public string Backend { get => _backend; private set => SetField(ref _backend, value); }
    public string PendingBackend { get => _pendingBackend; set => SetField(ref _pendingBackend, value); }
    public bool RestartRequired { get => _restartRequired; private set => SetField(ref _restartRequired, value); }
    public bool GpuAvailable { get => _gpuAvailable; private set => SetField(ref _gpuAvailable, value); }
    public bool CanSwitchBackend => !IsBusy && !string.Equals(Backend, PendingBackend, StringComparison.Ordinal);

    public async Task LoadSnapshotAsync(CancellationToken cancellationToken)
    {
        long generation = Interlocked.Increment(ref _generation);
        IsBusy = true;
        Status = "正在读取设置";
        try
        {
            SettingsSnapshotResponse response = await _worker.CallAsync<
                SettingsSnapshotRequest, SettingsSnapshotResponse>(
                RpcMethods.SettingsSnapshot,
                new SettingsSnapshotRequest(),
                cancellationToken);
            if (generation != Volatile.Read(ref _generation)) return;
            Backend = response.Backend;
            PendingBackend = response.Backend;
            TtlSeconds = response.TtlSeconds;
            PreloadPipelines.Clear();
            foreach (string pipeline in response.PreloadPipelines) PreloadPipelines.Add(pipeline);
            Status = $"后端：{response.Backend}；预热管线：{response.PreloadPipelines.Length} 个";
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
                Status = Localize(error.Error.Code);
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            Status = "Worker 已断开，请重试";
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation)) IsBusy = false;
        }
    }

    /// <summary>
    /// Switch the OCR backend (cpu/gpu). Never auto-retries; a network/mirror
    /// error surfaces as a localized status and leaves the current backend
    /// unchanged. A backend change requires a restart.
    /// </summary>
    public async Task SwitchBackendAsync(string target, CancellationToken cancellationToken)
    {
        if (IsBusy) return;
        if (string.Equals(Backend, target, StringComparison.Ordinal))
        {
            Status = "已是该后端";
            return;
        }
        if (target == "gpu" && !GpuAvailable)
        {
            Status = "未检测到可用 GPU";
            return;
        }
        long generation = Interlocked.Increment(ref _generation);
        IsBusy = true;
        Status = $"正在切换到 {target}";
        try
        {
            await OnSwitchBackendCoreAsync(target, cancellationToken);
            if (generation != Volatile.Read(ref _generation)) return;
            Backend = target;
            PendingBackend = target;
            RestartRequired = true;
            Status = $"已切换到 {target}，需重启生效";
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) Status = "已取消";
        }
        catch (WorkerRpcException error)
        {
            // Network/mirror/dependency errors never auto-retry.
            if (generation == Volatile.Read(ref _generation))
                Status = Localize(error.Error.Code);
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            Status = "切换失败，后端未改变";
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation)) IsBusy = false;
        }
    }

    /// <summary>
    /// Persist the backend switch through the WorkerHost
    /// <c>settings.switch_backend</c> RPC. The mutation never auto-retries on
    /// the worker side; a failure propagates to <see cref="SwitchBackendAsync"/>
    /// which surfaces a localized status and leaves the current backend.
    /// </summary>
    protected virtual async Task OnSwitchBackendCoreAsync(string target, CancellationToken cancellationToken)
    {
        await _worker.CallAsync<SwitchBackendRequest, SwitchBackendResponse>(
            RpcMethods.SwitchBackend,
            new SwitchBackendRequest { Backend = target },
            cancellationToken);
    }

    public void DetectGpu(bool available)
    {
        GpuAvailable = available;
        if (!available && PendingBackend == "gpu") PendingBackend = "cpu";
    }

    public void Cancel() =>
        Interlocked.Exchange(ref _activeRun, null)?.Cancel();

    private static string Localize(ErrorCode code) => code switch
    {
        ErrorCode.DependencyMissing => "依赖尚未安装",
        ErrorCode.WorkerUnavailable => "Worker 暂不可用，请重试",
        ErrorCode.ResourceExhausted => "内存或显存不足",
        ErrorCode.TaskTimeout => "操作超时，请重试",
        _ => "操作失败",
    };

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        if (name is nameof(IsBusy) or nameof(Backend) or nameof(PendingBackend))
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(CanSwitchBackend)));
        }
    }
}
