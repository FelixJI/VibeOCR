using System.ComponentModel;
using System.Collections.ObjectModel;
using System.Runtime.CompilerServices;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.RegularExpressions;
using VibeOCR.Platform.Bootstrap;

namespace VibeOCR.App.ViewModels;

public enum SupervisorHealthState
{
    NotReady,
    Connecting,
    Ready,
    ProtocolIncompatible,
    Faulted,
}

public sealed record SupervisorHealth(
    SupervisorHealthState State,
    string? InstanceId,
    int? ProtocolVersion,
    string? Detail);

public sealed record StartupMilestone(string Name, double ElapsedMilliseconds);

public sealed partial class DiagnosticsViewModel : INotifyPropertyChanged
{
    private readonly Func<PrerequisiteStatus, CancellationToken, Task> _repair;
    private SupervisorHealth _supervisor = new(SupervisorHealthState.NotReady, null, null, null);

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

    public string SupervisorInstanceId => _supervisor.InstanceId ?? "未知";

    public ObservableCollection<StartupMilestone> Milestones { get; } = [];

    public string SupervisorStatus => _supervisor.State switch
    {
        SupervisorHealthState.NotReady => "未就绪",
        SupervisorHealthState.Connecting => "正在连接",
        SupervisorHealthState.Ready => "已就绪",
        SupervisorHealthState.ProtocolIncompatible => "协议不兼容",
        SupervisorHealthState.Faulted => "连接失败",
        _ => "未知",
    };

    public string ProtocolStatus => _supervisor.ProtocolVersion is int supervisorVersion
        ? $"客户端 v{ProtocolConstants.Version} / Supervisor v{supervisorVersion}"
        : $"客户端 v{ProtocolConstants.Version} / Supervisor 未知";

    public bool IsReady =>
        Prerequisites.All(item => item.IsInstalled) &&
        _supervisor.State == SupervisorHealthState.Ready &&
        _supervisor.ProtocolVersion == ProtocolConstants.Version;

    public void UpdateSupervisor(SupervisorHealth health)
    {
        _supervisor = health ?? throw new ArgumentNullException(nameof(health));
        OnPropertyChanged(nameof(SupervisorStatus));
        OnPropertyChanged(nameof(SupervisorInstanceId));
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
            schema_version = 2,
            profile = Profile,
            app_version = AppVersion,
            protocol_version = ProtocolConstants.Version,
            supervisor = new
            {
                state = _supervisor.State.ToString(),
                instance_id = _supervisor.InstanceId,
                protocol_version = _supervisor.ProtocolVersion,
                detail = Redact(_supervisor.Detail),
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
