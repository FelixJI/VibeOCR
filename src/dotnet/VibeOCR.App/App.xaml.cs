using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using VibeOCR.App.Features.Recognition;
using VibeOCR.App.Features.Batch;
using VibeOCR.App.Features.Pdf;
using VibeOCR.App.Features.QrCode;
using VibeOCR.App.Features.Settings;
using VibeOCR.App.Inference;
using VibeOCR.App.Features.Shell;
using VibeOCR.App.Features.Update;
using VibeOCR.App.Services;
using VibeOCR.App.ViewModels;
using VibeOCR.App.Views;
using VibeOCR.Platform.Bootstrap;
using VibeOCR.Platform.Migration;
using VibeOCR.Platform.Update;
using VibeOCR.Platform.Inference;
using VibeOCR.Platform.Windows;

namespace VibeOCR.App;

public sealed partial class App : Application
{
    private readonly Stopwatch _startup = Stopwatch.StartNew();
    /// <summary>
    /// v2 supervisor client (deferred until the supervisor process is started).
    /// Attached after the Supervisor process reports a v2 ready envelope.
    /// </summary>
    private readonly DeferredInferenceClient _inferenceGateway = new();
    private readonly DeferredQrCodeClient _qrCodeGateway = new();
    private readonly SemaphoreSlim _supervisorLifecycle = new(1, 1);
    private readonly CancellationTokenSource _applicationShutdown = new();
    private readonly Dictionary<string, double> _startupMilestones = [];
    private MainWindow? _window;
    private WindowLayoutStore? _windowLayoutStore;
    private SingleInstanceService? _singleInstance;
    private InferenceSupervisorProcess? _supervisorProcess;
    private IInferenceClient? _activeInferenceClient;
    private IQrCodeClient? _activeQrCodeClient;
    private PortableLayout? _supervisorLayout;
    private DiagnosticsViewModel? _supervisorDiagnostics;
    private IRuntimeInstallerClient? _runtimeInstaller;
    private FrontendExclusiveLock? _exclusiveLock;
    private WindowMessageService? _windowMessages;
    private TrayIconService? _trayIcon;
    private WindowsHotkeyRegistrar? _hotkeyRegistrar;
    private ShellViewModel? _shellViewModel;
    private UpdateViewModel? _updateViewModel;
    private string? _startupHealthFile;
    private bool _shutdownStarted;
    private bool _soakCrashRequested;
    private bool _soakCrashInjected;
    private int _supervisorRecoveryScheduled;

    private const uint HotkeyMessage = 0x0312;
    private const uint TrayMessage = 0x8001;

    public App()
    {
        UnhandledException += OnUnhandledException;
        InitializeComponent();
    }

    private void OnUnhandledException(
        object sender,
        Microsoft.UI.Xaml.UnhandledExceptionEventArgs args)
    {
        AppLog.Error("Unhandled WinUI exception", args.Exception);
        WriteSoakResult(
            Environment.GetEnvironmentVariable("VIBEOCR_SOAK_INJECT_CRASH") == "1",
            recovered: false,
            args.Exception.ToString());
        FlushStartupTrace();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        AppLaunchOptions options = AppLaunchOptions.Parse(Environment.GetCommandLineArgs()[1..]);
        _startupHealthFile = options.HealthFile;
        _singleInstance = new SingleInstanceService(
            $"VibeOCR-{options.Profile}",
            arguments =>
            {
                _window?.DispatcherQueue.TryEnqueue(() =>
                {
                    // Parse the forwarded arguments so a secondary launch can
                    // carry intent (e.g. --goto pdf). Unknown flags are ignored
                    // by Parse, so a plain re-activation still just shows the
                    // window on its current tab.
                    string? destination = null;
                    try
                    {
                        destination = AppLaunchOptions.Parse(arguments).Goto;
                    }
                    catch (ArgumentException error)
                    {
                        AppLog.Warn($"Forwarded activation had invalid arguments: {error.Message}");
                    }
                    _window!.ShowAndNavigate(destination);
                });
                return Task.CompletedTask;
            });
        if (!_singleInstance.IsPrimary)
        {
            _ = ForwardActivationAndExitAsync(Environment.GetCommandLineArgs()[1..]);
            return;
        }

        // 跨产品互斥：同一登录会话内 PySide Classic 与 WinUI Next 不同时运行。
        // 在同产品单实例通过后、Supervisor 启动前获取；失败时提示退出，不启动
        // 第二个 Supervisor。Mutex 由 OS 在前端崩溃时自动释放（ADR §6）。
        _exclusiveLock = new FrontendExclusiveLock();
        if (!_exclusiveLock.IsAcquired)
        {
            FrontendExclusiveLock.ShowAnotherProductRunningPrompt();
            Exit();
            return;
        }
        string executable = Environment.ProcessPath ?? AppContext.BaseDirectory;
        PortableLayout layout = PortableLayout.Resolve(
            executable,
            options.Profile,
            Environment.GetEnvironmentVariable("VIBEOCR_PORTABLE_LAYOUT"));
        _runtimeInstaller = new RuntimeInstallerClient(
            RuntimeInstallerConfiguration.ForNext(layout));
        AppLog.Initialize(Path.Combine(layout.DataRoot, "logs"));
        AppLog.Info($"OnLaunched: profile={options.Profile} shellOnly={options.ShellOnly}");
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
        _supervisorLayout = layout;
        _supervisorDiagnostics = diagnostics;
        _soakCrashRequested =
            Environment.GetEnvironmentVariable("VIBEOCR_SOAK_INJECT_CRASH") == "1";
        RecordMilestone(diagnostics, "T0", TimeSpan.Zero);
        RecordMilestone(diagnostics, "T1", _startup.Elapsed);

        _windowLayoutStore = new WindowLayoutStore(
            Path.Combine(layout.DataRoot, "winui-layout.json"));

        _window = new MainWindow(
            diagnostics,
            layout,
            () => new RecognitionViewModel(
                _inferenceGateway,
                new InputService(() => WinRT.Interop.WindowNative.GetWindowHandle(_window!))),
            () => new BatchViewModel(
                _inferenceGateway,
                new BatchFileSource(() => WinRT.Interop.WindowNative.GetWindowHandle(_window!))),
            () =>
            {
                nint handle = WinRT.Interop.WindowNative.GetWindowHandle(_window!);
                var qrViewModel = new QrCodeViewModel(
                    _qrCodeGateway,
                    new QrCodeInputService(() => handle));
                return new QrCodePage(
                    qrViewModel,
                    new QrCodeSaveCommands(new QrCodeSavePlatform(() => handle)));
            },
            () =>
            {
                nint handle = WinRT.Interop.WindowNative.GetWindowHandle(_window!);
                return new PdfPage(
                    new PdfViewModel(_inferenceGateway, new PdfFileSource(() => handle)));
            },
            () => new SettingsPage(new SettingsViewModel(_inferenceGateway), _shellViewModel!),
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

        // --shell-only: run the UI shell without launching the Supervisor. Useful
        // for inspecting layout / XAML without paying the dev cold-import cost.
        // Without args the default is to bring the backend up automatically.
        if (options.ShellOnly)
        {
            diagnostics.UpdateSupervisor(new SupervisorHealth(
                SupervisorHealthState.NotReady,
                null,
                null,
                "外壳模式：未拉起后端（--shell-only）。"));
            RecordMilestone(diagnostics, "T6", _startup.Elapsed);
        }
        else
        {
            // Phase 8 atomic switch: start the v2 inference supervisor after the
            // first window is up. This spawns the supervisor subprocess, reads
            // the ready envelope, and Attach()es the real InferenceHttpClient /
            // QrCodeHttpClient into the deferred gateways so every ViewModel's
            // v2 calls stop throwing. Fire-and-forget: the window is already
            // interactive; the diagnostics panel reflects Connecting → Ready.
            _ = ConnectSupervisorAfterFirstWindowAsync(layout, diagnostics);
        }

        // Perf-gate smoke mode: exit shortly after first window so cold-start
        // timing can be measured without the supervisor handshake. Production runs
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
                typeof(App).Assembly
                    .GetCustomAttributes(
                        typeof(System.Reflection.AssemblyInformationalVersionAttribute),
                        inherit: false)
                    .OfType<System.Reflection.AssemblyInformationalVersionAttribute>()
                    .FirstOrDefault()?.InformationalVersion.Split('+', 2)[0]
                    ?? typeof(App).Assembly.GetName().Version?.ToString(3)
                    ?? "0.0.0",
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
        // Do NOT ShowMainWindow up front: ScreenRegionPicker hides the owner
        // window itself before capturing the desktop, and showing it here would
        // cause a visible flash (window appears, then gets hidden by the picker).
        // We activate the window after the screenshot flow finishes instead.
        try
        {
            await _window!.RecognizeScreenshotAsync();
        }
        catch (Exception error) when (
            error is InvalidOperationException or IOException or UnauthorizedAccessException)
        {
            // RecognitionViewModel owns localized status; activation must keep the shell alive.
        }
        finally
        {
            ShowMainWindow();
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

    private async Task<bool> ConnectSupervisorAfterFirstWindowAsync(
        PortableLayout layout,
        DiagnosticsViewModel diagnostics,
        bool isRecovery = false)
    {
        diagnostics.UpdateSupervisor(new SupervisorHealth(
            SupervisorHealthState.Connecting, null, null, null));
        RecordMilestone(diagnostics, "T3", _startup.Elapsed);

        await _supervisorLifecycle.WaitAsync();
        try
        {
            IRuntimeInstallerClient installer = _runtimeInstaller
                ?? throw new InvalidOperationException("Runtime Installer is unavailable.");
            RuntimeLaunch launch = await installer.EnsureAsync(
                _applicationShutdown.Token);
            string logPath = Path.Combine(layout.DataRoot, "supervisor.log");
            string token = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(32));
            bool injectSoakCrash =
                _soakCrashRequested && !_soakCrashInjected && !isRecovery;
            if (injectSoakCrash)
            {
                _soakCrashInjected = true;
            }
            InferenceSupervisorOptions options = BuildSupervisorOptions(
                launch,
                logPath,
                TimeSpan.FromSeconds(layout.Profile == "winui-dev" ? 90 : 15),
                injectSoakCrash);

            // Start the supervisor process.
            var process = new InferenceSupervisorProcess(options, token);
            process.UnexpectedExit += OnSupervisorUnexpectedExit;
            _supervisorProcess = process;
            SupervisorReadyEnvelope ready = await process.StartAsync(_applicationShutdown.Token);

            RecordMilestone(diagnostics, "T4", _startup.Elapsed);
            RecordMilestone(diagnostics, "T5", _startup.Elapsed);

            // Construct v2 clients and attach to the deferred gateways.
            Uri baseUrl = ready.BaseUrl;
            var inferenceClient = new InferenceHttpClient(baseUrl, token);
            var qrClient = new QrCodeHttpClient(baseUrl, token);
            _inferenceGateway.Attach(inferenceClient);
            _qrCodeGateway.Attach(qrClient);
            _activeInferenceClient = inferenceClient;
            _activeQrCodeClient = qrClient;

            diagnostics.UpdateSupervisor(new SupervisorHealth(
                SupervisorHealthState.Ready,
                ready.InstanceId,
                ready.ProtocolVersion,
                null));
            AppLog.Info($"Supervisor ready: instance={ready.InstanceId} port={ready.Port}");
            RecordMilestone(diagnostics, "T6", _startup.Elapsed);
            WriteHealthSignal();
            _ = UpdateArtifactCleaner.CleanupAsync(
                layout.InstallRoot, layout.DataRoot, TimeSpan.FromSeconds(3));

            bool soakCycleComplete = !_soakCrashRequested || isRecovery;
            if (soakCycleComplete)
            {
                WriteSoakResult(_soakCrashRequested, recovered: true);
            }
            if (Environment.GetEnvironmentVariable("VIBEOCR_SELF_TEST_SMOKE") == "t6"
                && soakCycleComplete)
            {
                FlushStartupTrace();
                Environment.Exit(0);
            }
            return true;
        }
        catch (Exception error)
        {
            AppLog.Error("Supervisor connection failed", error);
            await DisconnectSupervisorResourcesAsync();
            diagnostics.UpdateSupervisor(new SupervisorHealth(
                SupervisorHealthState.Faulted, null, null, error.Message));
            FailSoakRun(error.Message);
            return false;
        }
        finally
        {
            _supervisorLifecycle.Release();
        }
    }

    private static void WriteSoakResult(bool requested, bool recovered, string? error = null)
    {
        string? resultPath = Environment.GetEnvironmentVariable("VIBEOCR_SOAK_RESULT");
        if (string.IsNullOrWhiteSpace(resultPath)) return;
        string fullPath = Path.GetFullPath(resultPath);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        File.WriteAllText(fullPath, JsonSerializer.Serialize(new { crash_requested = requested, recovered, error }));
    }

    internal static InferenceSupervisorOptions BuildSupervisorOptions(
        RuntimeLaunch launch,
        string logPath,
        TimeSpan startupTimeout,
        bool injectSoakCrash = false)
    {
        ArgumentNullException.ThrowIfNull(launch);
        var environment = launch.Environment.ToDictionary(
            item => item.Key,
            item => item.Value,
            StringComparer.OrdinalIgnoreCase);
        if (injectSoakCrash)
        {
            environment["VIBEOCR_SUPERVISOR_SOAK_CRASH_AFTER_READY"] = "1";
        }
        return new InferenceSupervisorOptions(
            launch.PythonExecutable,
            ["-m", launch.SupervisorModule],
            launch.WorkingDirectory,
            logPath,
            startupTimeout,
            environment);
    }

    private void FailSoakRun(string error)
    {
        if (string.IsNullOrWhiteSpace(
                Environment.GetEnvironmentVariable("VIBEOCR_SOAK_RESULT")))
        {
            return;
        }

        WriteSoakResult(_soakCrashRequested, recovered: false, error);
        if (Environment.GetEnvironmentVariable("VIBEOCR_SELF_TEST_SMOKE") == "t6")
        {
            FlushStartupTrace();
            Environment.Exit(1);
        }
    }

    private void WriteHealthSignal()
    {
        if (string.IsNullOrWhiteSpace(_startupHealthFile)) return;
        string fullPath = Path.GetFullPath(_startupHealthFile);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        File.WriteAllText(fullPath, JsonSerializer.Serialize(new { status = "healthy", pid = Environment.ProcessId, timestamp = DateTimeOffset.UtcNow }));
    }

    private void RecordMilestone(DiagnosticsViewModel diagnostics, string name, TimeSpan elapsed)
    {
        diagnostics.RecordMilestone(name, elapsed);
        _startupMilestones.TryAdd(name, elapsed.TotalSeconds);
    }

    private void FlushStartupTrace()
    {
        string? tracePath = Environment.GetEnvironmentVariable("VIBEOCR_STARTUP_TRACE");
        if (string.IsNullOrWhiteSpace(tracePath)) return;
        string fullPath = Path.GetFullPath(tracePath);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        File.AppendAllText(fullPath, JsonSerializer.Serialize(_startupMilestones) + Environment.NewLine);
    }

    private void OnSupervisorUnexpectedExit(
        object? sender,
        SupervisorUnexpectedExitEventArgs eventArgs)
    {
        if (_applicationShutdown.IsCancellationRequested
            || !ReferenceEquals(sender, _supervisorProcess)
            || Interlocked.Exchange(ref _supervisorRecoveryScheduled, 1) != 0)
        {
            return;
        }

        AppLog.Warn(
            $"Supervisor exited unexpectedly (code={eventArgs.ExitCode?.ToString() ?? "unknown"}); "
            + "scheduling one reconnect attempt.");
        void ScheduleRecovery()
        {
            _supervisorDiagnostics?.UpdateSupervisor(new SupervisorHealth(
                SupervisorHealthState.Faulted,
                null,
                null,
                "Supervisor 异常退出，正在自动恢复。"));
            _ = RecoverSupervisorAfterExitAsync();
        }

        if (_window?.DispatcherQueue.TryEnqueue(ScheduleRecovery) != true)
        {
            _ = Task.Run(ScheduleRecovery);
        }
    }

    private async Task RecoverSupervisorAfterExitAsync()
    {
        try
        {
            await _supervisorLifecycle.WaitAsync(_applicationShutdown.Token);
            try
            {
                await DisconnectSupervisorResourcesAsync();
            }
            finally
            {
                _supervisorLifecycle.Release();
            }

            await Task.Delay(TimeSpan.FromMilliseconds(250), _applicationShutdown.Token);
            if (_supervisorLayout is null || _supervisorDiagnostics is null)
            {
                throw new InvalidOperationException("Supervisor recovery context is unavailable.");
            }

            await ConnectSupervisorAfterFirstWindowAsync(
                _supervisorLayout,
                _supervisorDiagnostics,
                isRecovery: true);
        }
        catch (OperationCanceledException) when (_applicationShutdown.IsCancellationRequested)
        {
            // Application shutdown won the race; no recovery is needed.
        }
        catch (Exception error)
        {
            AppLog.Error("Supervisor recovery failed", error);
            _supervisorDiagnostics?.UpdateSupervisor(new SupervisorHealth(
                SupervisorHealthState.Faulted, null, null, error.Message));
            FailSoakRun(error.Message);
        }
        finally
        {
            Interlocked.Exchange(ref _supervisorRecoveryScheduled, 0);
        }
    }

    private async Task StopSupervisorAsync()
    {
        // Phase 8: stop the v2 inference supervisor subprocess. The supervisor
        // owns MinerU/PDF children via a Job Object, so disposing the process
        // handle tears the whole tree down. Best-effort: shutdown must not hang
        // the UI even if the child is unresponsive.
        await DisconnectSupervisorResourcesAsync();
    }

    private async Task DisconnectSupervisorResourcesAsync()
    {
        IInferenceClient? inferenceClient = _activeInferenceClient;
        _activeInferenceClient = null;
        if (inferenceClient is not null)
        {
            _inferenceGateway.Detach(inferenceClient);
            await inferenceClient.DisposeAsync();
        }

        IQrCodeClient? qrCodeClient = _activeQrCodeClient;
        _activeQrCodeClient = null;
        if (qrCodeClient is not null)
        {
            _qrCodeGateway.Detach(qrCodeClient);
            await qrCodeClient.DisposeAsync();
        }

        InferenceSupervisorProcess? process = _supervisorProcess;
        _supervisorProcess = null;
        if (process is null)
        {
            return;
        }
        process.UnexpectedExit -= OnSupervisorUnexpectedExit;
        try
        {
            process.Dispose();
        }
        catch (Exception error)
        {
            AppLog.Warn($"Supervisor shutdown error: {error.Message}");
        }
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
        await _supervisorLifecycle.WaitAsync();
        try
        {
            if (_window is not null && _windowLayoutStore is not null && _window.CaptureGeometry() is { } geometry)
            {
                _windowLayoutStore.Save(geometry);
            }
            await StopSupervisorAsync();
            await _inferenceGateway.DisposeAsync();
            await _qrCodeGateway.DisposeAsync();
            await DisposeDesktopShellAsync();
        }
        finally
        {
            _supervisorLifecycle.Release();
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
        _exclusiveLock?.Dispose();
        _exclusiveLock = null;
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

public sealed record AppLaunchOptions(string Profile, string? HealthFile, bool ShellOnly, string? Goto = null)
{
    public static AppLaunchOptions Parse(IReadOnlyList<string> args)
    {
        string profile = AppBuildDefaults.Profile;
        string? healthFile = null;
        bool shellOnly = false;
        string? gotoDestination = null;
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
            else if (string.Equals(args[index], "--shell-only", StringComparison.Ordinal))
            {
                shellOnly = true;
            }
            else if (string.Equals(args[index], "--goto", StringComparison.Ordinal))
            {
                if (index + 1 >= args.Count)
                {
                    throw new ArgumentException("--goto requires a value.", nameof(args));
                }
                string destination = args[++index];
                if (!ShellNavigation.Destinations.Contains(destination))
                {
                    throw new ArgumentException(
                        $"Unsupported --goto destination: {destination}.", nameof(args));
                }
                gotoDestination = destination;
            }
        }

        if (profile is not ("production" or "winui-dev"))
        {
            throw new ArgumentException($"Unsupported profile: {profile}.", nameof(args));
        }

        return new AppLaunchOptions(profile, healthFile, shellOnly, gotoDestination);
    }
}

public static class AppBuildDefaults
{
#if DEBUG
    public const string Profile = "winui-dev";
#else
    public const string Profile = "production";
#endif
}

public static class ShellNavigation
{
    public static IReadOnlyList<string> Destinations { get; } =
        ["home", "recognition", "batch", "qrcode", "pdf", "settings", "about", "diagnostics"];
}
