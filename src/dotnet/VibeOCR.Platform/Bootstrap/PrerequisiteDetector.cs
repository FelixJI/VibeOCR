using System.Runtime.InteropServices;
using Windows.ApplicationModel;
using Windows.Management.Deployment;
using Windows.System;

namespace VibeOCR.Platform.Bootstrap;

public enum PrerequisiteKind
{
    DotNetDesktopRuntime,
    WindowsAppRuntime,
    WebView2Runtime,
    RuntimeInstaller,
}

public sealed record PrerequisiteSnapshot(
    string? DotNetDesktopVersion,
    string? WindowsAppRuntimeVersion,
    string? WebView2Version,
    bool RuntimeInstallerPresent);

public sealed record PrerequisiteStatus(
    PrerequisiteKind Kind,
    bool IsInstalled,
    string? InstalledVersion,
    string MinimumVersion,
    string RepairUri);

public sealed record PrerequisiteReport(IReadOnlyList<PrerequisiteStatus> Items)
{
    public bool IsReady => Items.All(item => item.IsInstalled);
    public IEnumerable<PrerequisiteStatus> Missing => Items.Where(item => !item.IsInstalled);
}

public sealed class PrerequisiteDetector
{
    public const string MinimumDotNetDesktopVersion = "10.0.0";
    public const string MinimumWindowsAppRuntimeVersion = "2.2.0";
    private readonly Func<PortableLayout, PrerequisiteSnapshot> _capture;

    public PrerequisiteDetector()
        : this(WindowsPrerequisiteProbe.Capture)
    {
    }

    public PrerequisiteDetector(Func<PortableLayout, PrerequisiteSnapshot> capture)
    {
        _capture = capture ?? throw new ArgumentNullException(nameof(capture));
    }

    public PrerequisiteReport Detect(PortableLayout layout)
    {
        ArgumentNullException.ThrowIfNull(layout);
        PrerequisiteSnapshot snapshot = _capture(layout);
        return new PrerequisiteReport(
        [
            Status(
                PrerequisiteKind.DotNetDesktopRuntime,
                snapshot.DotNetDesktopVersion,
                MinimumDotNetDesktopVersion,
                "https://dotnet.microsoft.com/download/dotnet/10.0"),
            Status(
                PrerequisiteKind.WindowsAppRuntime,
                snapshot.WindowsAppRuntimeVersion,
                MinimumWindowsAppRuntimeVersion,
                "https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads"),
            new PrerequisiteStatus(
                PrerequisiteKind.WebView2Runtime,
                !string.IsNullOrWhiteSpace(snapshot.WebView2Version),
                snapshot.WebView2Version,
                "Evergreen",
                "https://developer.microsoft.com/microsoft-edge/webview2/"),
            new PrerequisiteStatus(
                PrerequisiteKind.RuntimeInstaller,
                snapshot.RuntimeInstallerPresent,
                snapshot.RuntimeInstallerPresent ? "Bundled" : null,
                "Bundled",
                "repair://vibeocr/runtime-installer"),
        ]);
    }

    private static PrerequisiteStatus Status(
        PrerequisiteKind kind,
        string? installed,
        string minimum,
        string repairUri) =>
        new(kind, IsCompatible(installed, minimum), installed, minimum, repairUri);

    private static bool IsCompatible(string? installed, string minimum)
    {
        string normalized = installed?.Split(['-', '+'], 2)[0] ?? string.Empty;
        return Version.TryParse(normalized, out Version? actual) &&
            Version.TryParse(minimum, out Version? required) &&
            actual >= required;
    }
}

internal static class WindowsPrerequisiteProbe
{
    public static PrerequisiteSnapshot Capture(PortableLayout layout)
    {
        return new PrerequisiteSnapshot(
            FindDotNetDesktopVersion(),
            FindWindowsAppRuntimeVersion(),
            FindWebView2Version(),
            File.Exists(RuntimeInstallerConfiguration.ForNext(layout).Executable));
    }

    private static string? FindDotNetDesktopVersion()
    {
        string root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "dotnet",
            "shared",
            "Microsoft.WindowsDesktop.App");
        if (!Directory.Exists(root))
        {
            return null;
        }

        return Directory.EnumerateDirectories(root)
            .Select(Path.GetFileName)
            .Where(value => Version.TryParse(value, out _))
            .OrderByDescending(value => Version.Parse(value!))
            .FirstOrDefault();
    }

    private static string? FindWindowsAppRuntimeVersion()
    {
        try
        {
            var manager = new PackageManager();
            Package? package = manager.FindPackagesForUser(string.Empty)
                .Where(item => item.Id.Name.Equals(
                    "Microsoft.WindowsAppRuntime.2.2",
                    StringComparison.OrdinalIgnoreCase))
                .Where(item => item.Id.Architecture is ProcessorArchitecture.X64 or ProcessorArchitecture.Neutral)
                .FirstOrDefault(item => item.Status.VerifyIsOK());
            if (package is null)
            {
                return null;
            }

            PackageVersion version = package.Id.Version;
            return $"{version.Major}.{version.Minor}.{version.Build}.{version.Revision}";
        }
        catch (Exception error) when (error is UnauthorizedAccessException or TypeLoadException)
        {
            return null;
        }
    }

    private static string? FindWebView2Version()
    {
        try
        {
            int result = GetAvailableCoreWebView2BrowserVersionString(null, out nint value);
            if (result < 0 || value == 0)
            {
                return null;
            }

            try
            {
                return Marshal.PtrToStringUni(value);
            }
            finally
            {
                Marshal.FreeCoTaskMem(value);
            }
        }
        catch (DllNotFoundException)
        {
            return null;
        }
    }

    [DllImport("WebView2Loader.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int GetAvailableCoreWebView2BrowserVersionString(
        string? browserExecutableFolder,
        out nint versionInfo);
}
