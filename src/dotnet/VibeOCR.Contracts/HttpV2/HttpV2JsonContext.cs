// Source-generated JSON context for the HTTP v2 contract.
//
// Mirrors the v1 ProtocolJsonContext pattern: SnakeCaseLower naming policy at
// the context level (PascalCase C# properties -> snake_case wire), Metadata
// generation mode, and a thin HttpV2Json helper that serialises via the
// generated type info. Enum wire strings are pinned per-member in
// HttpV2Enums.cs so they are unaffected by the context policy.
using System.Text.Json;
using System.Text.Json.Serialization;

namespace VibeOCR.Contracts.HttpV2;

[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    GenerationMode = JsonSourceGenerationMode.Metadata,
    // Python to_payload() writes explicit nulls; mirror that so golden
    // payloads round-trip byte-for-byte structurally.
    DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    WriteIndented = false)]
[JsonSerializable(typeof(JobItem))]
[JsonSerializable(typeof(JobSummary))]
[JsonSerializable(typeof(StageEvent))]
[JsonSerializable(typeof(JobRef))]
[JsonSerializable(typeof(JobSnapshot))]
[JsonSerializable(typeof(PipelineSelection))]
[JsonSerializable(typeof(SubmitItem))]
[JsonSerializable(typeof(SubmitRequest))]
[JsonSerializable(typeof(ItemOutcome))]
[JsonSerializable(typeof(JobUpdate))]
[JsonSerializable(typeof(JobCommand))]
[JsonSerializable(typeof(ResultEntry))]
[JsonSerializable(typeof(PipelineSpec))]
[JsonSerializable(typeof(ResidencyEntry))]
[JsonSerializable(typeof(ResidencyStatus))]
[JsonSerializable(typeof(SettingsResidency))]
[JsonSerializable(typeof(SettingsSnapshot))]
[JsonSerializable(typeof(HttpV2ErrorPayload))]
[JsonSerializable(typeof(List<JobItem>))]
[JsonSerializable(typeof(List<SubmitItem>))]
[JsonSerializable(typeof(List<ItemOutcome>))]
[JsonSerializable(typeof(List<string>))]
[JsonSerializable(typeof(List<ResultEntry>))]
[JsonSerializable(typeof(List<ResidencyEntry>))]
[JsonSerializable(typeof(List<PipelineSpec>))]
[JsonSerializable(typeof(List<StageEvent>))]
[JsonSerializable(typeof(EventsEnvelope))]
[JsonSerializable(typeof(ResultsEnvelope))]
[JsonSerializable(typeof(CancelAck))]
public partial class HttpV2JsonContext : JsonSerializerContext;

/// <summary>Convenience helpers for v2 (de)serialisation via the source-generated context.</summary>
public static class HttpV2Json
{
    /// <summary>Serialise a v2 DTO to canonical JSON (snake_case, no indent).</summary>
    public static string Serialize<T>(T value)
        where T : class
    {
        var typeInfo = HttpV2JsonContext.Default.Options.GetTypeInfo(typeof(T));
        return JsonSerializer.Serialize(value, typeInfo);
    }

    /// <summary>Serialise using an explicit type (e.g. a base/interface).</summary>
    public static string Serialize(object? value, Type type)
    {
        var typeInfo = HttpV2JsonContext.Default.Options.GetTypeInfo(type);
        return JsonSerializer.Serialize(value, typeInfo);
    }

    /// <summary>Deserialise a v2 DTO from JSON.</summary>
    public static T? Deserialize<T>(string json)
        where T : class
    {
        var typeInfo = HttpV2JsonContext.Default.Options.GetTypeInfo(typeof(T));
        return (T?)JsonSerializer.Deserialize(json, typeInfo);
    }
}
