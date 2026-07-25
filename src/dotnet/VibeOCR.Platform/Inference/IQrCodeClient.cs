// Phase 7B step ④: QR decode/generate client surface for the v2 supervisor.
//
// QR decode/generate are NOT recognition jobs (plan §7B audit), so they get
// their own seam mirroring IInferenceClient. The supervisor exposes
// /v2/qrcode/decode and /v2/qrcode/generate; this interface is the .NET
// counterpart. It lets QrCodeViewModel migrate to v2 like the other 4
// ViewModels, unblocking the 5/5 default-switch goal.
using VibeOCR.Contracts.HttpV2;

namespace VibeOCR.Platform.Inference;

/// <summary>One decoded QR/barcode result.</summary>
public sealed record QrCodeDecodedItem(string Data, string Format, bool IsUrl);

/// <summary>Result of a QR/barcode generation request.</summary>
public sealed record QrCodeGeneratedImage(string Base64Png, string MediaType);

/// <summary>
/// Transport-neutral QR client used by QrCodeViewModel. Mirrors the v2
/// supervisor's /v2/qrcode/decode and /v2/qrcode/generate endpoints.
/// </summary>
public interface IQrCodeClient : IAsyncDisposable
{
    /// <summary>Decode QR/barcode(s) from a base64-encoded image.</summary>
    Task<IReadOnlyList<QrCodeDecodedItem>> DecodeAsync(string base64Image, CancellationToken cancellationToken);

    /// <summary>Generate a QR/barcode image from text. Returns base64-encoded PNG.</summary>
    Task<QrCodeGeneratedImage> GenerateAsync(string data, string format, CancellationToken cancellationToken);
}
