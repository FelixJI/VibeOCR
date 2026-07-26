using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.Storage.Streams;

namespace VibeOCR.App.Features.QrCode;

/// <summary>
/// Platform glue for the QR tab: reads images from the file picker/clipboard/drop,
/// and the generated image bytes are read back from shared memory for saving.
/// Mirrors the Recognition input service and the Batch file source.
/// </summary>
public sealed class QrCodeInputService(Func<nint> windowHandle) : IQrCodeInput
{
    private const long MaximumInputBytes = 256L << 20;
    private static readonly string[] SupportedExtensions =
        [".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"];

    public async Task<QrCodeInput?> PickFileAsync(CancellationToken cancellationToken)
    {
        var picker = new FileOpenPicker
        {
            SuggestedStartLocation = PickerLocationId.PicturesLibrary,
            ViewMode = PickerViewMode.Thumbnail,
        };
        foreach (string extension in SupportedExtensions) picker.FileTypeFilter.Add(extension);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle());
        StorageFile? file = await picker.PickSingleFileAsync();
        cancellationToken.ThrowIfCancellationRequested();
        return file is null ? null : await ReadFileAsync(file.Path, cancellationToken);
    }

    public async Task<QrCodeInput?> ReadClipboardAsync(CancellationToken cancellationToken)
    {
        DataPackageView content = Clipboard.GetContent();
        if (!content.Contains(StandardDataFormats.Bitmap))
        {
            throw new InvalidDataException("Clipboard does not contain an image.");
        }
        RandomAccessStreamReference reference = await content.GetBitmapAsync();
        using IRandomAccessStreamWithContentType stream = await reference.OpenReadAsync();
        cancellationToken.ThrowIfCancellationRequested();
        if (stream.Size > MaximumInputBytes)
        {
            throw new InvalidDataException("Clipboard image exceeds 256 MiB.");
        }
        using var reader = new DataReader(stream.GetInputStreamAt(0));
        uint size = checked((uint)stream.Size);
        await reader.LoadAsync(size);
        byte[] data = new byte[size];
        reader.ReadBytes(data);
        return new QrCodeInput(
            data,
            string.IsNullOrWhiteSpace(stream.ContentType) ? "image/png" : stream.ContentType,
            "clipboard");
    }

    public static async Task<QrCodeInput?> ReadFileAsync(string path, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        var info = new FileInfo(path);
        if (!info.Exists) throw new FileNotFoundException("Input image was not found.", path);
        if (info.Length > MaximumInputBytes) throw new InvalidDataException("Input image exceeds 256 MiB.");
        byte[] data = await File.ReadAllBytesAsync(path, cancellationToken);
        return new QrCodeInput(data, MediaType(path), info.Name);
    }

    Task<QrCodeInput?> IQrCodeInput.ReadDroppedFileAsync(string path, CancellationToken cancellationToken)
        => ReadFileAsync(path, cancellationToken);

    private static string MediaType(string path) => Path.GetExtension(path).ToLowerInvariant() switch
    {
        ".png" => "image/png",
        ".jpg" or ".jpeg" => "image/jpeg",
        ".bmp" => "image/bmp",
        ".webp" => "image/webp",
        ".gif" => "image/gif",
        ".tif" or ".tiff" => "image/tiff",
        _ => "application/octet-stream",
    };
}

/// <summary>Platform abstraction for saving the generated image.</summary>
public interface IQrCodeSavePlatform
{
    Task<string?> PickSavePathAsync(string suggestedName, CancellationToken cancellationToken);
    Task<bool> ConfirmOverwriteAsync(string path, CancellationToken cancellationToken);
    Task WriteFileAsync(string path, byte[] data, CancellationToken cancellationToken);
}

/// <summary>
/// Save commands: resolve the generated image bytes from shared memory and write them
/// to a user-chosen path. Overwrite confirmation mirrors the recognition export flow.
/// </summary>
public sealed class QrCodeSaveCommands(IQrCodeSavePlatform platform)
{
    public async Task<bool> SaveAsync(string base64Image, string suggestedName, CancellationToken cancellationToken)
    {
        if (string.IsNullOrEmpty(base64Image)) return false;
        string? path = await platform.PickSavePathAsync(suggestedName, cancellationToken);
        if (path is null) return false;
        if (File.Exists(path) && !await platform.ConfirmOverwriteAsync(path, cancellationToken)) return false;
        byte[] data = Convert.FromBase64String(base64Image);
        await platform.WriteFileAsync(path, data, cancellationToken);
        return true;
    }
}

/// <summary>WinUI implementation of <see cref="IQrCodeSavePlatform"/> using FileSavePicker.</summary>
public sealed class QrCodeSavePlatform(Func<nint> windowHandle) : IQrCodeSavePlatform
{
    public async Task<string?> PickSavePathAsync(string suggestedName, CancellationToken cancellationToken)
    {
        var picker = new FileSavePicker { SuggestedStartLocation = PickerLocationId.PicturesLibrary };
        picker.FileTypeChoices.Add("PNG", [".png"]);
        picker.FileTypeChoices.Add("JPG", [".jpg"]);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle());
        StorageFile? file = await picker.PickSaveFileAsync();
        cancellationToken.ThrowIfCancellationRequested();
        return file?.Path;
    }

    public Task<bool> ConfirmOverwriteAsync(string path, CancellationToken cancellationToken)
    {
        // WinUI's FileSavePicker already prompts on overwrite when the file exists.
        return Task.FromResult(true);
    }

    public async Task WriteFileAsync(string path, byte[] data, CancellationToken cancellationToken)
    {
        string extension = Path.GetExtension(path).ToLowerInvariant();
        await File.WriteAllBytesAsync(path, data, cancellationToken);
    }
}

