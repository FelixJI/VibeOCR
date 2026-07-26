// Standalone DTOs that were originally in VibeOCR.Contracts v1 protocol.
// Moved here so the v1 Contracts files can be deleted. These are simple
// data carriers — no protocol envelope, no IProtocolValidatable.
namespace VibeOCR.App;

public sealed record RecognizeResponse
{
    public required string Text { get; init; }
    public string Pipeline { get; init; } = "OCR";
    public string? RawText { get; init; }
    public string? MarkdownText { get; init; }
    public string? HtmlText { get; init; }
    public System.Text.Json.JsonElement[]? RawBlocks { get; init; }
}

public sealed record QrCodeResult
{
    public string Data { get; init; } = "";
    public string Format { get; init; } = "";
    public bool? IsUrl { get; init; }
}

public static class ProtocolConstants
{
    public const int Version = 2;
}
