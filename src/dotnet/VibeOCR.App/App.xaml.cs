using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.Features.Batch;
using VibeOCR.App.Features.QrCode;
using VibeOCR.App.ViewModels;
using VibeOCR.App.Views;
using VibeOCR.Contracts;
using VibeOCR.Platform.Bootstrap;
using VibeOCR.Platform.Worker;

namespace VibeOCR.App;

public sealed partial class App : Application
{
    private readonly Stopwatch _startup = Stopwatch.StartNew();
    private readonly DeferredWorkerHostClient _workerGateway = new();
    private readonly SemaphoreSlim _workerLifecycle = new(1, 1);
    private readonly CancellationTokenSource _applicationShutdown = new();
    private MainWindow? _window;
    private Process? _workerProcess;
    private WorkerHostClient? _workerClient;
    private bool _shutdownStarted;

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

        _window = new MainWindow(
            diagnostics,
            layout,
            () => new RecognitionViewModel(
                _workerGateway,
                new InputService(() => WinRT.Interop.WindowNative.GetWindowHandle(_window!))),
            () => new BatchViewModel(
                _workerGateway,
                new BatchFileSource(() => WinRT.Interop.WindowNative.GetWindowHandle(_window!))),
            () =>
            {
                nint handle = WinRT.Interop.WindowNative.GetWindowHandle(_window!);
                var qrViewModel = new QrCodeViewModel(
                    _workerGateway,
                    new QrCodeInputService(() => handle));
                return new QrCodePage(
                    qrViewModel,
                    new QrCodeSaveCommands(_workerGateway, new QrCodeSavePlatform(() => handle)));
            });
        _window.AppWindow.Closing += OnAppWindowClosing;
        _window.Closed += OnWindowClosedFallback;
        _window.Activate();
        diagnostics.RecordMilestone("T2", _startup.Elapsed);

        _workerGateway.ConfigureRecovery(
            cancellationToken => RestartWorkerAsync(layout, diagnostics, cancellationToken));
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

        await _workerLifecycle.WaitAsync();
        try
        {
            try
            {
                (Process process, WorkerHostClient client, HandshakeResponse handshake) =
                    await StartWorkerAsync(layout, _applicationShutdown.Token);
                _workerProcess = process;
                _workerClient = client;
                diagnostics.RecordMilestone("T4", _startup.Elapsed);
                diagnostics.RecordMilestone("T5", _startup.Elapsed);
                _workerGateway.Attach(client);
                diagnostics.UpdateWorker(ReadyHealth(handshake));
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
        finally
        {
            _workerLifecycle.Release();
        }
    }

    private async Task<IWorkerHostClient> RestartWorkerAsync(
        PortableLayout layout,
        DiagnosticsViewModel diagnostics,
        CancellationToken cancellationToken)
    {
        await _workerLifecycle.WaitAsync(cancellationToken);
        try
        {
            diagnostics.UpdateWorker(new WorkerHealth(
                WorkerHealthState.Connecting,
                null,
                null,
                "WorkerHost exited; attempting one restart."));
            await StopWorkerAsync();
            (Process process, WorkerHostClient client, HandshakeResponse handshake) =
                await StartWorkerAsync(layout, cancellationToken);
            _workerProcess = process;
            _workerClient = client;
            diagnostics.UpdateWorker(ReadyHealth(handshake));
            return client;
        }
        catch (Exception error)
        {
            diagnostics.UpdateWorker(new WorkerHealth(
                WorkerHealthState.Faulted,
                null,
                null,
                error.Message));
            throw;
        }
        finally
        {
            _workerLifecycle.Release();
        }
    }

    private static WorkerHealth ReadyHealth(HandshakeResponse handshake) => new(
        WorkerHealthState.Ready,
        handshake.WorkerVersion,
        handshake.ProtocolVersion,
        null);

    private static async Task<(Process Process, WorkerHostClient Client, HandshakeResponse Handshake)>
        StartWorkerAsync(PortableLayout layout, CancellationToken cancellationToken)
    {
        Process? process = null;
        WorkerHostClient? client = null;
        try
        {
            string pipeName = $@"\\.\pipe\VibeOCR-{Guid.NewGuid():D}";
            string token = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(32));
            var startInfo = new ProcessStartInfo
            {
                FileName = Path.Combine(layout.RuntimeRoot, "python.exe"),
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
            process = Process.Start(startInfo)
                ?? throw new InvalidOperationException("Failed to start WorkerHost.");
            _ = process.StandardError.ReadToEndAsync(cancellationToken);

            using var readinessTimeout = CancellationTokenSource.CreateLinkedTokenSource(
                cancellationToken);
            readinessTimeout.CancelAfter(TimeSpan.FromSeconds(10));
            string? readyLine = await ReadReadyLineAsync(
                process.StandardOutput,
                readinessTimeout.Token);
            using JsonDocument ready = JsonDocument.Parse(
                readyLine ?? throw new InvalidDataException("WorkerHost did not publish worker.ready."));
            if (ready.RootElement.GetProperty("event").GetString() != "worker.ready")
            {
                throw new InvalidDataException("WorkerHost published an invalid ready event.");
            }

            client = await WorkerHostClient.ConnectAsync(
                pipeName,
                token,
                TimeSpan.FromSeconds(10),
                TimeSpan.FromSeconds(30),
                cancellationToken);
            HandshakeResponse handshake = await client.CallAsync<HandshakeRequest, HandshakeResponse>(
                RpcMethods.Handshake,
                new HandshakeRequest
                {
                    AppVersion = typeof(App).Assembly.GetName().Version?.ToString() ?? "0.0.0",
                    ProtocolVersion = ProtocolConstants.Version,
                    MaxMessageBytes = FrameCodec.DefaultMaxFrameBytes,
                    MaxSharedPayloadBytes = 256L << 20,
                },
                cancellationToken);
            if (handshake.ProtocolVersion != ProtocolConstants.Version)
            {
                throw new ProtocolContractException(
                    $"Worker protocol v{handshake.ProtocolVersion} is incompatible.");
            }

            return (process, client, handshake);
        }
        catch
        {
            if (client is not null)
            {
                await client.DisposeAsync();
            }

            if (process is { HasExited: false })
            {
                process.Kill(entireProcessTree: true);
            }

            process?.Dispose();
            throw;
        }
    }

    private async Task StopWorkerAsync()
    {
        WorkerHostClient? client = Interlocked.Exchange(ref _workerClient, null);
        if (client is not null)
        {
            _workerGateway.Detach(client);
            await client.DisposeAsync();
        }

        Process? process = Interlocked.Exchange(ref _workerProcess, null);
        if (process is { HasExited: false })
        {
            process.Kill(entireProcessTree: true);
        }

        process?.Dispose();
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

    private void OnAppWindowClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (_shutdownStarted)
        {
            return;
        }

        args.Cancel = true;
        _shutdownStarted = true;
        _applicationShutdown.Cancel();
        _ = ShutdownAndExitAsync(sender);
    }

    private async Task ShutdownAndExitAsync(AppWindow appWindow)
    {
        await _workerLifecycle.WaitAsync();
        try
        {
            await StopWorkerAsync();
            await _workerGateway.DisposeAsync();
        }
        finally
        {
            _workerLifecycle.Release();
            _applicationShutdown.Dispose();
            appWindow.Closing -= OnAppWindowClosing;
            _window?.Close();
            Exit();
        }
    }

    private void OnWindowClosedFallback(object sender, WindowEventArgs args)
    {
        if (!_shutdownStarted)
        {
            _shutdownStarted = true;
            _applicationShutdown.Cancel();
        }

        Environment.Exit(0);
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
    public static IReadOnlyList<string> Destinations { get; } =
        ["home", "recognition", "batch", "qrcode", "diagnostics"];
}
