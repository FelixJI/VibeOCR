using System.ComponentModel;
using System.Collections.ObjectModel;
using System.Runtime.CompilerServices;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.RegularExpressions;
using VibeOCR.Platform.Bootstrap;

namespace VibeOCR.App.ViewModels;

public enum WorkerHealthState
{
    NotReady,
    Connecting,
    Ready,
    ProtocolIncompatible,
    Faulted,
}

public sealed record WorkerHealth(
    WorkerHealthState State,
    string? WorkerVersion,
    int? ProtocolVersion,
    string? Detail);

public sealed record StartupMilestone(string Name, double ElapsedMilliseconds);

public sealed partial class DiagnosticsViewModel : INotifyPropertyChanged
{
    private readonly Func<PrerequisiteStatus, CancellationToken, Task> _repair;
    private WorkerHealth _worker = new(WorkerHealthState.NotReady, null, null, null);

    public DiagnosticsViewModel(
        string profile,
        PrerequisiteReport prerequisites,
        Func<PrerequisiteStatus, CancellationToken, Task>? repair = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(profile);
        Profile = profile;
        Prerequisites = prerequisites?.Items ?? throw new ArgumentNullException(nameof(prerequisites));
        _repair = repair ?? (static (_, _) => Task.CompletedTask);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Profile { get; }

    public IReadOnlyList<PrerequisiteStatus> Prerequisites { get; }

    public string AppVersion { get; } =
        typeof(DiagnosticsViewModel).Assembly.GetName().Version?.ToString() ?? "0.0.0";

    public string WorkerVersion => _worker.WorkerVersion ?? "未知";

    public ObservableCollection<StartupMilestone> Milestones { get; } = [];

    public string WorkerStatus => _worker.State switch
    {
        WorkerHealthState.NotReady => "未就绪",
        WorkerHealthState.Connecting => "正在连接",
        WorkerHealthState.Ready => "已就绪",
        WorkerHealthState.ProtocolIncompatible => "协议不兼容",
        WorkerHealthState.Faulted => "连接失败",
        _ => "未知",
    };

    public string ProtocolStatus => _worker.ProtocolVersion is int workerVersion
        ? $"主机 v{ProtocolConstants.Version} / Worker v{workerVersion}"
        : $"主机 v{ProtocolConstants.Version} / Worker 未知";

    public bool IsReady =>
        Prerequisites.All(item => item.IsInstalled) &&
        _worker.State == WorkerHealthState.Ready &&
        _worker.ProtocolVersion == ProtocolConstants.Version;

    public void UpdateWorker(WorkerHealth health)
    {
        _worker = health ?? throw new ArgumentNullException(nameof(health));
        OnPropertyChanged(nameof(WorkerStatus));
        OnPropertyChanged(nameof(WorkerVersion));
        OnPropertyChanged(nameof(ProtocolStatus));
        OnPropertyChanged(nameof(IsReady));
    }

    public void RecordMilestone(string name, TimeSpan elapsed)
    {
        if (!StartupName().IsMatch(name))
        {
            throw new ArgumentException("Milestone must be T0 through T6.", nameof(name));
        }

        StartupMilestone? existing = Milestones.SingleOrDefault(item => item.Name == name);
        if (existing is not null)
        {
            Milestones.Remove(existing);
        }

        Milestones.Add(new StartupMilestone(name, elapsed.TotalMilliseconds));
    }

    public Task RepairAsync(PrerequisiteKind kind, CancellationToken cancellationToken)
    {
        PrerequisiteStatus item = Prerequisites.Single(status => status.Kind == kind);
        if (item.IsInstalled)
        {
            throw new InvalidOperationException($"{kind} does not require repair.");
        }

        return _repair(item, cancellationToken);
    }

    public async Task ExportAsync(string destination, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(destination);
        var document = new
        {
            schema_version = 1,
            profile = Profile,
            app_version = AppVersion,
            protocol_version = ProtocolConstants.Version,
            worker = new
            {
                state = _worker.State.ToString(),
                version = _worker.WorkerVersion,
                protocol_version = _worker.ProtocolVersion,
                detail = Redact(_worker.Detail),
            },
            prerequisites = Prerequisites.Select(item => new
            {
                kind = item.Kind.ToString(),
                installed = item.IsInstalled,
                version = item.InstalledVersion,
                minimum = item.MinimumVersion,
            }),
            milestones = Milestones.OrderBy(item => item.Name),
        };
        string? directory = Path.GetDirectoryName(Path.GetFullPath(destination));
        if (directory is not null)
        {
            Directory.CreateDirectory(directory);
        }

        await using FileStream stream = File.Create(destination);
        await JsonSerializer.SerializeAsync(
            stream,
            document,
            new JsonSerializerOptions
            {
                Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
                WriteIndented = true,
            },
            cancellationToken);
    }

    private static string? Redact(string? value)
    {
        if (value is null)
        {
            return null;
        }

        string sanitized = Secret().Replace(value, "$1<redacted>");
        return WindowsPath().Replace(sanitized, "<redacted>");
    }

    private void OnPropertyChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));

    [GeneratedRegex("^T[0-6]$", RegexOptions.CultureInvariant)]
    private static partial Regex StartupName();

    [GeneratedRegex("(?i)(token\\s*[=:]\\s*)[^;\\s,\\\"]+")]
    private static partial Regex Secret();

    [GeneratedRegex("[A-Za-z]:\\\\[^;\\r\\n]+")]
    private static partial Regex WindowsPath();
}
