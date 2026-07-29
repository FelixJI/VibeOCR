using VibeOCR.Platform.Bootstrap;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class PrerequisiteDetectorTests
{
    private static readonly PortableLayout Layout = PortableLayout.Resolve("C:\\VibeOCR", "production");

    [Fact]
    public void ReportsEveryMissingPrerequisiteWithoutMutatingLayout()
    {
        var detector = new PrerequisiteDetector(
            _ => new PrerequisiteSnapshot(null, null, null, false));

        PrerequisiteReport report = detector.Detect(Layout);

        Assert.False(report.IsReady);
        Assert.Equal(
            [
                PrerequisiteKind.DotNetDesktopRuntime,
                PrerequisiteKind.WindowsAppRuntime,
                PrerequisiteKind.WebView2Runtime,
                PrerequisiteKind.RuntimeInstaller,
            ],
            report.Missing.Select(item => item.Kind));
    }

    [Fact]
    public void AcceptsCompatibleInstalledVersions()
    {
        var detector = new PrerequisiteDetector(
            _ => new PrerequisiteSnapshot("10.0.9", "2.2.0", "140.0.3485.54", true));

        PrerequisiteReport report = detector.Detect(Layout);

        Assert.True(report.IsReady);
        Assert.Empty(report.Missing);
    }

    [Theory]
    [InlineData("9.0.17", "2.2.0", false)]
    [InlineData("10.0.9", "2.1.9", false)]
    [InlineData("10.0.9", "2.2.0", true)]
    public void EnforcesMinimumDesktopAndWindowsAppRuntimeVersions(
        string desktop,
        string windowsAppRuntime,
        bool expectedReady)
    {
        var detector = new PrerequisiteDetector(
            _ => new PrerequisiteSnapshot(desktop, windowsAppRuntime, "140.0", true));

        Assert.Equal(expectedReady, detector.Detect(Layout).IsReady);
    }

    [Fact]
    public void ProductionProbeIsReadOnlyAndAlwaysReturnsFourStatuses()
    {
        string root = Path.Combine(Path.GetTempPath(), $"vibeocr-probe-{Guid.NewGuid():N}");
        PortableLayout layout = PortableLayout.Resolve(root, "winui-dev");

        PrerequisiteReport report = new PrerequisiteDetector().Detect(layout);

        Assert.Equal(4, report.Items.Count);
        Assert.False(Directory.Exists(root));
    }
}
