using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.UI.Xaml;
using VibeOCR.App.ViewModels;
using VibeOCR.Contracts;
using VibeOCR.Platform.Bootstrap;
using VibeOCR.Platform.Worker;

namespace VibeOCR.App;

public sealed partial class App : Application
{
    private readonly Stopwatch _startup = Stopwatch.StartNew();
    private MainWindow? _window;
    private Process? _workerProcess;
    private WorkerHostClient? _workerClient;

    public App()
    {
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        AppLaunchOptions options = AppLaunchOptions.Parse(Environment.GetCommandLineArgs()[1..]);
        string executable = Environment.ProcessPath ?? AppContext.BaseDirectory;
        PortableLayout layout = PortableLayout.Resolve(executable, options.Profile);
        PrerequisiteReport prerequisites = new PrerequisiteDetector().Detect(layout);
        var diagnostics = new DiagnosticsViewModel(
            options.Profile,
            prerequisites,
            static async (item, _) =>
            {
                if (Uri.TryCreate(item.RepairUri, UriKind.Absolute, out Uri? uri) && uri.Scheme != "repair")
                {
                    await Windows.System.Launcher.LaunchUriAsync(uri);
                }
            });
        diagnostics.RecordMilestone("T0", TimeSpan.Zero);
        diagnostics.RecordMilestone("T1", _startup.Elapsed);

        _window = new MainWindow(diagnostics, layout);
        _window.Closed += OnWindowClosed;
        _window.Activate();
        diagnostics.RecordMilestone("T2", _startup.Elapsed);

        _ = ConnectWorkerAfterFirstWindowAsync(layout, diagnostics);
    }

    private async Task ConnectWorkerAfterFirstWindowAsync(
        PortableLayout layout,
        DiagnosticsViewModel diagnostics)
    {
        diagnostics.UpdateWorker(new WorkerHealth(WorkerHealthState.Connecting, null, null, null));
        diagnostics.RecordMilestone("T3", _startup.Elapsed);
        if (!diagnostics.Prerequisites.All(item => item.IsInstalled))
        {
            diagnostics.UpdateWorker(new WorkerHealth(
                WorkerHealthState.NotReady,
                null,
                null,
                "One or more prerequisites require repair."));
            return;
        }

        try
        {
            string pipeName = $@"\\.\pipe\VibeOCR-{Guid.NewGuid():D}";
            string token = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(32));
            string python = Path.Combine(layout.RuntimeRoot, "python.exe");
            var startInfo = new ProcessStartInfo
            {
                FileName = python,
                WorkingDirectory = layout.InstallRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            startInfo.ArgumentList.Add("-m");
            startInfo.ArgumentList.Add("vibeocr.worker_host.main");
            startInfo.ArgumentList.Add("--pipe");
            startInfo.ArgumentList.Add(pipeName);
            startInfo.ArgumentList.Add("--token");
            startInfo.ArgumentList.Add(token);
            startInfo.ArgumentList.Add("--profile");
            startInfo.ArgumentList.Add("winui-dev");
            startInfo.ArgumentList.Add("--parent-pid");
            startInfo.ArgumentList.Add(Environment.ProcessId.ToString());
            _workerProcess = Process.Start(startInfo)
                ?? throw new InvalidOperationException("Failed to start WorkerHost.");
            _ = _workerProcess.StandardError.ReadToEndAsync();

            using var readinessTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            string? readyLine = await ReadReadyLineAsync(
                _workerProcess.StandardOutput,
                readinessTimeout.Token);
            using JsonDocument ready = JsonDocument.Parse(
                readyLine ?? throw new InvalidDataException("WorkerHost did not publish worker.ready."));
            if (ready.RootElement.GetProperty("event").GetString() != "worker.ready")
            {
                throw new InvalidDataException("WorkerHost published an invalid ready event.");
            }

            diagnostics.RecordMilestone("T4", _startup.Elapsed);
            _workerClient = await WorkerHostClient.ConnectAsync(
                pipeName,
                token,
                TimeSpan.FromSeconds(10),
                TimeSpan.FromSeconds(30),
                CancellationToken.None);
            HandshakeResponse handshake = await _workerClient.CallAsync<HandshakeRequest, HandshakeResponse>(
                RpcMethods.Handshake,
                new HandshakeRequest
                {
                    AppVersion = typeof(App).Assembly.GetName().Version?.ToString() ?? "0.0.0",
                    ProtocolVersion = ProtocolConstants.Version,
                    MaxMessageBytes = FrameCodec.DefaultMaxFrameBytes,
                    MaxSharedPayloadBytes = 256L << 20,
                },
                CancellationToken.None);
            diagnostics.RecordMilestone("T5", _startup.Elapsed);
            diagnostics.UpdateWorker(new WorkerHealth(
                handshake.ProtocolVersion == ProtocolConstants.Version
                    ? WorkerHealthState.Ready
                    : WorkerHealthState.ProtocolIncompatible,
                handshake.WorkerVersion,
                handshake.ProtocolVersion,
                null));
            diagnostics.RecordMilestone("T6", _startup.Elapsed);
        }
        catch (Exception error)
        {
            diagnostics.UpdateWorker(new WorkerHealth(
                WorkerHealthState.Faulted,
                null,
                null,
                error.Message));
        }
    }

    private static async Task<string?> ReadReadyLineAsync(
        StreamReader reader,
        CancellationToken cancellationToken)
    {
        for (int attempt = 0; attempt < 20; attempt++)
        {
            string? line = await reader.ReadLineAsync(cancellationToken);
            if (line is null || line.TrimStart().StartsWith('{'))
            {
                return line;
            }
        }

        return null;
    }

    private async void OnWindowClosed(object sender, WindowEventArgs args)
    {
        if (_workerClient is not null)
        {
            await _workerClient.DisposeAsync();
        }

        if (_workerProcess is { HasExited: false })
        {
            _workerProcess.Kill(entireProcessTree: true);
        }

        _workerProcess?.Dispose();
    }
}

public sealed record AppLaunchOptions(string Profile)
{
    public static AppLaunchOptions Parse(IReadOnlyList<string> args)
    {
        // Phase 2–4 is deliberately side-by-side. Production is enabled only by the cutover task.
        return new AppLaunchOptions("winui-dev");
    }
}

public static class ShellNavigation
{
    public static IReadOnlyList<string> Destinations { get; } = ["home", "diagnostics"];
}
