using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Features.Settings;

public sealed class SettingsViewModel(IInferenceClient inference) : INotifyPropertyChanged
{
    private long _generation;
    private bool _isBusy;
    private string _status = "正在读取设置";
    private string _backend = "cpu";
    private string _pendingBackend = "cpu";
    private bool _restartRequired;
    private bool _gpuAvailable;

    public event PropertyChangedEventHandler? PropertyChanged;
    public ObservableCollection<string> PreloadPipelines { get; } = [];
    public ObservableCollection<ResidencyEntry> ResidencyEntries { get; } = [];
    public ObservableCollection<PipelineSpec> ResidencyPipelines { get; } = [];
    public int DefaultTtlSeconds { get; private set; } = 300;
    public int? VramTotalMb { get; private set; }
    public int? VramUsedMb { get; private set; }
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
        if (generation == Volatile.Read(ref _generation)) { IsBusy = true; Status = "正在读取模型驻留状态"; }
        try
        {
            ResidencyStatus status = await inference.GetResidencyAsync(cancellationToken);
            if (generation != Volatile.Read(ref _generation)) return;
            DefaultTtlSeconds = status.DefaultTtlSeconds;
            VramTotalMb = status.VramTotalMb;
            VramUsedMb = status.VramUsedMb;
            ResidencyEntries.Clear(); foreach (var e in status.Entries) ResidencyEntries.Add(e);
            ResidencyPipelines.Clear(); foreach (var p in status.Pipelines) ResidencyPipelines.Add(p);
            PropertyChanged?.Invoke(this, new(nameof(DefaultTtlSeconds)));
            PropertyChanged?.Invoke(this, new(nameof(VramTotalMb)));
            PropertyChanged?.Invoke(this, new(nameof(VramUsedMb)));
            Status = $"默认 TTL {status.DefaultTtlSeconds}s；已驻留管线 {status.Entries.Count} 个";
        }
        catch (OperationCanceledException) { if (generation == Volatile.Read(ref _generation)) Status = "已取消"; }
        catch (InferenceClientException error) { if (generation == Volatile.Read(ref _generation)) Status = LocalizeV2(error.Code); }
        catch (Exception) when (generation == Volatile.Read(ref _generation)) { Status = "Supervisor 已断开，请重试"; }
        finally { if (generation == Volatile.Read(ref _generation)) IsBusy = false; }
    }

    public void DetectGpu(bool available) { GpuAvailable = available; if (!available && PendingBackend == "gpu") PendingBackend = "cpu"; }
    public void Cancel() { }

    private static string LocalizeV2(HttpV2ErrorCode code) => code switch
    {
        HttpV2ErrorCode.Unauthorized => "Supervisor 会话无效",
        HttpV2ErrorCode.ForbiddenLoopback => "Supervisor 拒绝非本地连接",
        HttpV2ErrorCode.BackendUnavailable or HttpV2ErrorCode.TransientBackend => "Supervisor 暂不可用，请重试",
        HttpV2ErrorCode.OutOfMemory => "内存或显存不足",
        HttpV2ErrorCode.SupervisorDraining => "Supervisor 正在关闭，请稍后",
        HttpV2ErrorCode.ProtocolMismatch => "Supervisor 协议不兼容",
        _ => "操作失败",
    };

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        if (name is nameof(IsBusy) or nameof(Backend) or nameof(PendingBackend))
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(CanSwitchBackend)));
    }
}
