using Windows.Storage;
using Windows.Storage.Pickers;

namespace VibeOCR.App.Features.Batch;

public interface IBatchFileSource
{
    Task<IReadOnlyList<string>> PickFilesAsync(CancellationToken cancellationToken);
    Task<(byte[] Data, string MediaType)> ReadAsync(string path, CancellationToken cancellationToken);
}

public sealed class BatchFileSource(Func<nint> windowHandle) : IBatchFileSource
{
    private const long MaximumInputBytes = 256L << 20;

    public async Task<IReadOnlyList<string>> PickFilesAsync(CancellationToken cancellationToken)
    {
        var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.PicturesLibrary, ViewMode = PickerViewMode.Thumbnail };
        foreach (string extension in BatchCommands.Extensions) picker.FileTypeFilter.Add(extension);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle());
        IReadOnlyList<StorageFile> files = await picker.PickMultipleFilesAsync();
        cancellationToken.ThrowIfCancellationRequested();
        return files.Select(file => file.Path).ToArray();
    }

    public async Task<(byte[] Data, string MediaType)> ReadAsync(string path, CancellationToken cancellationToken)
    {
        var info = new FileInfo(path);
        if (!info.Exists) throw new FileNotFoundException("Batch input was not found.", path);
        if (info.Length > MaximumInputBytes) throw new InvalidDataException("Batch input exceeds 256 MiB.");
        byte[] data = await File.ReadAllBytesAsync(info.FullName, cancellationToken);
        return (data, BatchCommands.MediaType(info.Extension));
    }
}

public static class BatchCommands
{
    public static IReadOnlyList<string> Extensions { get; } = [".png", ".jpg", ".jpeg", ".bmp", ".webp"];
    public static string MediaType(string extension) => extension.ToLowerInvariant() switch
    {
        ".png" => "image/png", ".jpg" or ".jpeg" => "image/jpeg", ".bmp" => "image/bmp", ".webp" => "image/webp",
        _ => throw new InvalidDataException("Unsupported batch image format."),
    };

    public static string UniqueOutputPath(string directory, string sourcePath, string format, ISet<string> reserved)
    {
        string extension = format switch { "markdown" => ".md", "html" => ".html", _ => ".txt" };
        string stem = Path.GetFileNameWithoutExtension(sourcePath);
        string candidate = Path.Combine(directory, stem + extension);
        for (int suffix = 1; File.Exists(candidate) || !reserved.Add(candidate); suffix++) candidate = Path.Combine(directory, $"{stem}_{suffix}{extension}");
        return candidate;
    }
}
