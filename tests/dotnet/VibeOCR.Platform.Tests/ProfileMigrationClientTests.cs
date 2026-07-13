using System.Text.Json;
using VibeOCR.Platform.Migration;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class ProfileMigrationClientTests
{
    [Fact]
    public void MigrateUnversionedAddsSchemaVersionAndBackup()
    {
        string path = Path.Combine(Path.GetTempPath(), $"migrate-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, "{\"preload_pipelines\":[\"OCR\"],\"hotkey\":\"Ctrl+Alt+Q\"}");
        byte[] original = File.ReadAllBytes(path);
        try
        {
            MigrationResult result = ProfileMigrationClient.MigrateConfig(path);

            Assert.Equal("migrated", result.Status);
            var data = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(File.ReadAllText(path));
            Assert.Equal(ProfileMigrationClient.CurrentSchemaVersion, data!["schema_version"].GetInt32());
            Assert.Equal("Ctrl+Alt+Q", data!["hotkey"].GetString());
            Assert.True(File.Exists(result.BackupPath));
            Assert.Equal(original, File.ReadAllBytes(result.BackupPath!));
        }
        finally
        {
            File.Delete(path);
            if (File.Exists(path + ".bak")) File.Delete(path + ".bak");
        }
    }

    [Fact]
    public void SecondRunIsAlreadyMigrated()
    {
        string path = Path.Combine(Path.GetTempPath(), $"migrate-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, "{\"x\":1}");
        try
        {
            ProfileMigrationClient.MigrateConfig(path);
            byte[] snapshot = File.ReadAllBytes(path);

            MigrationResult second = ProfileMigrationClient.MigrateConfig(path);

            Assert.Equal("already_migrated", second.Status);
            Assert.Equal(snapshot, File.ReadAllBytes(path));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void MissingFileIsSkipped()
    {
        MigrationResult result = ProfileMigrationClient.MigrateConfig(Path.GetTempFileName() + ".missing");
        Assert.Equal("skipped", result.Status);
    }

    [Fact]
    public void NewerSchemaIsSkipped()
    {
        string path = Path.Combine(Path.GetTempPath(), $"migrate-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, "{\"schema_version\":99}");
        try
        {
            MigrationResult result = ProfileMigrationClient.MigrateConfig(path);
            Assert.Equal("skipped", result.Status);
            Assert.Contains("newer", result.Message);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void CorruptJsonIsSkippedWithoutWriting()
    {
        string path = Path.Combine(Path.GetTempPath(), $"migrate-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, "{ broken");
        try
        {
            MigrationResult result = ProfileMigrationClient.MigrateConfig(path);
            Assert.Equal("skipped", result.Status);
            Assert.Equal("{ broken", File.ReadAllText(path));
        }
        finally
        {
            File.Delete(path);
        }
    }
}
