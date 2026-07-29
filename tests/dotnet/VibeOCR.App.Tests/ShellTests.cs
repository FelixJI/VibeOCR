using System.Text.Json;
using VibeOCR.App.ViewModels;
using VibeOCR.Platform.Bootstrap;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class ShellTests
{
    [Fact]
    public void BuildConfigurationSelectsSafeDefaultAndExplicitProfileWins()
    {
        AppLaunchOptions defaults = AppLaunchOptions.Parse([]);
        AppLaunchOptions production = AppLaunchOptions.Parse(["--profile", "production"]);
        AppLaunchOptions development = AppLaunchOptions.Parse(["--profile", "winui-dev"]);
        AppLaunchOptions health = AppLaunchOptions.Parse(["--health-file", "startup.healthy"]);

        Assert.Equal(AppBuildDefaults.Profile, defaults.Profile);
        Assert.Equal("production", production.Profile);
        Assert.Equal("winui-dev", development.Profile);
        Assert.EndsWith("startup.healthy", health.HealthFile);
        Assert.Contains("diagnostics", ShellNavigation.Destinations);
    }

    [Theory]
    [InlineData("other")]
    [InlineData("")]
    public void UnsupportedProfilesAreRejected(string profile) =>
        Assert.Throws<ArgumentException>(() => AppLaunchOptions.Parse(["--profile", profile]));

    [Fact]
    public void MissingProfileValueIsRejected() =>
        Assert.Throws<ArgumentException>(() => AppLaunchOptions.Parse(["--profile"]));

    [Fact]
    public void SupervisorOptionsUseInstallerLaunchContractVerbatim()
    {
        var launch = new RuntimeLaunch(
            "manifest/win-x64-cpu",
            "win-x64-cpu",
            @"D:\shared\runtimes\python.exe",
            "custom.backend.supervisor",
            @"D:\products\next",
            @"D:\shared\models",
            new Dictionary<string, string>
            {
                ["VIBEOCR_RUNTIME_ROOT"] = @"D:\shared\runtimes",
            });

        InferenceSupervisorOptions options = App.BuildSupervisorOptions(
            launch,
            @"D:\products\next\data\supervisor.log",
            TimeSpan.FromSeconds(42),
            injectSoakCrash: true);

        Assert.Equal(launch.PythonExecutable, options.FileName);
        Assert.Equal(["-m", launch.SupervisorModule], options.Arguments);
        Assert.Equal(launch.WorkingDirectory, options.WorkingDirectory);
        Assert.Equal(
            launch.Environment["VIBEOCR_RUNTIME_ROOT"],
            options.EnvironmentOverrides!["VIBEOCR_RUNTIME_ROOT"]);
        Assert.Equal(
            "1",
            options.EnvironmentOverrides["VIBEOCR_SUPERVISOR_SOAK_CRASH_AFTER_READY"]);
    }

    [Fact]
    public void GotoDestinationIsParsedWhenValid()
    {
        AppLaunchOptions result = AppLaunchOptions.Parse(["--goto", "pdf"]);
        Assert.Equal("pdf", result.Goto);
    }

    [Fact]
    public void GotoDefaultsToNullWhenAbsent()
    {
        AppLaunchOptions result = AppLaunchOptions.Parse([]);
        Assert.Null(result.Goto);
    }

    [Theory]
    [InlineData("unknown")]
    [InlineData("")]
    public void UnsupportedGotoDestinationsAreRejected(string destination) =>
        Assert.Throws<ArgumentException>(() => AppLaunchOptions.Parse(["--goto", destination]));

    [Fact]
    public void MissingGotoValueIsRejected() =>
        Assert.Throws<ArgumentException>(() => AppLaunchOptions.Parse(["--goto"]));

    [Fact]
    public void DiagnosticsShowMissingRuntimeAndSupervisorNotReady()
    {
        var report = new PrerequisiteReport(
        [
            new(PrerequisiteKind.DotNetDesktopRuntime, true, "10.0.1", "10.0.0", "https://example.test/dotnet"),
            new(PrerequisiteKind.WebView2Runtime, false, null, "Evergreen", "https://example.test/webview"),
        ]);
        var viewModel = new DiagnosticsViewModel("winui-dev", report);

        Assert.False(viewModel.IsReady);
        Assert.Equal("未就绪", viewModel.SupervisorStatus);
        Assert.Contains(viewModel.Prerequisites, item =>
            item.Kind == PrerequisiteKind.WebView2Runtime && !item.IsInstalled);
    }

    [Fact]
    public void DiagnosticsExposeProtocolIncompatibility()
    {
        var viewModel = new DiagnosticsViewModel("winui-dev", ReadyReport());

        viewModel.UpdateSupervisor(new SupervisorHealth(
            SupervisorHealthState.ProtocolIncompatible,
            "sup-123",
            2,
            "expected protocol 2"));

        Assert.Equal("协议不兼容", viewModel.SupervisorStatus);
        Assert.Equal("sup-123", viewModel.SupervisorInstanceId);
        Assert.Equal("客户端 v2 / Supervisor v2", viewModel.ProtocolStatus);
        Assert.False(viewModel.IsReady);
    }

    [Fact]
    public async Task RepairIsExplicitAndTargetsSelectedPrerequisite()
    {
        PrerequisiteStatus? repaired = null;
        var missing = new PrerequisiteStatus(
            PrerequisiteKind.WindowsAppRuntime,
            false,
            null,
            "2.2.0",
            "https://example.test/windows-app-runtime");
        var viewModel = new DiagnosticsViewModel(
            "winui-dev",
            new PrerequisiteReport([missing]),
            (item, _) =>
            {
                repaired = item;
                return Task.CompletedTask;
            });

        await viewModel.RepairAsync(PrerequisiteKind.WindowsAppRuntime, TestContext.Current.CancellationToken);

        Assert.Same(missing, repaired);
    }

    [Fact]
    public async Task ExportRedactsSecretsAndAbsolutePaths()
    {
        string destination = Path.Combine(Path.GetTempPath(), $"vibeocr-diagnostics-{Guid.NewGuid():N}.json");
        try
        {
            var viewModel = new DiagnosticsViewModel("winui-dev", ReadyReport());
            viewModel.UpdateSupervisor(new SupervisorHealth(
                SupervisorHealthState.Faulted,
                "sup-123",
                1,
                @"token=top-secret; log=C:\Users\alice\private\supervisor.log"));
            viewModel.RecordMilestone("T0", TimeSpan.Zero);
            viewModel.RecordMilestone("T6", TimeSpan.FromMilliseconds(320));

            Assert.Contains(viewModel.Milestones, item => item.Name == "T0");
            Assert.Contains(viewModel.Milestones, item => item.Name == "T6");

            await viewModel.ExportAsync(destination, TestContext.Current.CancellationToken);
            string exported = await File.ReadAllTextAsync(destination, TestContext.Current.CancellationToken);

            Assert.DoesNotContain("top-secret", exported, StringComparison.Ordinal);
            Assert.DoesNotContain(@"C:\Users\alice", exported, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("<redacted>", exported, StringComparison.Ordinal);
            Assert.Contains("T6", exported, StringComparison.Ordinal);
            using JsonDocument document = JsonDocument.Parse(exported);
            Assert.Equal(2, document.RootElement.GetProperty("schema_version").GetInt32());
            JsonElement supervisor = document.RootElement.GetProperty("supervisor");
            Assert.Equal("sup-123", supervisor.GetProperty("instance_id").GetString());
            Assert.False(document.RootElement.TryGetProperty("worker", out _));
        }
        finally
        {
            File.Delete(destination);
        }
    }

    private static PrerequisiteReport ReadyReport() =>
        new(
        [
            new(PrerequisiteKind.DotNetDesktopRuntime, true, "10.0.1", "10.0.0", "https://example.test/dotnet"),
            new(PrerequisiteKind.WindowsAppRuntime, true, "2.2.0", "2.2.0", "https://example.test/windows-app-runtime"),
            new(PrerequisiteKind.WebView2Runtime, true, "140.0", "Evergreen", "https://example.test/webview"),
            new(PrerequisiteKind.RuntimeInstaller, true, "Bundled", "Bundled", "repair://vibeocr/runtime-installer"),
        ]);
}
