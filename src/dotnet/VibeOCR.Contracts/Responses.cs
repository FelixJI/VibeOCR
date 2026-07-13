using System.Text.Json;
using System.Text.Json.Serialization;

namespace VibeOCR.Contracts;

public abstract record ResponseContract : IProtocolValidatable
{
    public virtual void Validate()
    {
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record HandshakeResponse : ResponseContract
{
    public required string WorkerVersion { get; init; }
    public required int ProtocolVersion { get; init; }
    public required string[] Capabilities { get; init; }
    public required string PythonVersion { get; init; }
    public required string Backend { get; init; }
    public required long MaxMessageBytes { get; init; }
    public required long MaxSharedPayloadBytes { get; init; }

    public override void Validate()
    {
        ContractValidation.NonEmpty(WorkerVersion, nameof(WorkerVersion));
        ContractValidation.Version(ProtocolVersion);
        ContractValidation.NonEmpty(PythonVersion, nameof(PythonVersion));
        ContractValidation.OneOf(Backend, nameof(Backend), "cpu", "gpu");
        if (MaxMessageBytes < 1024 || MaxSharedPayloadBytes < 0)
        {
            throw new ProtocolContractException("Handshake response limits are invalid.");
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record PingResponse : ResponseContract
{
    public required string Nonce { get; init; }
    public override void Validate() => ContractValidation.NonEmpty(Nonce, nameof(Nonce));
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ShutdownResponse : ResponseContract
{
    public required bool Acknowledged { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record CancelResponse : ResponseContract
{
    public required bool Accepted { get; init; }
    public required string State { get; init; }
    public override void Validate() => ContractValidation.OneOf(
        State,
        nameof(State),
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "unknown");
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ReleaseMemoryResponse : ResponseContract
{
    public required bool Released { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RecognizeResponse : ResponseContract
{
    public required string Text { get; init; }
    public required string Pipeline { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement[]? RawBlocks { get; init; }

    public override void Validate() => ContractValidation.OneOf(
        Pipeline,
        nameof(Pipeline),
        "OCR",
        "TABLE_RECOGNITION",
        "FORMULA_RECOGNITION");
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record OpenPdfResponse : ResponseContract
{
    public required string SessionId { get; init; }
    public required string FilePath { get; init; }
    public required int PageCount { get; init; }

    public override void Validate()
    {
        ContractValidation.NonEmpty(SessionId, nameof(SessionId));
        ContractValidation.NonEmpty(FilePath, nameof(FilePath));
        if (PageCount < 0)
        {
            throw new ProtocolContractException("page_count must be non-negative.");
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record QrCodeResult : IProtocolValidatable
{
    public required string Data { get; init; }
    public required string Format { get; init; }

    public void Validate() => ContractValidation.NonEmpty(Format, nameof(Format));
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record DecodeQrCodeResponse : ResponseContract
{
    public required QrCodeResult[] Codes { get; init; }

    public override void Validate()
    {
        foreach (QrCodeResult code in Codes)
        {
            code.Validate();
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record GenerateQrCodeResponse : ResponseContract
{
    public required SharedPayloadRef Image { get; init; }
    public override void Validate() => Image.Validate();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SettingsSnapshotResponse : ResponseContract
{
    public required string Backend { get; init; }
    public required string[] PreloadPipelines { get; init; }
    public required int TtlSeconds { get; init; }

    public override void Validate()
    {
        ContractValidation.OneOf(Backend, nameof(Backend), "cpu", "gpu");
        if (TtlSeconds < 0)
        {
            throw new ProtocolContractException("ttl_seconds must be non-negative.");
        }

        foreach (string pipeline in PreloadPipelines)
        {
            ContractValidation.NonEmpty(pipeline, nameof(PreloadPipelines));
        }
    }
}
