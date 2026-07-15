using VibeOCR.Platform.Bootstrap;
using System.Diagnostics;
using System.Text.Json;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class PortableLayoutTests
{
    [Fact]
    public void ProductionLayoutMatchesPythonAppPaths()
    {
        string root = Path.Combine(Path.GetTempPath(), "Vibe OCR With Spaces");
        PortableLayout layout = PortableLayout.Resolve(Path.Combine(root, "VibeOCR.WinUI.exe"), "production");

        Assert.Equal("production", layout.Profile);
        Assert.Equal(Path.GetFullPath(root), layout.InstallRoot);
        Assert.Equal(Path.Combine(root, "data"), layout.DataRoot);
        Assert.Equal(Path.Combine(root, "python"), layout.RuntimeRoot);
        Assert.Equal(Path.Combine(root, "models"), layout.ModelCacheRoot);
        Assert.Equal(Path.Combine(root, "output"), layout.OutputRoot);
        Assert.Equal(Path.Combine(root, "config", "app_settings.json"), layout.ConfigFile);
    }

    [Fact]
    public void WinUiDevLayoutIsIsolatedAndDoesNotTouchProductionFiles()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-layout-{Guid.NewGuid():N}");
        string productionConfig = Path.Combine(root, "config", "app_settings.json");
        Directory.CreateDirectory(Path.GetDirectoryName(productionConfig)!);
        File.WriteAllText(productionConfig, "production");
        DateTime before = File.GetLastWriteTimeUtc(productionConfig);

        PortableLayout layout = PortableLayout.Resolve(root, "winui-dev");

        Assert.Equal("winui-dev", layout.Profile);
        string profileRoot = Path.Combine(root, "data", "profiles", "winui-dev");
        Assert.Equal(profileRoot, layout.DataRoot);
        Assert.Equal(Path.Combine(profileRoot, "python"), layout.RuntimeRoot);
        Assert.Equal(Path.Combine(profileRoot, "models"), layout.ModelCacheRoot);
        Assert.Equal(Path.Combine(profileRoot, "output"), layout.OutputRoot);
        Assert.Equal(Path.Combine(profileRoot, "config", "app_settings.json"), layout.ConfigFile);
        Assert.Equal("production", File.ReadAllText(productionConfig));
        Assert.Equal(before, File.GetLastWriteTimeUtc(productionConfig));
        Assert.False(Directory.Exists(profileRoot));
    }

    [Theory]
    [InlineData("")]
    [InlineData("other")]
    public void UnknownProfilesAreRejected(string profile) =>
        Assert.Throws<ArgumentException>(() => PortableLayout.Resolve("C:\\VibeOCR", profile));

    [Fact]
    public void ProductionPythonExecutableIsAlwaysThePackagedLayout()
    {
        // REPOSITORY_ROOT must not influence the production interpreter.
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-prod-python-{Guid.NewGuid():N}");
        PortableLayout layout = PortableLayout.Resolve(Path.Combine(root, "VibeOCR.WinUI.exe"), "production");
        using var _ = WithEnvironment(PortableLayout.RepositoryRootVariable, root);

        string exe = PortableLayout.ResolvePythonExecutable(layout);
        Assert.Equal(Path.Combine(root, "python", "python.exe"), exe);
    }

    [Fact]
    public void WinUiDevPythonPrefersRepositoryVenvWhenPresent()
    {
        // Build a fake repository with a .venv/Scripts/python.exe and point
        // VIBEOCR_REPOSITORY_ROOT at it — the dev interpreter must win over the
        // packaged layout (matching how ResolveWorkerRoot picks up the repo).
        string repo = Path.Combine(Path.GetTempPath(), $"vibeocr-repo-{Guid.NewGuid():N}");
        Directory.CreateDirectory(Path.Combine(repo, ".venv", "Scripts"));
        string venvExe = Path.Combine(repo, ".venv", "Scripts", "python.exe");
        File.WriteAllText(venvExe, "fake");

        PortableLayout layout = PortableLayout.Resolve(repo, "winui-dev");
        using var _ = WithEnvironment(PortableLayout.RepositoryRootVariable, repo);

        Assert.Equal(venvExe, PortableLayout.ResolvePythonExecutable(layout));
    }

    [Fact]
    public void WinUiDevPythonFallsBackToPackagedLayoutWithoutVenv()
    {
        // REPOSITORY_ROOT set but no .venv — must not throw, returns packaged path.
        string repo = Path.Combine(Path.GetTempPath(), $"vibeocr-novenv-{Guid.NewGuid():N}");
        Directory.CreateDirectory(repo);
        PortableLayout layout = PortableLayout.Resolve(repo, "winui-dev");
        using var _ = WithEnvironment(PortableLayout.RepositoryRootVariable, repo);

        string expected = Path.Combine(layout.RuntimeRoot, "python.exe");
        Assert.Equal(expected, PortableLayout.ResolvePythonExecutable(layout));
    }

    [Fact]
    public void WinUiDevPythonIgnoresRepositoryRootWhenUnset()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-noroot-{Guid.NewGuid():N}");
        PortableLayout layout = PortableLayout.Resolve(root, "winui-dev");
        using var _ = WithEnvironment(PortableLayout.RepositoryRootVariable, null);

        Assert.Equal(
            Path.Combine(layout.RuntimeRoot, "python.exe"),
            PortableLayout.ResolvePythonExecutable(layout));
    }

    /// <summary>Set an environment variable for the duration of a test, restoring the prior value on dispose.</summary>
    private static IDisposable WithEnvironment(string name, string? value)
    {
        string? prior = Environment.GetEnvironmentVariable(name);
        if (value is null)
        {
            Environment.SetEnvironmentVariable(name, null);
        }
        else
        {
            Environment.SetEnvironmentVariable(name, value);
        }

        return new EnvironmentRestore(name, prior);
    }

    private sealed class EnvironmentRestore(string name, string? prior) : IDisposable
    {
        public void Dispose() => Environment.SetEnvironmentVariable(name, prior);
    }

    [Fact]
    [Trait("Category", "WindowsIntegration")]
    public async Task MatchesPythonResolverForTheSameFixtures()
    {
        string? python = Environment.GetEnvironmentVariable("VIBEOCR_TEST_PYTHON");
        if (string.IsNullOrWhiteSpace(python))
        {
            return;
        }

        string repository = FindRepositoryRoot();
        string root = Path.Combine(Path.GetTempPath(), "Vibe OCR parity fixture");
        const string script = "import json,sys; from vibeocr.app_paths import resolve_app_paths; p=resolve_app_paths(sys.argv[1],profile=sys.argv[2]); print(json.dumps({k:str(getattr(p,k)) for k in ('install_root','data_root','runtime_root','model_cache_root','output_root','config_file')}))";
        foreach (string profile in new[] { "production", "winui-dev" })
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = python,
                WorkingDirectory = repository,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
            };
            startInfo.ArgumentList.Add("-c");
            startInfo.ArgumentList.Add(script);
            startInfo.ArgumentList.Add(Path.Combine(root, "VibeOCR.exe"));
            startInfo.ArgumentList.Add(profile);
            startInfo.Environment["PYTHONPATH"] = Path.Combine(repository, "src");
            using Process process = Process.Start(startInfo)!;
            string output = await process.StandardOutput.ReadToEndAsync(
                TestContext.Current.CancellationToken);
            await process.WaitForExitAsync(TestContext.Current.CancellationToken);
            Assert.Equal(0, process.ExitCode);
            Dictionary<string, string> pythonPaths = JsonSerializer.Deserialize<Dictionary<string, string>>(output)!;
            PortableLayout dotnet = PortableLayout.Resolve(Path.Combine(root, "VibeOCR.exe"), profile);

            Assert.Equal(dotnet.InstallRoot, pythonPaths["install_root"], ignoreCase: true);
            Assert.Equal(dotnet.DataRoot, pythonPaths["data_root"], ignoreCase: true);
            Assert.Equal(dotnet.RuntimeRoot, pythonPaths["runtime_root"], ignoreCase: true);
            Assert.Equal(dotnet.ModelCacheRoot, pythonPaths["model_cache_root"], ignoreCase: true);
            Assert.Equal(dotnet.OutputRoot, pythonPaths["output_root"], ignoreCase: true);
            Assert.Equal(dotnet.ConfigFile, pythonPaths["config_file"], ignoreCase: true);
        }
    }

    private static string FindRepositoryRoot()
    {
        foreach (string? seed in new[]
                 {
                     Environment.GetEnvironmentVariable("VIBEOCR_REPOSITORY_ROOT"),
                     Directory.GetCurrentDirectory(),
                     AppContext.BaseDirectory,
                 })
        {
            DirectoryInfo? directory = string.IsNullOrWhiteSpace(seed) ? null : new(seed);
            while (directory is not null)
            {
                if (File.Exists(Path.Combine(directory.FullName, "pyproject.toml")))
                {
                    return directory.FullName;
                }

                directory = directory.Parent;
            }
        }

        throw new DirectoryNotFoundException("Could not locate repository root.");
    }
}
