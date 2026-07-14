using System.Diagnostics;
using System.IO.Compression;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json.Serialization;

namespace VibeOCR.App.Features.Update;

internal sealed class GitHubUpdateSource(
    string currentVersion,
    string installRoot,
    string updateRoot,
    HttpClient? httpClient = null) : IUpdateSource
{
    private const string LatestRelease =
        "https://api.github.com/repos/felji/VibeOCR/releases/latest";
    private readonly Version _currentVersion = ParseVersion(currentVersion);
    private readonly string _installRoot = Path.GetFullPath(installRoot);
    private readonly string _updateRoot = updateRoot;
    private readonly HttpClient _http = httpClient ?? CreateClient();
    private ReleaseAsset? _package;
    private ReleaseAsset? _checksum;
    private string? _verifiedPackagePath;

    public async Task<(string Version, bool Available)> FetchLatestAsync(
        CancellationToken cancellationToken)
    {
        Release release = await _http.GetFromJsonAsync<Release>(LatestRelease, cancellationToken)
            ?? throw new InvalidDataException("GitHub release response was empty.");
        string versionText = release.TagName.TrimStart('v', 'V');
        Version latest = ParseVersion(versionText);
        _package = release.Assets.SingleOrDefault(asset =>
            asset.Name.EndsWith("-win64.zip", StringComparison.OrdinalIgnoreCase));
        _checksum = release.Assets.SingleOrDefault(asset =>
            asset.Name.EndsWith("-win64.zip.sha256", StringComparison.OrdinalIgnoreCase));
        bool available = latest > _currentVersion && _package is not null && _checksum is not null;
        return (versionText, available);
    }

    public async Task<bool> DownloadVerifyAsync(CancellationToken cancellationToken)
    {
        ReleaseAsset package = _package
            ?? throw new InvalidOperationException("Check for updates before downloading.");
        ReleaseAsset checksum = _checksum
            ?? throw new InvalidOperationException("Release checksum is unavailable.");
        Directory.CreateDirectory(_updateRoot);
        string packagePath = Path.Combine(_updateRoot, package.Name);
        string checksumPath = packagePath + ".sha256";

        await DownloadAsync(package.BrowserDownloadUrl, packagePath, cancellationToken);
        await DownloadAsync(checksum.BrowserDownloadUrl, checksumPath, cancellationToken);

        string expected = (await File.ReadAllTextAsync(checksumPath, cancellationToken))
            .Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)[0];
        await using FileStream stream = File.OpenRead(packagePath);
        byte[] actualBytes = await SHA256.HashDataAsync(stream, cancellationToken);
        string actual = Convert.ToHexStringLower(actualBytes);
        if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
        {
            File.Delete(packagePath);
            File.Delete(checksumPath);
            return false;
        }
        _verifiedPackagePath = packagePath;
        return true;
    }

    public async Task<bool> LaunchUpdaterAsync(CancellationToken cancellationToken)
    {
        string packagePath = _verifiedPackagePath
            ?? throw new InvalidOperationException("Download and verify the update before launch.");
        Directory.CreateDirectory(_updateRoot);
        string updaterPath = ExtractStagedUpdater(packagePath);
        string readyFile = Path.Combine(_updateRoot, "updater.ready");
        File.Delete(readyFile);
        var startInfo = new ProcessStartInfo
        {
            FileName = updaterPath,
            WorkingDirectory = _installRoot,
            UseShellExecute = true,
        };
        startInfo.ArgumentList.Add("--update");
        startInfo.ArgumentList.Add(packagePath);
        startInfo.ArgumentList.Add("--app-dir");
        startInfo.ArgumentList.Add(_installRoot);
        using Process process = Process.Start(startInfo)
            ?? throw new InvalidOperationException("Failed to start the independent updater.");

        DateTimeOffset deadline = DateTimeOffset.UtcNow.AddSeconds(15);
        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (File.Exists(readyFile))
            {
                return true;
            }
            if (process.HasExited)
            {
                return false;
            }
            await Task.Delay(100, cancellationToken);
        }
        return false;
    }

    private string ExtractStagedUpdater(string packagePath)
    {
        using ZipArchive archive = ZipFile.OpenRead(packagePath);
        ZipArchiveEntry[] candidates = archive.Entries
            .Where(entry => string.Equals(
                Path.GetFileName(entry.FullName),
                "updater.exe",
                StringComparison.OrdinalIgnoreCase))
            .ToArray();
        if (candidates.Length != 1 || candidates[0].Length == 0)
        {
            throw new InvalidDataException(
                "The verified update package must contain one non-empty updater.exe.");
        }
        string updaterPath = Path.Combine(_updateRoot, "updater.exe");
        candidates[0].ExtractToFile(updaterPath, overwrite: true);
        return updaterPath;
    }

    private async Task DownloadAsync(string url, string destination, CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await _http.GetAsync(
            url,
            HttpCompletionOption.ResponseHeadersRead,
            cancellationToken);
        response.EnsureSuccessStatusCode();
        await using Stream input = await response.Content.ReadAsStreamAsync(cancellationToken);
        await using FileStream output = new(destination, FileMode.Create, FileAccess.Write, FileShare.None);
        await input.CopyToAsync(output, cancellationToken);
    }

    private static HttpClient CreateClient()
    {
        var client = new HttpClient();
        client.DefaultRequestHeaders.UserAgent.ParseAdd("VibeOCR-WinUI-Updater");
        client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
        return client;
    }

    private static Version ParseVersion(string value)
    {
        if (!Version.TryParse(value, out Version? version))
        {
            throw new InvalidDataException($"Invalid release version: {value}");
        }
        return version;
    }

    private sealed record Release(
        [property: JsonPropertyName("tag_name")] string TagName,
        [property: JsonPropertyName("assets")] ReleaseAsset[] Assets);

    private sealed record ReleaseAsset(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("browser_download_url")] string BrowserDownloadUrl);
}
