namespace VibeOCR.Platform.Bootstrap;

/// <summary>
/// Product-owned paths for the portable Next application.
/// </summary>
/// <remarks>
/// Runtime and model paths are deliberately absent. The Backend Runtime
/// Installer is the sole owner of those paths and returns them through its
/// JSON launch contract. A shared portable store is considered only when the
/// caller supplies an explicit <paramref name="PortableLayoutManifest"/>.
/// </remarks>
public sealed record PortableLayout(
    string Profile,
    string InstallRoot,
    string DataRoot,
    string OutputRoot,
    string ConfigFile,
    string? PortableLayoutManifest)
{
    public static PortableLayout Resolve(
        string executable,
        string profile,
        string? portableLayoutManifest = null)
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
        string? explicitManifest = string.IsNullOrWhiteSpace(portableLayoutManifest)
            ? null
            : Path.GetFullPath(portableLayoutManifest);

        return new PortableLayout(
            profile,
            installRoot,
            dataRoot,
            Path.Combine(scopedRoot, "output"),
            Path.Combine(scopedRoot, "config", "app_settings.json"),
            explicitManifest);
    }
}
