using VibeOCR.Platform.Bootstrap;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class PortableLayoutTests
{
    [Fact]
    public void ProductionLayoutContainsOnlyProductOwnedPaths()
    {
        string root = Path.Combine(Path.GetTempPath(), "Vibe OCR With Spaces");
        PortableLayout layout = PortableLayout.Resolve(
            Path.Combine(root, "VibeOCR.Next.exe"),
            "production");

        Assert.Equal("production", layout.Profile);
        Assert.Equal(Path.GetFullPath(root), layout.InstallRoot);
        Assert.Equal(Path.Combine(root, "data"), layout.DataRoot);
        Assert.Equal(Path.Combine(root, "output"), layout.OutputRoot);
        Assert.Equal(Path.Combine(root, "config", "app_settings.json"), layout.ConfigFile);
        Assert.Null(layout.PortableLayoutManifest);
        Assert.DoesNotContain(
            layout.GetType().GetProperties(),
            property => property.Name.Contains("Runtime", StringComparison.OrdinalIgnoreCase) ||
                property.Name.Contains("Model", StringComparison.OrdinalIgnoreCase) ||
                property.Name.Contains("Python", StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public void WinUiDevProductDataIsIsolated()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-layout-{Guid.NewGuid():N}");
        PortableLayout layout = PortableLayout.Resolve(root, "winui-dev");

        string profileRoot = Path.Combine(root, "data", "profiles", "winui-dev");
        Assert.Equal(profileRoot, layout.DataRoot);
        Assert.Equal(Path.Combine(profileRoot, "output"), layout.OutputRoot);
        Assert.Equal(
            Path.Combine(profileRoot, "config", "app_settings.json"),
            layout.ConfigFile);
        Assert.False(Directory.Exists(profileRoot));
    }

    [Fact]
    public void SharedLayoutIsBoundOnlyWhenExplicitlySupplied()
    {
        string bundle = Path.Combine(Path.GetTempPath(), $"vibeocr-bundle-{Guid.NewGuid():N}");
        string product = Path.Combine(bundle, "next");
        string manifest = Path.Combine(bundle, "portable-layout.json");

        PortableLayout standalone = PortableLayout.Resolve(product, "production");
        PortableLayout shared = PortableLayout.Resolve(product, "production", manifest);

        Assert.Null(standalone.PortableLayoutManifest);
        Assert.Equal(Path.GetFullPath(manifest), shared.PortableLayoutManifest);
    }

    [Fact]
    public void ResolverNeverScansParentsForPortableLayout()
    {
        string bundle = Path.Combine(Path.GetTempPath(), $"vibeocr-bundle-{Guid.NewGuid():N}");
        string product = Path.Combine(bundle, "next");
        Directory.CreateDirectory(product);
        File.WriteAllText(Path.Combine(bundle, "portable-layout.json"), "{}");
        try
        {
            PortableLayout layout = PortableLayout.Resolve(product, "production");
            Assert.Null(layout.PortableLayoutManifest);
        }
        finally
        {
            Directory.Delete(bundle, recursive: true);
        }
    }

    [Theory]
    [InlineData("")]
    [InlineData("other")]
    public void UnknownProfilesAreRejected(string profile) =>
        Assert.Throws<ArgumentException>(() => PortableLayout.Resolve("C:\\VibeOCR", profile));
}
