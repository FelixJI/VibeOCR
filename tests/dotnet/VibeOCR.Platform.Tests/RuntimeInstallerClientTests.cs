using System.Diagnostics;
using VibeOCR.Platform.Bootstrap;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class RuntimeInstallerClientTests
{
    [Fact]
    public async Task EnsureUsesOnlyInstallerLaunchContract()
    {
        var runner = new StubRunner(
            new RuntimeInstallerProcessResult(
                0,
                """
                {
                  "runtime_id": "abc/win-x64-cpu",
                  "profile": "win-x64-cpu",
                  "python_executable": "C:\\store\\python.exe",
                  "supervisor_module": "vibeocr.backend.supervisor.main",
                  "working_directory": "C:\\Next",
                  "model_root": "C:\\store\\models",
                  "environment": {
                    "VIBEOCR_RUNTIME_ROOT": "C:\\store",
                    "VIBEOCR_MODEL_ROOT": "C:\\store\\models"
                  }
                }
                """,
                string.Empty));
        var client = new RuntimeInstallerClient(Configuration(), runner);

        RuntimeLaunch launch = await client.EnsureAsync(
            TestContext.Current.CancellationToken);

        Assert.Equal(@"C:\store\python.exe", launch.PythonExecutable);
        Assert.Equal("vibeocr.backend.supervisor.main", launch.SupervisorModule);
        Assert.Equal(@"C:\Next", launch.WorkingDirectory);
        Assert.Equal(@"C:\store", launch.Environment["VIBEOCR_RUNTIME_ROOT"]);
        Assert.Equal("ensure", runner.LastStartInfo!.ArgumentList[0]);
        Assert.DoesNotContain(
            runner.LastStartInfo.ArgumentList,
            argument => argument.Contains("pip", StringComparison.OrdinalIgnoreCase) ||
                argument.Contains("torch", StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public async Task InspectParsesIntegrityWithoutDerivingRuntimePaths()
    {
        var runner = new StubRunner(
            new RuntimeInstallerProcessResult(
                0,
                """
                {
                  "status": "ready",
                  "runtime_id": "abc/win-x64-cpu",
                  "profile": "win-x64-cpu",
                  "runtime_root": "C:\\store",
                  "manifest_sha256": "abc",
                  "backend_version": "0.7.0",
                  "integrity": "verified"
                }
                """,
                string.Empty));
        var client = new RuntimeInstallerClient(Configuration(), runner);

        RuntimeInspection inspection = await client.InspectAsync(
            TestContext.Current.CancellationToken);

        Assert.Equal("ready", inspection.Status);
        Assert.Equal("verified", inspection.Integrity);
        Assert.Equal("inspect", runner.LastStartInfo!.ArgumentList[0]);
    }

    [Fact]
    public async Task ExplicitPortableLayoutBindingIsForwardedWithoutParentDiscovery()
    {
        RuntimeInstallerConfiguration configuration = Configuration() with
        {
            PortableLayoutManifest = @"C:\bundle\portable-layout.json",
            ProductId = "next",
        };
        var runner = LaunchRunner();
        var client = new RuntimeInstallerClient(configuration, runner);

        await client.RepairAsync(TestContext.Current.CancellationToken);

        IReadOnlyList<string> arguments = runner.LastStartInfo!.ArgumentList;
        AssertOption(arguments, "--layout-manifest", @"C:\bundle\portable-layout.json");
        AssertOption(arguments, "--product-id", "next");
        Assert.Equal("repair", arguments[0]);
    }

    [Fact]
    public async Task StandaloneInvocationDoesNotGuessSharedLayout()
    {
        var runner = LaunchRunner();
        var client = new RuntimeInstallerClient(Configuration(), runner);

        await client.EnsureAsync(TestContext.Current.CancellationToken);

        Assert.DoesNotContain("--layout-manifest", runner.LastStartInfo!.ArgumentList);
        Assert.DoesNotContain("--product-id", runner.LastStartInfo.ArgumentList);
    }

    [Fact]
    public async Task InstallerErrorIsReportedFromJson()
    {
        var runner = new StubRunner(
            new RuntimeInstallerProcessResult(
                1,
                """{"error":"RuntimeInstallError","message":"hash mismatch"}""",
                string.Empty));
        var client = new RuntimeInstallerClient(Configuration(), runner);

        RuntimeInstallerException error = await Assert.ThrowsAsync<RuntimeInstallerException>(
            () => client.EnsureAsync(TestContext.Current.CancellationToken));

        Assert.Contains("hash mismatch", error.Message);
    }

    [Fact]
    public async Task GarbageCollectionForwardsAllProductLocks()
    {
        var runner = new StubRunner(
            new RuntimeInstallerProcessResult(
                0,
                """["old/runtime"]""",
                string.Empty));
        var client = new RuntimeInstallerClient(Configuration(), runner);

        IReadOnlyList<string> removed = await client.GcAsync(
            [@"C:\Next\component-lock.json", @"C:\Classic\component-lock.json"],
            TestContext.Current.CancellationToken);

        Assert.Equal(["old/runtime"], removed);
        Assert.Equal("gc", runner.LastStartInfo!.ArgumentList[0]);
        Assert.Equal(
            2,
            runner.LastStartInfo.ArgumentList.Count(
                argument => argument == "--referenced-component-lock"));
    }

    [Fact]
    public async Task CommandRunnerRejectsTamperedBoundInstallerBeforeExecution()
    {
        string root = Path.Combine(
            Path.GetTempPath(),
            $"vibeocr-installer-test-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            string executable = Path.Combine(
                root,
                "vibeocr-runtime-installer.exe");
            string manifest = Path.Combine(root, "runtime-manifest.json");
            await File.WriteAllBytesAsync(
                executable,
                [1, 2, 3],
                TestContext.Current.CancellationToken);
            await File.WriteAllTextAsync(
                manifest,
                "{\"installer\":{\"executable_sha256\":\"" +
                    new string('0', 64) +
                    "\"}}",
                TestContext.Current.CancellationToken);
            var startInfo = new ProcessStartInfo(executable)
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            startInfo.ArgumentList.Add("--runtime-manifest");
            startInfo.ArgumentList.Add(manifest);

            var runner = new RuntimeInstallerCommandRunner();
            RuntimeInstallerException error =
                await Assert.ThrowsAsync<RuntimeInstallerException>(
                    () => runner.RunAsync(
                        startInfo,
                        TestContext.Current.CancellationToken));

            Assert.Contains("SHA-256 mismatch", error.Message);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public void NextDefaultsPointOnlyToCommittedReleaseBindings()
    {
        PortableLayout layout = PortableLayout.Resolve(
            @"C:\Next\VibeOCR.Next.exe",
            "production");

        RuntimeInstallerConfiguration configuration =
            RuntimeInstallerConfiguration.ForNext(
                layout,
                runtimeProfile: "win-x64-cu126",
                executable: @"C:\Next\runtime-installer\installer.exe");

        Assert.Equal(@"C:\Next", configuration.ProductRoot);
        Assert.Equal(@"C:\Next\component-lock.json", configuration.ComponentLock);
        Assert.Equal(
            @"C:\Next\backend\runtime-manifest.json",
            configuration.RuntimeManifest);
        Assert.Equal("win-x64-cu126", configuration.RuntimeProfile);
    }

    private static RuntimeInstallerConfiguration Configuration() =>
        new(
            @"C:\Next\runtime-installer\vibeocr-runtime-installer.exe",
            @"C:\Next",
            @"C:\Next\component-lock.json",
            @"C:\Next\backend\runtime-manifest.json",
            "win-x64-cpu");

    private static StubRunner LaunchRunner() =>
        new(
            new RuntimeInstallerProcessResult(
                0,
                """
                {
                  "runtime_id": "abc/win-x64-cpu",
                  "profile": "win-x64-cpu",
                  "python_executable": "C:\\store\\python.exe",
                  "supervisor_module": "vibeocr.backend.supervisor.main",
                  "working_directory": "C:\\Next",
                  "model_root": "C:\\store\\models",
                  "environment": {}
                }
                """,
                string.Empty));

    private static void AssertOption(
        IReadOnlyList<string> arguments,
        string option,
        string expected)
    {
        int index = arguments.ToList().IndexOf(option);
        Assert.True(index >= 0);
        Assert.Equal(expected, arguments[index + 1]);
    }

    private sealed class StubRunner(RuntimeInstallerProcessResult result)
        : IRuntimeInstallerCommandRunner
    {
        public ProcessStartInfo? LastStartInfo { get; private set; }

        public Task<RuntimeInstallerProcessResult> RunAsync(
            ProcessStartInfo startInfo,
            CancellationToken cancellationToken)
        {
            LastStartInfo = startInfo;
            return Task.FromResult(result);
        }
    }
}
