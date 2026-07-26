// HTTP v2 DTO records mirroring the Python vibeocr.protocol.v2 dataclasses.
// Field order and nesting follow each to_payload() method in dtos.py exactly.
using System.Text.Json;
using System.Text.Json.Serialization;

namespace VibeOCR.Contracts.HttpV2;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record JobItem
{
    public required string ItemId { get; init; }
    public required string DisplayName { get; init; }
    public required ItemState State { get; init; }
    public int Attempt { get; init; }
    public string? Error { get; init; }
    public string? ClientItemKey { get; init; }
    public int Ordinal { get; init; }
    public string? SourceItemId { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record JobSummary
{
    public int Succeeded { get; init; }
    public int Failed { get; init; }
    public int Cancelled { get; init; }
    public int Total { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record StageEvent
{
    public required int Sequence { get; init; }
    public required string Stage { get; init; }
    public string? ItemId { get; init; }
    public string? Timestamp { get; init; }
    /// <summary>Arbitrary event detail. Defaults to an empty object on the wire.</summary>
    public IDictionary<string, JsonElement>? Detail { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record JobRef
{
    public required string JobId { get; init; }
    public int SchemaVersion { get; init; } = HttpV2Schema.Version;
    public string? InstanceId { get; init; }
    public JobState State { get; init; } = JobState.Accepted;
    public IReadOnlyList<JobItem> Items { get; init; } = Array.Empty<JobItem>();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record JobSnapshot
{
    public required string JobId { get; init; }
    public required JobKind Kind { get; init; }
    public required JobPriority Priority { get; init; }
    public required JobState State { get; init; }
    public int SchemaVersion { get; init; } = HttpV2Schema.Version;
    public string? InstanceId { get; init; }
    public string? CreatedAt { get; init; }
    public string? StartedAt { get; init; }
    public string? FinishedAt { get; init; }
    public string? Stage { get; init; }
    public int ProgressCurrent { get; init; }
    public int ProgressTotal { get; init; }
    public IReadOnlyList<JobItem> Items { get; init; } = Array.Empty<JobItem>();
    public JobSummary Summary { get; init; } = new();
    public string? CancelRequestedAt { get; init; }
    public CancelMode? CancelMode { get; init; }
    public bool Degraded { get; init; }
    public int EventSequence { get; init; }
    public bool ResultAvailable { get; init; }
    public string? RequestId { get; init; }
    public string? SourceJobId { get; init; }
    public PipelineSelection? Pipeline { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record PipelineSelection
{
    public required string PipelineId { get; init; }
    public int OptionsVersion { get; init; } = 1;
    public IDictionary<string, JsonElement> Options { get; init; } =
        new Dictionary<string, JsonElement>();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SubmitItem
{
    public required string ClientItemKey { get; init; }
    public required int Ordinal { get; init; }
    public required string DisplayName { get; init; }
    public required IDictionary<string, JsonElement> Source { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SubmitRequest
{
    public required string RequestId { get; init; }
    public required JobKind Kind { get; init; }
    public required JobPriority Priority { get; init; }
    public required PipelineSelection Pipeline { get; init; }
    public required IReadOnlyList<SubmitItem> Items { get; init; }
    public int SchemaVersion { get; init; } = HttpV2Schema.Version;
    public IDictionary<string, JsonElement> Parameters { get; init; } =
        new Dictionary<string, JsonElement>();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ItemOutcome
{
    public required string ItemId { get; init; }
    public required ItemState State { get; init; }
    public required int Attempt { get; init; }
    public string? PayloadType { get; init; }
    public IDictionary<string, JsonElement>? Payload { get; init; }
    public string? ErrorCode { get; init; }
    public IDictionary<string, JsonElement> ErrorDetail { get; init; } =
        new Dictionary<string, JsonElement>();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record JobUpdate
{
    public required JobSnapshot Snapshot { get; init; }
    public required IReadOnlyList<StageEvent> Events { get; init; }
    public required IReadOnlyList<ItemOutcome> Outcomes { get; init; }
    public required int ThroughSequence { get; init; }
    public bool More { get; init; }
    public int SchemaVersion { get; init; } = HttpV2Schema.Version;
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record JobCommand
{
    public required string CommandId { get; init; }
    public required JobCommandKind Kind { get; init; }
    public required string JobId { get; init; }
    public IReadOnlyList<string> ItemIds { get; init; } = Array.Empty<string>();
    public JobPriority? PriorityOverride { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ResultEntry
{
    public required string ItemId { get; init; }
    public required string DisplayName { get; init; }
    /// <summary>Free-form result payload. Defaults to an empty object on the wire.</summary>
    public IDictionary<string, JsonElement> Payload { get; init; } = new Dictionary<string, JsonElement>();
    public string? ErrorCode { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record PipelineSpec
{
    public required string Name { get; init; }
    public int? TtlSeconds { get; init; }
    public bool Pinned { get; init; }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ResidencyEntry
{
    public required string Pipeline { get; init; }
    public required ResidencyKind Kind { get; init; }
    public int ActiveLeases { get; init; }
    public int? RemainingTtlSeconds { get; init; }
    public int? EstimatedVramMb { get; init; }
    public EvictionReason EvictionReason { get; init; } = EvictionReason.None;
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ResidencyStatus
{
    public int SchemaVersion { get; init; } = HttpV2Schema.Version;
    public int DefaultTtlSeconds { get; init; } = 300;
    public IReadOnlyList<ResidencyEntry> Entries { get; init; } = Array.Empty<ResidencyEntry>();
    public IReadOnlyList<PipelineSpec> Pipelines { get; init; } = Array.Empty<PipelineSpec>();
    public int? VramTotalMb { get; init; }
    public int? VramUsedMb { get; init; }
}

/// <summary>
/// The nested ``residency`` object inside SettingsSnapshot. Kept as its own
/// record so the wire shape exactly matches the Python to_payload() nesting.
/// </summary>
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SettingsResidency
{
    public int DefaultTtlSeconds { get; init; } = 300;
    public IReadOnlyList<PipelineSpec> Pipelines { get; init; } = Array.Empty<PipelineSpec>();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SettingsSnapshot
{
    public int SchemaVersion { get; init; } = HttpV2Schema.Version;
    public SettingsResidency Residency { get; init; } = new();
    /// <summary>Extra backend settings (transport-neutral key/value bag).</summary>
    public IDictionary<string, JsonElement> Extra { get; init; } = new Dictionary<string, JsonElement>();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record HttpV2ErrorPayload
{
    public required int SchemaVersion { get; init; }
    public string? InstanceId { get; init; }
    public required HttpV2ErrorCode Code { get; init; }
    public required string Message { get; init; }
    public required ErrorCategory Category { get; init; }
    public required bool Retryable { get; init; }
    /// <summary>Typed error detail. Defaults to an empty object on the wire.</summary>
    public IDictionary<string, JsonElement> Detail { get; init; } = new Dictionary<string, JsonElement>();
    public string? JobId { get; init; }
}
