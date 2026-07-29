using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace VibeOCR.Platform.Bootstrap;

/// <summary>
/// Complete invocation binding for the Backend-owned Runtime Installer.
/// </summary>
public sealed record RuntimeInstallerConfiguration(
    string Executable,
    string ProductRoot,
    string ComponentLock,
    string RuntimeManifest,
    string RuntimeProfile,
    string? PortableLayoutManifest = null,
    string? ProductId = null)
{
    public static RuntimeInstallerConfiguration ForNext(
        PortableLayout layout,
        string? runtimeProfile = null,
        string? executable = null)
    {
        ArgumentNullException.ThrowIfNull(layout);
        string installer = executable ??
            Environment.GetEnvironmentVariable("VIBEOCR_RUNTIME_INSTALLER") ??
            Path.Combine(
                layout.InstallRoot,
                "runtime-installer",
                "vibeocr-runtime-installer.exe");
        string profile = runtimeProfile ??
            Environment.GetEnvironmentVariable("VIBEOCR_RUNTIME_PROFILE") ??
            "win-x64-cpu";
        return new RuntimeInstallerConfiguration(
            installer,
            layout.InstallRoot,
            Path.Combine(layout.InstallRoot, "component-lock.json"),
            Path.Combine(layout.InstallRoot, "backend", "runtime-manifest.json"),
            profile,
            layout.PortableLayoutManifest,
            layout.PortableLayoutManifest is null ? null : "next");
    }
}

public sealed record RuntimeInspection(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("runtime_id")] string RuntimeId,
    [property: JsonPropertyName("profile")] string Profile,
    [property: JsonPropertyName("runtime_root")] string RuntimeRoot,
    [property: JsonPropertyName("manifest_sha256")] string ManifestSha256,
    [property: JsonPropertyName("backend_version")] string BackendVersion,
    [property: JsonPropertyName("integrity")] string Integrity);

public sealed record RuntimeLaunch(
    [property: JsonPropertyName("runtime_id")] string RuntimeId,
    [property: JsonPropertyName("profile")] string Profile,
    [property: JsonPropertyName("python_executable")] string PythonExecutable,
    [property: JsonPropertyName("supervisor_module")] string SupervisorModule,
    [property: JsonPropertyName("working_directory")] string WorkingDirectory,
    [property: JsonPropertyName("model_root")] string ModelRoot,
    [property: JsonPropertyName("environment")]
    IReadOnlyDictionary<string, string> Environment);

public sealed record RuntimeInstallerProcessResult(
    int ExitCode,
    string StandardOutput,
    string StandardError);

public interface IRuntimeInstallerCommandRunner
{
    Task<RuntimeInstallerProcessResult> RunAsync(
        ProcessStartInfo startInfo,
        CancellationToken cancellationToken);
}

public interface IRuntimeInstallerClient
{
    Task<RuntimeInspection> InspectAsync(CancellationToken cancellationToken = default);
    Task<RuntimeLaunch> EnsureAsync(CancellationToken cancellationToken = default);
    Task<RuntimeLaunch> RepairAsync(CancellationToken cancellationToken = default);
    Task<IReadOnlyList<string>> GcAsync(
        IReadOnlyList<string> componentLocks,
        CancellationToken cancellationToken = default);
}

public sealed class RuntimeInstallerException : InvalidOperationException
{
    public RuntimeInstallerException(string message)
        : base(message)
    {
    }
}

/// <summary>
/// Thin process adapter around the Backend Runtime Installer JSON CLI.
/// </summary>
/// <remarks>
/// This client intentionally knows nothing about Python packages, indexes,
/// lock-file contents, runtime directories, or model directories.
/// </remarks>
public sealed class RuntimeInstallerClient : IRuntimeInstallerClient
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly RuntimeInstallerConfiguration _configuration;
    private readonly IRuntimeInstallerCommandRunner _runner;

    public RuntimeInstallerClient(RuntimeInstallerConfiguration configuration)
        : this(configuration, new RuntimeInstallerCommandRunner())
    {
    }

    public RuntimeInstallerClient(
        RuntimeInstallerConfiguration configuration,
        IRuntimeInstallerCommandRunner runner)
    {
        _configuration = configuration ?? throw new ArgumentNullException(nameof(configuration));
        _runner = runner ?? throw new ArgumentNullException(nameof(runner));
        ValidateConfiguration(configuration);
    }

    public Task<RuntimeInspection> InspectAsync(CancellationToken cancellationToken = default) =>
        InvokeAsync<RuntimeInspection>("inspect", [], cancellationToken);

    public Task<RuntimeLaunch> EnsureAsync(CancellationToken cancellationToken = default) =>
        InvokeLaunchAsync("ensure", cancellationToken);

    public Task<RuntimeLaunch> RepairAsync(CancellationToken cancellationToken = default) =>
        InvokeLaunchAsync("repair", cancellationToken);

    public Task<IReadOnlyList<string>> GcAsync(
        IReadOnlyList<string> componentLocks,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(componentLocks);
        var extraArguments = new List<string>(componentLocks.Count * 2);
        foreach (string componentLock in componentLocks)
        {
            ArgumentException.ThrowIfNullOrWhiteSpace(componentLock);
            extraArguments.Add("--referenced-component-lock");
            extraArguments.Add(Path.GetFullPath(componentLock));
        }
        return InvokeAsync<IReadOnlyList<string>>("gc", extraArguments, cancellationToken);
    }

    private async Task<RuntimeLaunch> InvokeLaunchAsync(
        string operation,
        CancellationToken cancellationToken)
    {
        RuntimeLaunch launch = await InvokeAsync<RuntimeLaunch>(
            operation,
            [],
            cancellationToken).ConfigureAwait(false);
        if (string.IsNullOrWhiteSpace(launch.PythonExecutable) ||
            string.IsNullOrWhiteSpace(launch.SupervisorModule) ||
            string.IsNullOrWhiteSpace(launch.WorkingDirectory) ||
            string.IsNullOrWhiteSpace(launch.ModelRoot) ||
            launch.Environment is null ||
            !Path.IsPathFullyQualified(launch.PythonExecutable) ||
            !Path.IsPathFullyQualified(launch.WorkingDirectory) ||
            !Path.IsPathFullyQualified(launch.ModelRoot))
        {
            throw new RuntimeInstallerException(
                $"Runtime Installer returned an invalid {operation} launch contract.");
        }
        return launch;
    }

    private async Task<T> InvokeAsync<T>(
        string operation,
        IReadOnlyList<string> extraArguments,
        CancellationToken cancellationToken)
    {
        ProcessStartInfo startInfo = BuildStartInfo(operation, extraArguments);
        RuntimeInstallerProcessResult result = await _runner.RunAsync(
            startInfo,
            cancellationToken).ConfigureAwait(false);
        if (result.ExitCode != 0)
        {
            string detail = ParseError(result.StandardOutput) ??
                result.StandardError.Trim();
            throw new RuntimeInstallerException(
                $"Runtime Installer {operation} failed with exit code {result.ExitCode}: {detail}");
        }

        try
        {
            T? value = JsonSerializer.Deserialize<T>(result.StandardOutput, JsonOptions);
            return value ?? throw new JsonException("JSON value was null.");
        }
        catch (JsonException error)
        {
            throw new RuntimeInstallerException(
                $"Runtime Installer {operation} returned invalid JSON: {error.Message}");
        }
    }

    private ProcessStartInfo BuildStartInfo(
        string operation,
        IReadOnlyList<string> extraArguments)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = _configuration.Executable,
            WorkingDirectory = _configuration.ProductRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        startInfo.ArgumentList.Add(operation);
        AddOption(startInfo, "--product-root", _configuration.ProductRoot);
        AddOption(startInfo, "--component-lock", _configuration.ComponentLock);
        AddOption(startInfo, "--runtime-manifest", _configuration.RuntimeManifest);
        AddOption(startInfo, "--profile", _configuration.RuntimeProfile);
        if (_configuration.PortableLayoutManifest is not null)
        {
            AddOption(
                startInfo,
                "--layout-manifest",
                _configuration.PortableLayoutManifest);
            AddOption(startInfo, "--product-id", _configuration.ProductId!);
        }
        foreach (string argument in extraArguments)
        {
            startInfo.ArgumentList.Add(argument);
        }
        return startInfo;
    }

    private static void AddOption(ProcessStartInfo startInfo, string name, string value)
    {
        startInfo.ArgumentList.Add(name);
        startInfo.ArgumentList.Add(value);
    }

    private static string? ParseError(string standardOutput)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(standardOutput);
            if (document.RootElement.TryGetProperty("message", out JsonElement message))
            {
                return message.GetString();
            }
        }
        catch (JsonException)
        {
        }
        return null;
    }

    private static void ValidateConfiguration(RuntimeInstallerConfiguration configuration)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(configuration.Executable);
        ArgumentException.ThrowIfNullOrWhiteSpace(configuration.ProductRoot);
        ArgumentException.ThrowIfNullOrWhiteSpace(configuration.ComponentLock);
        ArgumentException.ThrowIfNullOrWhiteSpace(configuration.RuntimeManifest);
        ArgumentException.ThrowIfNullOrWhiteSpace(configuration.RuntimeProfile);
        if ((configuration.PortableLayoutManifest is null) !=
            (configuration.ProductId is null))
        {
            throw new ArgumentException(
                "PortableLayoutManifest and ProductId must be supplied together.");
        }
    }
}

public sealed class RuntimeInstallerCommandRunner : IRuntimeInstallerCommandRunner
{
    public async Task<RuntimeInstallerProcessResult> RunAsync(
        ProcessStartInfo startInfo,
        CancellationToken cancellationToken)
    {
        VerifyBoundExecutable(startInfo);
        using Process process = Process.Start(startInfo) ??
            throw new RuntimeInstallerException("Could not start Runtime Installer.");
        Task<string> stdout = process.StandardOutput.ReadToEndAsync(cancellationToken);
        Task<string> stderr = process.StandardError.ReadToEndAsync(cancellationToken);
        try
        {
            await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException)
            {
            }
            throw;
        }
        return new RuntimeInstallerProcessResult(
            process.ExitCode,
            await stdout.ConfigureAwait(false),
            await stderr.ConfigureAwait(false));
    }

    private static void VerifyBoundExecutable(ProcessStartInfo startInfo)
    {
        try
        {
            string componentLockPath = RequireOption(
                startInfo,
                "--component-lock");
            string runtimeManifestPath = RequireOption(
                startInfo,
                "--runtime-manifest");

            byte[] componentLockBytes = File.ReadAllBytes(componentLockPath);
            byte[] runtimeManifestBytes = File.ReadAllBytes(runtimeManifestPath);
            using JsonDocument componentLock = JsonDocument.Parse(componentLockBytes);
            byte[] expectedManifestSha256 = ParseSha256(
                componentLock.RootElement
                    .GetProperty("backend")
                    .GetProperty("runtime_manifest_sha256"),
                "backend.runtime_manifest_sha256");
            byte[] actualManifestSha256 = SHA256.HashData(runtimeManifestBytes);
            if (!CryptographicOperations.FixedTimeEquals(
                actualManifestSha256,
                expectedManifestSha256))
            {
                throw new RuntimeInstallerException(
                    "Runtime manifest SHA-256 mismatch.");
            }

            using JsonDocument runtimeManifest = JsonDocument.Parse(
                runtimeManifestBytes);
            byte[] expectedExecutableSha256 = ParseSha256(
                runtimeManifest.RootElement
                .GetProperty("installer")
                .GetProperty("executable_sha256"),
                "installer.executable_sha256");
            using FileStream stream = File.OpenRead(startInfo.FileName);
            byte[] actualExecutableSha256 = SHA256.HashData(stream);
            if (!CryptographicOperations.FixedTimeEquals(
                actualExecutableSha256,
                expectedExecutableSha256))
            {
                throw new RuntimeInstallerException(
                    "Runtime Installer executable SHA-256 mismatch.");
            }
        }
        catch (RuntimeInstallerException)
        {
            throw;
        }
        catch (Exception error) when (
            error is IOException or
            UnauthorizedAccessException or
            JsonException or
            InvalidDataException or
            KeyNotFoundException or
            InvalidOperationException or
            ArgumentException)
        {
            throw new RuntimeInstallerException(
                $"Could not verify Runtime Installer trust chain: {error.Message}");
        }
    }

    private static string RequireOption(
        ProcessStartInfo startInfo,
        string option)
    {
        string? value = null;
        for (int index = 0; index < startInfo.ArgumentList.Count; index++)
        {
            if (!startInfo.ArgumentList[index].Equals(
                option,
                StringComparison.Ordinal))
            {
                continue;
            }
            if (value is not null)
            {
                throw new InvalidDataException(
                    $"{option} argument is duplicated.");
            }
            if (index + 1 >= startInfo.ArgumentList.Count)
            {
                throw new InvalidDataException(
                    $"{option} argument has no value.");
            }
            value = startInfo.ArgumentList[index + 1];
        }
        return value ??
            throw new InvalidDataException($"{option} argument is missing.");
    }

    private static byte[] ParseSha256(JsonElement value, string field)
    {
        string? digest = value.GetString();
        if (digest is null || digest.Length != 64)
        {
            throw new InvalidDataException(
                $"{field} must be a lowercase SHA-256 digest.");
        }
        foreach (char character in digest)
        {
            if (character is not (>= '0' and <= '9') and
                not (>= 'a' and <= 'f'))
            {
                throw new InvalidDataException(
                    $"{field} must be a lowercase SHA-256 digest.");
            }
        }
        return Convert.FromHexString(digest);
    }
}
