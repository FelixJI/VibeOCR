using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace VibeOCR.Platform.Migration;

/// <summary>
/// Idempotent profile/config migrator mirroring the Python
/// <c>vibeocr.migration.profile_migrator</c>. Adds <c>schema_version</c> to
/// <c>app_settings.json</c>, writes a hashed backup, and replaces atomically.
/// A second run is a no-op.
/// </summary>
public static class ProfileMigrationClient
{
    public const int CurrentSchemaVersion = 1;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static MigrationResult MigrateConfig(string configPath)
    {
        var path = new FileInfo(configPath);
        if (!path.Exists)
        {
            return new MigrationResult("skipped", configPath, null, CurrentSchemaVersion, "file not found");
        }

        string text;
        try
        {
            text = File.ReadAllText(path.FullName, Encoding.UTF8);
        }
        catch (Exception)
        {
            return new MigrationResult("skipped", configPath, null, CurrentSchemaVersion, "cannot read");
        }

        Dictionary<string, object?> data;
        try
        {
            data = JsonSerializer.Deserialize<Dictionary<string, object?>>(text, JsonOptions)
                ?? throw new JsonException("null root");
        }
        catch (JsonException)
        {
            return new MigrationResult("skipped", configPath, null, CurrentSchemaVersion, "not a JSON object");
        }

        if (data.TryGetValue("schema_version", out object? existing))
        {
            int version = existing switch
            {
                JsonElement e when e.ValueKind == JsonValueKind.Number => e.GetInt32(),
                int i => i,
                _ => -1,
            };
            if (version == CurrentSchemaVersion)
            {
                return new MigrationResult("already_migrated", configPath, null, CurrentSchemaVersion, string.Empty);
            }
            if (version > CurrentSchemaVersion)
            {
                return new MigrationResult("skipped", configPath, null, CurrentSchemaVersion,
                    $"schema_version {version} is newer than migrator");
            }
        }

        string backupPath;
        try
        {
            backupPath = WriteHashedBackup(path);
        }
        catch (Exception error)
        {
            return new MigrationResult(
                "skipped",
                configPath,
                null,
                CurrentSchemaVersion,
                $"backup failed: {error.Message}");
        }
        data["schema_version"] = CurrentSchemaVersion;
        try
        {
            AtomicWrite(path, data);
        }
        catch (Exception error)
        {
            return new MigrationResult("skipped", configPath, backupPath, CurrentSchemaVersion, $"write failed: {error.Message}");
        }
        return new MigrationResult("migrated", configPath, backupPath, CurrentSchemaVersion, string.Empty);
    }

    private static string WriteHashedBackup(FileInfo path)
    {
        byte[] original = File.ReadAllBytes(path.FullName);
        string digest = Convert.ToHexStringLower(SHA256.HashData(original))[..16];
        string backup = Path.Combine(
            path.DirectoryName!,
            $"{Path.GetFileNameWithoutExtension(path.Name)}.pre-migrate-{digest}{path.Extension}.bak");
        using var stream = new FileStream(
            backup,
            FileMode.Create,
            FileAccess.Write,
            FileShare.None,
            4096,
            FileOptions.WriteThrough);
        stream.Write(original);
        stream.Flush(flushToDisk: true);
        return backup;
    }

    private static void AtomicWrite(FileInfo path, Dictionary<string, object?> data)
    {
        string payload = JsonSerializer.Serialize(data, JsonOptions);
        string tmp = path.FullName + ".tmp";
        try
        {
            using (var stream = new FileStream(
                tmp,
                FileMode.Create,
                FileAccess.Write,
                FileShare.None,
                4096,
                FileOptions.WriteThrough))
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(payload);
                stream.Write(bytes);
                stream.Flush(flushToDisk: true);
            }
            File.Move(tmp, path.FullName, overwrite: true);
        }
        finally
        {
            File.Delete(tmp);
        }
    }
}

public sealed record MigrationResult(
    string Status,
    string Path,
    string? BackupPath,
    int SchemaVersion,
    string Message);
