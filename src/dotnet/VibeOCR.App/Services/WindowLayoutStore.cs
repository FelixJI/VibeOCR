using System.IO;
using System.Text.Json;

namespace VibeOCR.App.Services;

public sealed class WindowLayoutStore
{
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);

    private readonly string _filePath;

    public WindowLayoutStore(string filePath) => _filePath = filePath;

    public WindowGeometry? Load()
    {
        if (!File.Exists(_filePath))
        {
            return null;
        }
        try
        {
            using FileStream stream = File.OpenRead(_filePath);
            return JsonSerializer.Deserialize<WindowGeometry>(stream, Options);
        }
        catch (JsonException)
        {
            return null;
        }
        catch (IOException)
        {
            return null;
        }
    }

    public void Save(WindowGeometry geometry)
    {
        string? directory = Path.GetDirectoryName(_filePath);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }
        using FileStream stream = File.Create(_filePath);
        JsonSerializer.Serialize(stream, geometry, Options);
    }
}
