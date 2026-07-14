namespace VibeOCR.Platform.Update;

/// <summary>Removes updater hand-off artifacts after the new application is healthy.</summary>
public static class UpdateArtifactCleaner
{
    private static readonly string[] RootOldExecutables =
    [
        "updater.exe.old",
        "VibeOCR.exe.old",
        "VibeOCR.WinUI.exe.old",
        "VibeOCR.Bootstrapper.exe.old",
    ];

    private static readonly string[] CacheFiles =
    [
        "updater.exe",
        "updater.ready",
        "startup.healthy",
    ];

    public static async Task CleanupAsync(
        string installRoot,
        string dataRoot,
        TimeSpan initialDelay,
        CancellationToken cancellationToken = default)
    {
        string install = Path.GetFullPath(installRoot)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string data = Path.GetFullPath(dataRoot);
        string prefix = install + Path.DirectorySeparatorChar;
        if (!data.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("The data root must be inside the install root.", nameof(dataRoot));
        }

        if (initialDelay > TimeSpan.Zero)
        {
            await Task.Delay(initialDelay, cancellationToken);
        }

        string updateRoot = Path.Combine(data, "cache", "update");
        for (int attempt = 0; attempt < 10; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            foreach (string directory in new[] { "tmp", "_backup" })
            {
                TryDeleteDirectory(Path.Combine(updateRoot, directory));
            }
            if (Directory.Exists(updateRoot))
            {
                foreach (string file in Directory.EnumerateFiles(updateRoot))
                {
                    string name = Path.GetFileName(file);
                    if (CacheFiles.Contains(name, StringComparer.OrdinalIgnoreCase) ||
                        name.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ||
                        name.EndsWith(".sha256", StringComparison.OrdinalIgnoreCase))
                    {
                        TryDeleteFile(file);
                    }
                }
            }
            foreach (string name in RootOldExecutables)
            {
                TryDeleteFile(Path.Combine(install, name));
            }

            if (!HasPendingArtifacts(install, updateRoot))
            {
                return;
            }
            await Task.Delay(TimeSpan.FromMilliseconds(300), cancellationToken);
        }
    }

    private static bool HasPendingArtifacts(string installRoot, string updateRoot)
    {
        bool oldExecutable = RootOldExecutables.Any(name =>
            File.Exists(Path.Combine(installRoot, name)));
        if (!Directory.Exists(updateRoot))
        {
            return oldExecutable;
        }
        bool cacheArtifact = Directory.Exists(Path.Combine(updateRoot, "tmp")) ||
            Directory.Exists(Path.Combine(updateRoot, "_backup")) ||
            Directory.EnumerateFiles(updateRoot).Any(file =>
            {
                string name = Path.GetFileName(file);
                return CacheFiles.Contains(name, StringComparer.OrdinalIgnoreCase) ||
                    name.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ||
                    name.EndsWith(".sha256", StringComparison.OrdinalIgnoreCase);
            });
        return oldExecutable || cacheArtifact;
    }

    private static void TryDeleteFile(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
        }
    }

    private static void TryDeleteDirectory(string path)
    {
        try
        {
            if (Directory.Exists(path))
            {
                Directory.Delete(path, recursive: true);
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
        }
    }
}
