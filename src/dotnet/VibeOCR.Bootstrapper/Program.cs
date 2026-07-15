using Microsoft.Web.WebView2.Core;
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Windows.Management.Deployment;
using Windows.System;

namespace VibeOCR.Bootstrapper;

internal static class Program
{
    private const int PrerequisiteMissing = 2;
    private const int AppMissing = 3;
    private const int InvalidArguments = 4;

    [STAThread]
    private static int Main(string[] args)
    {
        string installRoot = AppDomain.CurrentDomain.BaseDirectory;
        string appPath = ReadOption(args, "--app") ?? Path.Combine(installRoot, "VibeOCR.WinUI.exe");
        string profile = ReadOption(args, "--profile") ?? "production";
        string? healthFile = ReadOption(args, "--health-file");
        if (profile is not ("production" or "winui-dev"))
        {
            Console.Error.WriteLine("Unsupported profile: " + profile);
            return InvalidArguments;
        }
        string scopedRoot = profile == "production"
            ? installRoot
            : Path.Combine(installRoot, "data", "profiles", profile);
        // Mirror VibeOCR.Platform.Bootstrap.PortableLayout.ResolvePythonExecutable:
        // under winui-dev, prefer the repository .venv python (set alongside the
        // worker source root via VIBEOCR_REPOSITORY_ROOT) over the packaged
        // python/ layout, so the same editable environment drives both checks.
        string pythonRoot = Path.Combine(scopedRoot, "python");
        string pythonExe = ResolveDevPython(profile, pythonRoot);
        bool packagedLayout = pythonExe == Path.Combine(pythonRoot, "python.exe");
        bool pythonPresent = File.Exists(pythonExe) &&
            (!packagedLayout || File.Exists(Path.Combine(pythonRoot, "python313.dll")));

        string[] missing = new string?[]
        {
            HasDotNetDesktop10() ? null : ".NET Desktop Runtime 10 x64",
            HasWindowsAppRuntime22() ? null : "Windows App Runtime 2.2 x64",
            HasWebView2() ? null : "Microsoft Edge WebView2 Evergreen Runtime",
            pythonPresent ? null : "VibeOCR Python 3.13 runtime",
        }.Where(item => item != null).Select(item => item!).ToArray();
        if (missing.Length > 0)
        {
            Console.Error.WriteLine("VibeOCR prerequisites require repair:");
            foreach (string item in missing)
            {
                Console.Error.WriteLine("- " + item);
            }

            Console.Error.WriteLine("No component was downloaded or modified.");
            return PrerequisiteMissing;
        }

        if (!File.Exists(appPath))
        {
            Console.Error.WriteLine("WinUI application is missing: " + appPath);
            return AppMissing;
        }

        string arguments = "--profile " + Quote(profile);
        if (!string.IsNullOrWhiteSpace(healthFile))
        {
            arguments += " --health-file " + Quote(Path.GetFullPath(healthFile));
        }
        var startInfo = new ProcessStartInfo
        {
            FileName = appPath,
            Arguments = arguments,
            WorkingDirectory = Path.GetDirectoryName(appPath),
            UseShellExecute = true,
        };
        Process.Start(startInfo);
        return 0;
    }

    private static string? ReadOption(string[] args, string name)
    {
        for (int index = 0; index + 1 < args.Length; index++)
        {
            if (string.Equals(args[index], name, StringComparison.Ordinal))
            {
                return args[index + 1];
            }
        }

        return null;
    }

    // Keep in sync with PortableLayout.ResolvePythonExecutable (Platform project,
    // net10.0 — not referenceable from this net472 bootstrapper). Under winui-dev
    // a repository .venv python takes precedence over the packaged python/ layout.
    private static string ResolveDevPython(string profile, string packagedPythonRoot)
    {
        if (profile == "winui-dev")
        {
            string? repository = Environment.GetEnvironmentVariable("VIBEOCR_REPOSITORY_ROOT");
            if (!string.IsNullOrWhiteSpace(repository))
            {
                string venvPython = Path.Combine(repository, ".venv", "Scripts", "python.exe");
                if (File.Exists(venvPython))
                {
                    return venvPython;
                }
            }
        }

        return Path.Combine(packagedPythonRoot, "python.exe");
    }

    private static bool HasDotNetDesktop10()
    {
        string root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "dotnet",
            "shared",
            "Microsoft.WindowsDesktop.App");
        return Directory.Exists(root) && Directory.EnumerateDirectories(root)
            .Select(Path.GetFileName)
            .Any(value => Version.TryParse(value, out Version version) && version.Major >= 10);
    }

    private static bool HasWindowsAppRuntime22()
    {
        try
        {
            return new PackageManager().FindPackagesForUser(string.Empty).Any(package =>
                package.Id.Name.Equals("Microsoft.WindowsAppRuntime.2.2", StringComparison.OrdinalIgnoreCase) &&
                (package.Id.Architecture == ProcessorArchitecture.X64 ||
                    package.Id.Architecture == ProcessorArchitecture.Neutral) &&
                package.Status.VerifyIsOK());
        }
        catch
        {
            return false;
        }
    }

    private static bool HasWebView2()
    {
        try
        {
            return !string.IsNullOrWhiteSpace(
                CoreWebView2Environment.GetAvailableBrowserVersionString());
        }
        catch
        {
            return false;
        }
    }

    private static string Quote(string value) =>
        "\"" + value.Replace("\"", "\\\"") + "\"";

}
