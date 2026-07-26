namespace VibeOCR.Platform.Bootstrap;

public sealed record PortableLayout(
    string Profile,
    string InstallRoot,
    string DataRoot,
    string RuntimeRoot,
    string ModelCacheRoot,
    string OutputRoot,
    string ConfigFile)
{
    /// <summary>Environment variable naming the repository root for dev runs.</summary>
    /// <remarks>
    /// Mirrors <c>VibeOCR.App.App.ResolveSupervisorRoot</c>: when set under the
    /// <c>winui-dev</c> profile, the supervisor source and (for this helper) the
    /// repository's <c>.venv</c> Python are resolved from it instead of the
    /// packaged <c>python/</c> layout.
    /// </remarks>
    public const string RepositoryRootVariable = "VIBEOCR_REPOSITORY_ROOT";

    public static PortableLayout Resolve(string executable, string profile)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(executable);
        if (profile is not ("production" or "winui-dev"))
        {
            throw new ArgumentException($"Unsupported profile: {profile}.", nameof(profile));
        }

        string candidate = Path.GetFullPath(executable);
        string extension = Path.GetExtension(candidate);
        string installRoot = extension.Equals(".exe", StringComparison.OrdinalIgnoreCase) ||
            extension.Equals(".app", StringComparison.OrdinalIgnoreCase) ||
            extension.Equals(".bin", StringComparison.OrdinalIgnoreCase) ||
            File.Exists(candidate)
            ? Path.GetDirectoryName(candidate)!
            : candidate;
        string dataRoot = profile == "production"
            ? Path.Combine(installRoot, "data")
            : Path.Combine(installRoot, "data", "profiles", profile);
        string scopedRoot = profile == "production" ? installRoot : dataRoot;

        return new PortableLayout(
            profile,
            installRoot,
            dataRoot,
            Path.Combine(scopedRoot, "python"),
            Path.Combine(scopedRoot, "models"),
            Path.Combine(scopedRoot, "output"),
            Path.Combine(scopedRoot, "config", "app_settings.json"));
    }

    /// <summary>
    /// Resolve the Python interpreter used to launch the Supervisor.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Production always uses the packaged interpreter at
    /// <c>{RuntimeRoot}/python.exe</c>. Under <c>winui-dev</c>, if
    /// <see cref="RepositoryRootVariable"/> points at a repository whose
    /// <c>.venv/Scripts/python.exe</c> exists, that interpreter is used so the
    /// supervisor runs against the same editable dependencies the developer
    /// installed via <c>uv sync</c> — mirroring how the PySide shell uses
    /// <c>sys.executable</c> and how <c>ResolveSupervisorRoot</c> already picks up
    /// the supervisor source tree.
    /// </para>
    /// <para>
    /// Otherwise the packaged-layout path is returned even though it may not
    /// exist yet; callers detect presence separately (see
    /// <see cref="Bootstrap.PrerequisiteDetector"/>).
    /// </para>
    /// </remarks>
    public static string ResolvePythonExecutable(PortableLayout layout)
    {
        if (layout.Profile == "winui-dev")
        {
            string? repository = FindRepositoryRoot(layout.InstallRoot);
            if (repository is not null)
            {
                string venvPython = Path.Combine(repository, ".venv", "Scripts", "python.exe");
                if (File.Exists(venvPython))
                {
                    return venvPython;
                }
            }
        }

        return Path.Combine(layout.RuntimeRoot, "python.exe");
    }

    /// <summary>
    /// Locate a source checkout for a development run.
    /// </summary>
    /// <remarks>
    /// An explicit <see cref="RepositoryRootVariable"/> always wins.  When it
    /// is not set, walk upwards from the supplied WinUI output directory.
    /// Visual Studio and <c>dotnet run</c> normally launch
    /// from <c>bin/&lt;Configuration&gt;/...</c>, so requiring every developer to
    /// maintain a machine-wide environment variable made the default debug
    /// experience silently fall back to the packaged production layout.
    /// </remarks>
    public static string? FindRepositoryRoot(params string?[] seeds)
    {
        string? configured = Environment.GetEnvironmentVariable(RepositoryRootVariable);
        if (!string.IsNullOrWhiteSpace(configured))
        {
            string candidate = Path.GetFullPath(configured);
            if (IsRepositoryRoot(candidate))
            {
                return candidate;
            }
        }

        foreach (string? seed in seeds.Where(value => !string.IsNullOrWhiteSpace(value)))
        {
            string fullPath = Path.GetFullPath(seed!);
            var directory = new DirectoryInfo(
                File.Exists(fullPath) ? Path.GetDirectoryName(fullPath)! : fullPath);
            while (directory is not null)
            {
                if (IsRepositoryRoot(directory.FullName))
                {
                    return directory.FullName;
                }

                directory = directory.Parent;
            }
        }

        return null;
    }

    private static bool IsRepositoryRoot(string path) =>
        File.Exists(Path.Combine(path, "pyproject.toml")) &&
        Directory.Exists(
            Path.Combine(
                path,
                "packages",
                "vibeocr-backend",
                "src",
                "vibeocr",
                "supervisor"));
}
