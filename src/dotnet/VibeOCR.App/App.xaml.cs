using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.Features.Batch;
using VibeOCR.App.Features.Pdf;
using VibeOCR.App.Features.QrCode;
using VibeOCR.App.Features.Settings;
using VibeOCR.App.Features.Shell;
using VibeOCR.App.Features.Update;
using VibeOCR.App.Services;
using VibeOCR.App.ViewModels;
using VibeOCR.App.Views;
using VibeOCR.Contracts;
using VibeOCR.Platform.Bootstrap;
using VibeOCR.Platform.Migration;
using VibeOCR.Platform.Update;
using VibeOCR.Platform.Worker;
using VibeOCR.Platform.Windows;

namespace VibeOCR.App;

public sealed partial class App : Application
{
    private readonly Stopwatch _startup = Stopwatch.StartNew();
    private readonly DeferredWorkerHostClient _workerGateway = new();
    private readonly SemaphoreSlim _workerLifecycle = new(1, 1);
    private readonly CancellationTokenSource _applicationShutdown = new();
    private readonly Dictionary<string, double> _startupMilestones = [];
    private MainWindow? _window;
    private WindowLayoutStore? _windowLayoutStore;
    private Process? _workerProcess;
    private WorkerHostClient? _workerClient;
    private SingleInstanceService? _singleInstance;
    private FrontendExclusiveLock? _exclusiveLock;
    private WindowMessageService? _windowMessages;
    private TrayIconService? _trayIcon;
    private WindowsHotkeyRegistrar? _hotkeyRegistrar;
    private ShellViewModel? _shellViewModel;
    private UpdateViewModel? _updateViewModel;
    private string? _startupHealthFile;
    private bool _shutdownStarted;

    private const uint HotkeyMessage = 0x0312;
    private const uint TrayMessage = 0x8001;

    public App()
    {
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        AppLaunchOptions options = AppLaunchOptions.Parse(Environment.GetCommandLineArgs()[1..]);
        _startupHealthFile = options.HealthFile;
        _singleInstance = new SingleInstanceService(
            $"VibeOCR-{options.Profile}",
            _ =>
            {
                _window?.DispatcherQueue.TryEnqueue(ShowMainWindow);
                return Task.CompletedTask;
            });
        if (!_singleInstance.IsPrimary)
        {
            _ = ForwardActivationAndExitAsync(Environment.GetCommandLineArgs()[1..]);
            return;
        }

        // 跨产品互斥：同一登录会话内 PySide Classic 与 WinUI Next 不同时运行。
        // 在同产品单实例通过后、WorkerHost 启动前获取；失败时提示退出，不启动
        // 第二个 WorkerHost。Mutex 由 OS 在前端崩溃时自动释放（ADR §6）。
        _exclusiveLock = new FrontendExclusiveLock();
        if (!_exclusiveLock.IsAcquired)
        {
            FrontendExclusiveLock.ShowAnotherProductRunningPrompt();
            Exit();
            return;
        }
        string executable = Environment.ProcessPath ?? AppContext.BaseDirectory;
        PortableLayout layout = PortableLayout.Resolve(executable, options.Profile);
        if (layout.Profile == "production" && File.Exists(layout.ConfigFile))
        {
            MigrationResult migration = ProfileMigrationClient.MigrateConfig(layout.ConfigFile);
            if (migration.Status == "skipped")
            {
                throw new InvalidDataException(
                    $"Production profile migration failed: {migration.Message}");
            }
        }
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
        RecordMilestone(diagnostics, "T0", TimeSpan.Zero);
        RecordMilestone(diagnostics, "T1", _startup.Elapsed);

        _windowLayoutStore = new WindowLayoutStore(
            Path.Combine(layout.DataRoot, "winui-layout.json"));

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
            },
            () =>
            {
                nint handle = WinRT.Interop.WindowNative.GetWindowHandle(_window!);
                return new PdfPage(
                    new PdfViewModel(_workerGateway, new PdfFileSource(() => handle)));
            },
            () => new SettingsPage(new SettingsViewModel(_workerGateway), _shellViewModel!),
            () =>
            {
                return new AboutPage(
                    _shellViewModel ?? throw new InvalidOperationException("Desktop shell is unavailable."),
                    _updateViewModel ?? throw new InvalidOperationException("Update service is unavailable."));
            },
            _windowLayoutStore);
        _window.AppWindow.Closing += OnAppWindowClosing;
        _window.Closed += OnWindowClosedFallback;
        _window.Activate();
        InitializeDesktopShell(layout);
        RecordMilestone(diagnostics, "T2", _startup.Elapsed);

        _workerGateway.ConfigureRecovery(
            cancellationToken => RestartWorkerAsync(layout, diagnostics, cancellationToken));
        _ = ConnectWorkerAfterFirstWindowAsync(layout, diagnostics);

        // Perf-gate smoke mode: exit shortly after first window so cold-start
        // timing can be measured without the worker handshake. Production runs
        // never set this env var.
        if (Environment.GetEnvironmentVariable("VIBEOCR_SELF_TEST_SMOKE") is "1" or "t3")
        {
            _ = SmokeExitAsync();
        }
    }

    private async Task ForwardActivationAndExitAsync(IReadOnlyList<string> arguments)
    {
        SingleInstanceService instance = _singleInstance
            ?? throw new InvalidOperationException("Single-instance service is unavailable.");
        try
        {
            await instance.ForwardAsync(arguments, CancellationToken.None);
        }
        finally
        {
            await instance.DisposeAsync();
            _singleInstance = null;
            Exit();
        }
    }

    private void InitializeDesktopShell(PortableLayout layout)
    {
        nint handle = WinRT.Interop.WindowNative.GetWindowHandle(_window!);
        _windowMessages = new WindowMessageService(handle);
        _windowMessages.MessageReceived += OnWindowMessage;
        _trayIcon = new TrayIconService();
        _trayIcon.Show(handle, TrayMessage, "VibeOCR");

        string hotkey = ReadConfiguredHotkey(layout.ConfigFile) ?? "Ctrl+Alt+Q";
        _hotkeyRegistrar = new WindowsHotkeyRegistrar(
            new GlobalHotkeyService(windowHandle: handle),
            layout.ConfigFile);
        _hotkeyRegistrar.Register(hotkey, out _);
        _shellViewModel = new ShellViewModel(
            _hotkeyRegistrar,
            new WindowsStartupRegistrar(Path.Combine(layout.InstallRoot, "VibeOCR.Bootstrapper.exe")),
            () => _window!.AppWindow.Hide(),
            () => _window!.Close(),
            hotkey);
        _updateViewModel = new UpdateViewModel(
            new GitHubUpdateSource(
                typeof(App).Assembly.GetName().Version?.ToString(3) ?? "0.0.0",
                layout.InstallRoot,
                Path.Combine(layout.DataRoot, "cache", "update")),
            () => _window!.Close());
    }

    private static string? ReadConfiguredHotkey(string configFile)
    {
        if (!File.Exists(configFile))
        {
            return null;
        }
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(configFile));
            return document.RootElement
                .GetProperty("hotkeys")
                .GetProperty("global_screenshot")
                .GetString();
        }
        catch (Exception error) when (error is JsonException or KeyNotFoundException)
        {
            return null;
        }
    }

    private void OnWindowMessage(object? sender, WindowMessage message)
    {
        if (message.Id == HotkeyMessage)
        {
            _ = RecognizeFromHotkeyAsync();
            return;
        }
        if (message.Id == TrayMessage && (uint)message.LParam is 0x0202 or 0x0203 or 0x0205)
        {
            ShowMainWindow();
        }
    }

    private async Task RecognizeFromHotkeyAsync()
    {
        ShowMainWindow();
        try
        {
            await _window!.RecognizeScreenshotAsync();
        }
        catch (Exception error) when (
            error is InvalidOperationException or IOException or UnauthorizedAccessException)
        {
            // RecognitionViewModel owns localized status; activation must keep the shell alive.
        }
    }

    private void ShowMainWindow()
    {
        _window?.AppWindow.Show();
        _window?.Activate();
    }

    private async Task SmokeExitAsync()
    {
        await Task.Delay(150);  // allow first-window render
        FlushStartupTrace();
        Environment.Exit(0);
    }

    private async Task ConnectWorkerAfterFirstWindowAsync(
        PortableLayout layout,
        DiagnosticsViewModel diagnostics)
    {
        bool crashRequested =
            Environment.GetEnvironmentVariable("VIBEOCR_SOAK_INJECT_CRASH") == "1";
        bool recoverySucceeded = !crashRequested;
        diagnostics.UpdateWorker(new WorkerHealth(WorkerHealthState.Connecting, null, null, null));
        RecordMilestone(diagnostics, "T3", _startup.Elapsed);
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
                RecordMilestone(diagnostics, "T4", _startup.Elapsed);
                RecordMilestone(diagnostics, "T5", _startup.Elapsed);
                _workerGateway.Attach(client);
                diagnostics.UpdateWorker(ReadyHealth(handshake));
                RecordMilestone(diagnostics, "T6", _startup.Elapsed);
                WriteHealthSignal();
                _ = UpdateArtifactCleaner.CleanupAsync(
                    layout.InstallRoot,
                    layout.DataRoot,
                    TimeSpan.FromSeconds(3));
                if (crashRequested)
                {
                    await ExerciseInjectedCrashAsync(layout, diagnostics);
                    recoverySucceeded = true;
                }
                WriteSoakResult(crashRequested, recoverySucceeded);
                if (Environment.GetEnvironmentVariable("VIBEOCR_SELF_TEST_SMOKE") == "t6")
                {
                    FlushStartupTrace();
                    Environment.Exit(0);
                }
            }
            catch (Exception error)
            {
                WriteSoakResult(crashRequested, recovered: false, error: error.Message);
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

    private async Task ExerciseInjectedCrashAsync(
        PortableLayout layout,
        DiagnosticsViewModel diagnostics)
    {
        Process process = _workerProcess
            ?? throw new InvalidOperationException("WorkerHost is unavailable for crash injection.");
        process.Kill(entireProcessTree: true);
        await process.WaitForExitAsync(_applicationShutdown.Token);
        await StopWorkerAsync();
        (Process replacement, WorkerHostClient client, HandshakeResponse handshake) =
            await StartWorkerAsync(layout, _applicationShutdown.Token);
        _workerProcess = replacement;
        _workerClient = client;
        _workerGateway.Attach(client);
        diagnostics.UpdateWorker(ReadyHealth(handshake));
    }

    private static void WriteSoakResult(bool requested, bool recovered, string? error = null)
    {
        string? resultPath = Environment.GetEnvironmentVariable("VIBEOCR_SOAK_RESULT");
        if (string.IsNullOrWhiteSpace(resultPath))
        {
            return;
        }

        string fullPath = Path.GetFullPath(resultPath);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        File.WriteAllText(
            fullPath,
            JsonSerializer.Serialize(new
            {
                crash_requested = requested,
                recovered,
                error,
            }));
    }

    private void WriteHealthSignal()
    {
        if (string.IsNullOrWhiteSpace(_startupHealthFile))
        {
            return;
        }
        string fullPath = Path.GetFullPath(_startupHealthFile);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        File.WriteAllText(
            fullPath,
            JsonSerializer.Serialize(new
            {
                status = "healthy",
                pid = Environment.ProcessId,
                timestamp = DateTimeOffset.UtcNow,
            }));
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

    private void RecordMilestone(
        DiagnosticsViewModel diagnostics,
        string name,
        TimeSpan elapsed)
    {
        diagnostics.RecordMilestone(name, elapsed);
        _startupMilestones.TryAdd(name, elapsed.TotalSeconds);
    }

    private void FlushStartupTrace()
    {
        string? tracePath = Environment.GetEnvironmentVariable("VIBEOCR_STARTUP_TRACE");
        if (string.IsNullOrWhiteSpace(tracePath))
        {
            return;
        }

        string fullPath = Path.GetFullPath(tracePath);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        File.AppendAllText(
            fullPath,
            JsonSerializer.Serialize(_startupMilestones) + Environment.NewLine);
    }

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
                FileName = PortableLayout.ResolvePythonExecutable(layout),
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
            startInfo.ArgumentList.Add(layout.Profile);
            startInfo.ArgumentList.Add("--parent-pid");
            startInfo.ArgumentList.Add(Environment.ProcessId.ToString());
            string workerRoot = ResolveWorkerRoot(layout);
            string? existingPythonPath = startInfo.Environment["PYTHONPATH"];
            startInfo.Environment["PYTHONPATH"] = string.IsNullOrWhiteSpace(existingPythonPath)
                ? workerRoot
                : workerRoot + Path.PathSeparator + existingPythonPath;
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

    public static string ResolveWorkerRoot(PortableLayout layout)
    {
        string packaged = Path.Combine(layout.InstallRoot, "worker");
        if (Directory.Exists(Path.Combine(packaged, "vibeocr", "worker_host")))
        {
            return packaged;
        }

        if (layout.Profile == "winui-dev")
        {
            string? repository = Environment.GetEnvironmentVariable("VIBEOCR_REPOSITORY_ROOT");
            if (!string.IsNullOrWhiteSpace(repository))
            {
                string source = Path.Combine(repository, "src");
                if (Directory.Exists(Path.Combine(source, "vibeocr", "worker_host")))
                {
                    return source;
                }
            }
        }

        throw new DirectoryNotFoundException(
            $"WorkerHost package is missing under {packaged}.");
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
            if (_window is not null && _windowLayoutStore is not null && _window.CaptureGeometry() is { } geometry)
            {
                _windowLayoutStore.Save(geometry);
            }
            await StopWorkerAsync();
            await _workerGateway.DisposeAsync();
            await DisposeDesktopShellAsync();
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

    private async Task DisposeDesktopShellAsync()
    {
        _hotkeyRegistrar?.Dispose();
        _hotkeyRegistrar = null;
        _trayIcon?.Dispose();
        _trayIcon = null;
        if (_windowMessages is not null)
        {
            _windowMessages.MessageReceived -= OnWindowMessage;
            _windowMessages.Dispose();
            _windowMessages = null;
        }
        if (_singleInstance is not null)
        {
            await _singleInstance.DisposeAsync();
            _singleInstance = null;
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

public sealed record AppLaunchOptions(string Profile, string? HealthFile)
{
    public static AppLaunchOptions Parse(IReadOnlyList<string> args)
    {
        string profile = "production";
        string? healthFile = null;
        for (int index = 0; index < args.Count; index++)
        {
            if (string.Equals(args[index], "--profile", StringComparison.Ordinal))
            {
                if (index + 1 >= args.Count)
                {
                    throw new ArgumentException("--profile requires a value.", nameof(args));
                }
                profile = args[++index];
            }
            else if (string.Equals(args[index], "--health-file", StringComparison.Ordinal))
            {
                if (index + 1 >= args.Count)
                {
                    throw new ArgumentException("--health-file requires a value.", nameof(args));
                }
                healthFile = Path.GetFullPath(args[++index]);
            }
        }

        if (profile is not ("production" or "winui-dev"))
        {
            throw new ArgumentException($"Unsupported profile: {profile}.", nameof(args));
        }

        return new AppLaunchOptions(profile, healthFile);
    }
}

public static class ShellNavigation
{
    public static IReadOnlyList<string> Destinations { get; } =
        ["home", "recognition", "batch", "qrcode", "pdf", "settings", "about", "diagnostics"];
}
