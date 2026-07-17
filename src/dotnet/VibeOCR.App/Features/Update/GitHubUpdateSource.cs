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
    // owner 必须与 Python 侧 env_config.GITHUB_OWNER（"FelixJI"）一致（SSOT）。
    // 早期误写成全小写 "felji" → GitHub API 返回 404 → 检查更新 100% 失败，
    // 被 UpdateViewModel 的 catch 吞成「检查更新失败，请检查网络」。
    private const string LatestRelease =
        "https://api.github.com/repos/FelixJI/VibeOCR/releases/latest";
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
        _package = SelectAsset(release.Assets, "-win64.zip");
        _checksum = SelectAsset(release.Assets, "-win64.zip.sha256");
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

    /// <summary>
    /// 从 release assets 中选出本进程要下载的 asset。选择规则与 Classic 侧
    /// update_service._find_asset 对齐，但前端是 Next：
    /// 1. 优先：名字含 "-Next-" 且后缀匹配（本进程是 WinUI Next 运行态）。
    /// 2. 回退：任意后缀匹配的 asset（兼容未来命名变化 / 单产物 release）。
    /// </summary>
    /// <remarks>
    /// 早期用 SingleOrDefault 按后缀匹配，在双产物 release（Classic+Next 同时发布）
    /// 时会抛 InvalidOperationException 导致检查更新崩溃；单产物时还会下到错误前端
    /// 的包（如 WinUI 进程下到 Classic zip，前端错配无法运行）。
    /// </remarks>
    internal static ReleaseAsset? SelectAsset(IEnumerable<ReleaseAsset> assets, string suffix)
    {
        ReleaseAsset? fallback = null;
        foreach (ReleaseAsset asset in assets)
        {
            if (!asset.Name.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                continue;
            if (asset.Name.Contains("-Next-", StringComparison.OrdinalIgnoreCase))
                return asset;
            fallback ??= asset;
        }
        return fallback;
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

    // TODO: 当前只硬连 browser_download_url（GitHub 直链），国内用户可能因 GitHub
    // 被墙下载失败。Classic 侧（update_service.py + env_config.py）有完整 3 源回退
    // （gh-proxy → ghproxy → GitHub 直连，按 network_type 选序）。WinUI 当前不发版、
    // 用户极少，暂不移植；正式发版前需补齐。
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

    // 提为 internal 供测试直接构造（SelectAsset 的入参类型）。Release 仍是 private
    // （只在 FetchLatestAsync 反序列化内部使用）。
    internal sealed record ReleaseAsset(
        [property: JsonPropertyName("name")] string Name,
        [property: JsonPropertyName("browser_download_url")] string BrowserDownloadUrl);
}
