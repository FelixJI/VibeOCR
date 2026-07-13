using System.Buffers.Binary;
using System.Runtime.InteropServices;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.Storage.Streams;
using VibeOCR.Platform.Windows;

namespace VibeOCR.App.Features.Recognition;

public sealed record RecognitionInput(
    byte[] Data,
    string MediaType,
    string DisplayName,
    string Origin);

public interface IInputService
{
    Task<RecognitionInput?> PickFileAsync(CancellationToken cancellationToken);
    Task<RecognitionInput?> ReadClipboardAsync(CancellationToken cancellationToken);
    Task<RecognitionInput?> CaptureScreenAsync(CancellationToken cancellationToken);
    Task<RecognitionInput?> ReadDroppedFileAsync(string path, CancellationToken cancellationToken);
}

public sealed class InputService(Func<nint> windowHandle) : IInputService
{
    private const long MaximumInputBytes = 256L << 20;

    public async Task<RecognitionInput?> PickFileAsync(CancellationToken cancellationToken)
    {
        var picker = new FileOpenPicker
        {
            SuggestedStartLocation = PickerLocationId.PicturesLibrary,
            ViewMode = PickerViewMode.Thumbnail,
        };
        picker.FileTypeFilter.Add(".png");
        picker.FileTypeFilter.Add(".jpg");
        picker.FileTypeFilter.Add(".jpeg");
        picker.FileTypeFilter.Add(".bmp");
        picker.FileTypeFilter.Add(".webp");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle());
        StorageFile? file = await picker.PickSingleFileAsync();
        cancellationToken.ThrowIfCancellationRequested();
        return file is null ? null : await ReadFileAsync(file.Path, "file", cancellationToken);
    }

    public async Task<RecognitionInput?> ReadClipboardAsync(CancellationToken cancellationToken)
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
        return new RecognitionInput(
            data,
            string.IsNullOrWhiteSpace(stream.ContentType) ? "image/png" : stream.ContentType,
            "clipboard",
            "clipboard");
    }

    public async Task<RecognitionInput?> CaptureScreenAsync(CancellationToken cancellationToken)
    {
        int x = GetSystemMetrics(76);
        int y = GetSystemMetrics(77);
        int width = GetSystemMetrics(78);
        int height = GetSystemMetrics(79);
        cancellationToken.ThrowIfCancellationRequested();
        if (width <= 0 || height <= 0 || checked((long)width * height * 4) > MaximumInputBytes)
        {
            throw new InvalidDataException("Virtual desktop capture exceeds 256 MiB.");
        }

        await using var capture = new ScreenCaptureService(Guid.NewGuid());
        CapturedFrame frame = capture.Capture(
            new PhysicalRectangle(x, y, width, height),
            TimeSpan.FromMinutes(1));
        byte[] bgra = capture.Read(frame);
        return new RecognitionInput(
            EncodeTopDownBmp(bgra, width, height, frame.Stride),
            "image/bmp",
            "screenshot.bmp",
            "screenshot");
    }

    public Task<RecognitionInput?> ReadDroppedFileAsync(
        string path,
        CancellationToken cancellationToken) => ReadFileAsync(path, "drop", cancellationToken);

    private static async Task<RecognitionInput?> ReadFileAsync(
        string path,
        string origin,
        CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        var info = new FileInfo(path);
        if (!info.Exists)
        {
            throw new FileNotFoundException("Input image was not found.", path);
        }

        if (info.Length > MaximumInputBytes)
        {
            throw new InvalidDataException("Input image exceeds 256 MiB.");
        }

        byte[] data = await File.ReadAllBytesAsync(path, cancellationToken);
        return new RecognitionInput(data, MediaType(path), info.Name, origin);
    }

    private static string MediaType(string path) => Path.GetExtension(path).ToLowerInvariant() switch
    {
        ".png" => "image/png",
        ".jpg" or ".jpeg" => "image/jpeg",
        ".bmp" => "image/bmp",
        ".webp" => "image/webp",
        _ => throw new InvalidDataException("Unsupported image format."),
    };

    private static byte[] EncodeTopDownBmp(byte[] bgra, int width, int height, int stride)
    {
        int pixelBytes = checked(stride * height);
        if (stride != checked(width * 4) || bgra.Length != pixelBytes)
        {
            throw new InvalidDataException("Invalid BGRA capture buffer.");
        }

        byte[] bmp = new byte[checked(54 + pixelBytes)];
        bmp[0] = (byte)'B';
        bmp[1] = (byte)'M';
        BinaryPrimitives.WriteInt32LittleEndian(bmp.AsSpan(2), bmp.Length);
        BinaryPrimitives.WriteInt32LittleEndian(bmp.AsSpan(10), 54);
        BinaryPrimitives.WriteInt32LittleEndian(bmp.AsSpan(14), 40);
        BinaryPrimitives.WriteInt32LittleEndian(bmp.AsSpan(18), width);
        BinaryPrimitives.WriteInt32LittleEndian(bmp.AsSpan(22), -height);
        BinaryPrimitives.WriteInt16LittleEndian(bmp.AsSpan(26), 1);
        BinaryPrimitives.WriteInt16LittleEndian(bmp.AsSpan(28), 32);
        BinaryPrimitives.WriteInt32LittleEndian(bmp.AsSpan(34), pixelBytes);
        bgra.CopyTo(bmp, 54);
        return bmp;
    }

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);
}
