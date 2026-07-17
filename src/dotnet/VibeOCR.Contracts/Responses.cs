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

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? MarkdownText { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? HtmlText { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? RawText { get; init; }

    // text_blocks: serialized TextBlock list [{text, bbox, confidence, order, ...}].
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement[]? TextBlocks { get; init; }

    // text_with_scores: [[text, score], ...] 2-element pairs.
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement[]? TextWithScores { get; init; }

    // content_list: structured content (MinerU/table/formula pipelines).
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement[]? ContentList { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? ImageWidth { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? ImageHeight { get; init; }

    public override void Validate() => ContractValidation.OneOf(
        Pipeline,
        nameof(Pipeline),
        "OCR",
        "PP-StructureV3",
        "MinerU",
        "PaddleOCR-VL",
        "TABLE_RECOGNITION",
        "FORMULA_RECOGNITION");
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RecognizeBatchResponse : ResponseContract
{
    // 元素可空：批处理中单个槽位失败时为 null（与 golden.json fixture 及
    // methods.schema.json 的 anyOf [..., {type:null}] 一致）。
    public required RecognizeResponse?[] Results { get; init; }

    public override void Validate()
    {
        foreach (RecognizeResponse? result in Results)
        {
            result?.Validate();
        }
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ExportOcrResponse : ResponseContract
{
    public required string OutputPath { get; init; }
    public required long BytesWritten { get; init; }
    public override void Validate()
    {
        ContractValidation.NonEmpty(OutputPath, nameof(OutputPath));
        if (BytesWritten < 0) throw new ProtocolContractException("bytes_written must be non-negative.");
    }
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
public sealed record ClosePdfResponse : ResponseContract
{
    public required bool Closed { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record PdfCommandResponse : ResponseContract
{
    public required JsonElement Result { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RenderPdfPageResponse : ResponseContract
{
    public required SharedPayloadRef Image { get; init; }
    public override void Validate() => Image.Validate();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RotatePdfResponse : ResponseContract
{
    public required int PageCount { get; init; }
    public override void Validate()
    {
        if (PageCount < 0) throw new ProtocolContractException("page_count must be non-negative.");
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record DeletePdfPagesResponse : ResponseContract
{
    public required int PageCount { get; init; }
    public override void Validate()
    {
        if (PageCount < 0) throw new ProtocolContractException("page_count must be non-negative.");
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record AddPdfTextLayerResponse : ResponseContract
{
    public required bool Written { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? Saved { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record DeletePdfTextLayersResponse : ResponseContract
{
    public required int DeletedCount { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int[]? ResidualPages { get; init; }
    public override void Validate()
    {
        if (DeletedCount < 0) throw new ProtocolContractException("deleted_count must be non-negative.");
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SavePdfResponse : ResponseContract
{
    public required string SavedPath { get; init; }
    public override void Validate() => ContractValidation.NonEmpty(SavedPath, nameof(SavedPath));
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record StartPdfOcrResponse : ResponseContract
{
    public required int Completed { get; init; }
    public required int Failed { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? Cancelled { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? Compressed { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string[]? WriteErrors { get; init; }
    public override void Validate()
    {
        if (Completed < 0) throw new ProtocolContractException("completed must be non-negative.");
        if (Failed < 0) throw new ProtocolContractException("failed must be non-negative.");
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record QrCodeResult : IProtocolValidatable
{
    public required string Data { get; init; }
    public required string Format { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? IsUrl { get; init; }

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
public sealed record GenerateQrCodeSvgResponse : ResponseContract
{
    public required string Svg { get; init; }
    public override void Validate() => ContractValidation.NonEmpty(Svg, nameof(Svg));
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

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SwitchBackendResponse : ResponseContract
{
    public required string Backend { get; init; }
    public required bool RestartRequired { get; init; }
    public override void Validate() => ContractValidation.OneOf(Backend, nameof(Backend), "cpu", "gpu");
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record InstallDependencyResponse : ResponseContract
{
    public required bool Installed { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? Restarted { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Name { get; init; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Source { get; init; }
}
