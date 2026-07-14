using VibeOCR.Platform.Update;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class UpdateArtifactCleanerTests
{
    [Fact]
    public async Task CleanupRemovesOnlyWhitelistedArtifacts()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-cleaner-{Guid.NewGuid():N}");
        string update = Path.Combine(root, "data", "cache", "update");
        Directory.CreateDirectory(Path.Combine(update, "tmp"));
        Directory.CreateDirectory(Path.Combine(update, "_backup"));
        CancellationToken cancellationToken = TestContext.Current.CancellationToken;
        await File.WriteAllTextAsync(Path.Combine(update, "package.zip"), "zip", cancellationToken);
        await File.WriteAllTextAsync(Path.Combine(update, "package.zip.sha256"), "hash", cancellationToken);
        await File.WriteAllTextAsync(Path.Combine(update, "updater.exe"), "exe", cancellationToken);
        await File.WriteAllTextAsync(Path.Combine(update, "updater.ready"), "ready", cancellationToken);
        await File.WriteAllTextAsync(Path.Combine(update, "startup.healthy"), "healthy", cancellationToken);
        await File.WriteAllTextAsync(Path.Combine(update, "progress.json"), "{}", cancellationToken);
        await File.WriteAllTextAsync(Path.Combine(update, "keep.txt"), "keep", cancellationToken);
        await File.WriteAllTextAsync(Path.Combine(root, "VibeOCR.WinUI.exe.old"), "old", cancellationToken);

        try
        {
            await UpdateArtifactCleaner.CleanupAsync(
                root,
                Path.Combine(root, "data"),
                TimeSpan.Zero,
                cancellationToken);

            Assert.False(Directory.Exists(Path.Combine(update, "tmp")));
            Assert.False(File.Exists(Path.Combine(update, "package.zip")));
            Assert.False(File.Exists(Path.Combine(update, "updater.exe")));
            Assert.False(File.Exists(Path.Combine(root, "VibeOCR.WinUI.exe.old")));
            Assert.True(File.Exists(Path.Combine(update, "progress.json")));
            Assert.True(File.Exists(Path.Combine(update, "keep.txt")));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task CleanupRejectsDataRootOutsideInstallRoot()
    {
        await Assert.ThrowsAsync<ArgumentException>(() => UpdateArtifactCleaner.CleanupAsync(
            Path.Combine(Path.GetTempPath(), "install"),
            Path.Combine(Path.GetTempPath(), "outside"),
            TimeSpan.Zero,
            TestContext.Current.CancellationToken));
    }
}
