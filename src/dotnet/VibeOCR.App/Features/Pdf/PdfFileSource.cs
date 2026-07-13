using Windows.Storage;
using Windows.Storage.Pickers;

namespace VibeOCR.App.Features.Pdf;

/// <summary>WinUI file picker for PDF files.</summary>
public sealed class PdfFileSource(Func<nint> windowHandle) : IPdfFileSource
{
    public async Task<string?> PickFileAsync(CancellationToken cancellationToken)
    {
        var picker = new FileOpenPicker
        {
            SuggestedStartLocation = PickerLocationId.DocumentsLibrary,
            ViewMode = PickerViewMode.Thumbnail,
        };
        picker.FileTypeFilter.Add(".pdf");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle());
        StorageFile? file = await picker.PickSingleFileAsync();
        cancellationToken.ThrowIfCancellationRequested();
        return file?.Path;
    }
}
