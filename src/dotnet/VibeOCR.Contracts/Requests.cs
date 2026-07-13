using System.Text.Json.Serialization;

namespace VibeOCR.Contracts;

public abstract record RequestContract : IProtocolValidatable
{
    public virtual void Validate()
    {
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record HandshakeRequest : RequestContract
{
    public required string AppVersion { get; init; }
    public required int ProtocolVersion { get; init; }
    public long? MaxMessageBytes { get; init; }
    public long? MaxSharedPayloadBytes { get; init; }

    public override void Validate()
    {
        ContractValidation.NonEmpty(AppVersion, nameof(AppVersion));
        ContractValidation.Version(ProtocolVersion);
        if (MaxMessageBytes is < 1024 || MaxSharedPayloadBytes is < 0)
        {
            throw new ProtocolContractException("Handshake payload limits are invalid.");
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record PingRequest : RequestContract
{
    public required string Nonce { get; init; }
    public override void Validate() => ContractValidation.NonEmpty(Nonce, nameof(Nonce));
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ShutdownRequest : RequestContract
{
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Reason { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record CancelRequest : RequestContract
{
    public required Guid TaskId { get; init; }
    public override void Validate() => ContractValidation.Version4Uuid(TaskId, nameof(TaskId));
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ReleaseMemoryRequest : RequestContract
{
    public required string Name { get; init; }
    public override void Validate() => SharedPayloadRef.ValidateName(Name);
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RecognizeRequest : RequestContract
{
    public required SharedPayloadRef Image { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Pipeline { get; init; }

    public string? Language { get; init; }

    public override void Validate()
    {
        Image.Validate();
        if (Pipeline is not null)
        {
            ContractValidation.OneOf(
                Pipeline,
                nameof(Pipeline),
                "OCR",
                "TABLE_RECOGNITION",
                "FORMULA_RECOGNITION");
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ExportOcrRequest : RequestContract
{
    public required string RawText { get; init; }
    public required string MarkdownText { get; init; }
    public required string HtmlText { get; init; }
    public required System.Text.Json.JsonElement[] RawBlocks { get; init; }
    public required string OutputPath { get; init; }
    public required string Format { get; init; }
    public required bool Overwrite { get; init; }

    public override void Validate()
    {
        ContractValidation.NonEmpty(OutputPath, nameof(OutputPath));
        ContractValidation.OneOf(Format, nameof(Format), "txt", "markdown", "html");
        if (!Path.IsPathFullyQualified(OutputPath))
        {
            throw new ProtocolContractException("output_path must be absolute.");
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record OpenPdfRequest : RequestContract
{
    public required string FilePath { get; init; }
    public override void Validate() => ContractValidation.NonEmpty(FilePath, nameof(FilePath));
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record DecodeQrCodeRequest : RequestContract
{
    public required SharedPayloadRef Image { get; init; }
    public override void Validate() => Image.Validate();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record GenerateQrCodeRequest : RequestContract
{
    public required string Data { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Format { get; init; }

    public override void Validate()
    {
        ContractValidation.NonEmpty(Data, nameof(Data));
        if (Format is not null)
        {
            ContractValidation.OneOf(Format, nameof(Format), "qrcode", "barcode");
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SettingsSnapshotRequest : RequestContract;
