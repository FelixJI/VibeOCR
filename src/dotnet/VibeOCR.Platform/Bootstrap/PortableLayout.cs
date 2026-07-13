namespace VibeOCR.Platform.Bootstrap;

public sealed record PortableLayout(
    string InstallRoot,
    string DataRoot,
    string RuntimeRoot,
    string ModelCacheRoot,
    string OutputRoot,
    string ConfigFile)
{
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
            installRoot,
            dataRoot,
            Path.Combine(scopedRoot, "python"),
            Path.Combine(scopedRoot, "models"),
            Path.Combine(scopedRoot, "output"),
            Path.Combine(scopedRoot, "config", "app_settings.json"));
    }
}
