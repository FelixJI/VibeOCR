// HTTP v2 inference supervisor contract DTOs (.NET mirror of the Python
// vibeocr.protocol.v2 package). These types are the wire contract shared with
// PySide (Python) and, eventually, the WinUI front-end. They MUST stay in
// lock-step with packages/vibeocr-contracts-py/src/vibeocr/runtime_contracts/.
//
// Wire conventions (see specs/2026-07-24-inference-supervisor-adr.md):
//   * schema_version is always 2 on the wire.
//   * Field names are snake_case; the HttpV2JsonContext applies
//     JsonKnownNamingPolicy.SnakeCaseLower so PascalCase C# properties map
//     automatically (job_id, schema_version, progress_current, ...).
//   * Enums carry explicit [JsonStringEnumMemberName] on every member so the
//     wire string is unambiguous regardless of the context naming policy
//     (lowercase snake for state/kind/priority/mode; SCREAMING_SNAKE for
//     ErrorCode, which must NOT be lower-cased).
using System.Text.Json.Serialization;

namespace VibeOCR.Contracts.HttpV2;

/// <summary>Wire schema major version for the v2 protocol.</summary>
public static class HttpV2Schema
{
    public const int Version = 2;
}

[JsonConverter(typeof(JsonStringEnumConverter<JobState>))]
public enum JobState
{
    [JsonStringEnumMemberName("accepted")] Accepted,
    [JsonStringEnumMemberName("queued")] Queued,
    [JsonStringEnumMemberName("running")] Running,
    [JsonStringEnumMemberName("completed")] Completed,
    [JsonStringEnumMemberName("completed_with_errors")] CompletedWithErrors,
    [JsonStringEnumMemberName("cancel_requested")] CancelRequested,
    [JsonStringEnumMemberName("cancelled")] Cancelled,
    [JsonStringEnumMemberName("failed")] Failed,
}

[JsonConverter(typeof(JsonStringEnumConverter<ItemState>))]
public enum ItemState
{
    [JsonStringEnumMemberName("queued")] Queued,
    [JsonStringEnumMemberName("running")] Running,
    [JsonStringEnumMemberName("succeeded")] Succeeded,
    [JsonStringEnumMemberName("failed")] Failed,
    [JsonStringEnumMemberName("cancelled")] Cancelled,
}

[JsonConverter(typeof(JsonStringEnumConverter<JobKind>))]
public enum JobKind
{
    [JsonStringEnumMemberName("recognition")] Recognition,
    [JsonStringEnumMemberName("pdf_ocr")] PdfOcr,
    [JsonStringEnumMemberName("mineru_parse")] MineruParse,
    [JsonStringEnumMemberName("model_download")] ModelDownload,
    [JsonStringEnumMemberName("settings_install")] SettingsInstall,
}

[JsonConverter(typeof(JsonStringEnumConverter<JobPriority>))]
public enum JobPriority
{
    [JsonStringEnumMemberName("interactive")] Interactive,
    [JsonStringEnumMemberName("background")] Background,
}

[JsonConverter(typeof(JsonStringEnumConverter<JobCommandKind>))]
public enum JobCommandKind
{
    [JsonStringEnumMemberName("cancel")] Cancel,
    [JsonStringEnumMemberName("retry")] Retry,
    [JsonStringEnumMemberName("forget")] Forget,
}

[JsonConverter(typeof(JsonStringEnumConverter<CancelMode>))]
public enum CancelMode
{
    [JsonStringEnumMemberName("queued_only")] QueuedOnly,
    [JsonStringEnumMemberName("cooperative")] Cooperative,
    [JsonStringEnumMemberName("forced")] Forced,
}

[JsonConverter(typeof(JsonStringEnumConverter<ResidencyKind>))]
public enum ResidencyKind
{
    [JsonStringEnumMemberName("soft_ttl")] SoftTtl,
    [JsonStringEnumMemberName("pinned")] Pinned,
    [JsonStringEnumMemberName("idle")] Idle,
    [JsonStringEnumMemberName("evicted")] Evicted,
}

[JsonConverter(typeof(JsonStringEnumConverter<EvictionReason>))]
public enum EvictionReason
{
    [JsonStringEnumMemberName("none")] None,
    [JsonStringEnumMemberName("ttl_expired")] TtlExpired,
    [JsonStringEnumMemberName("vram_pressure")] VramPressure,
    [JsonStringEnumMemberName("explicit_release")] ExplicitRelease,
    [JsonStringEnumMemberName("supervisor_shutdown")] SupervisorShutdown,
}

/// <summary>Typed error categories for the v2 protocol (errors.json categories).</summary>
[JsonConverter(typeof(JsonStringEnumConverter<ErrorCategory>))]
public enum ErrorCategory
{
    [JsonStringEnumMemberName("validation")] Validation,
    [JsonStringEnumMemberName("auth")] Auth,
    [JsonStringEnumMemberName("not_found")] NotFound,
    [JsonStringEnumMemberName("conflict")] Conflict,
    [JsonStringEnumMemberName("cancelled")] Cancelled,
    [JsonStringEnumMemberName("oom")] Oom,
    [JsonStringEnumMemberName("transient")] Transient,
    [JsonStringEnumMemberName("backend_unavailable")] BackendUnavailable,
    [JsonStringEnumMemberName("internal")] Internal,
}

/// <summary>
/// Typed error codes for the v2 protocol. SCREAMING_SNAKE wire strings are
/// pinned per-member so they are NOT affected by the context snake_case policy.
/// Keep in lock-step with errors.json and Python ErrorCode.
/// </summary>
[JsonConverter(typeof(JsonStringEnumConverter<HttpV2ErrorCode>))]
public enum HttpV2ErrorCode
{
    [JsonStringEnumMemberName("VALIDATION_ERROR")] ValidationError,
    [JsonStringEnumMemberName("QUOTA_EXCEEDED")] QuotaExceeded,
    [JsonStringEnumMemberName("UNAUTHORIZED")] Unauthorized,
    [JsonStringEnumMemberName("FORBIDDEN_LOOPBACK")] ForbiddenLoopback,
    [JsonStringEnumMemberName("JOB_NOT_FOUND")] JobNotFound,
    [JsonStringEnumMemberName("RESOURCE_NOT_FOUND")] ResourceNotFound,
    [JsonStringEnumMemberName("JOB_NOT_CANCELLABLE")] JobNotCancellable,
    [JsonStringEnumMemberName("JOB_NOT_RETRYABLE")] JobNotRetryable,
    [JsonStringEnumMemberName("INPUT_EXPIRED")] InputExpired,
    [JsonStringEnumMemberName("PIN_CAPACITY_CONFLICT")] PinCapacityConflict,
    [JsonStringEnumMemberName("SUPERVISOR_DRAINING")] SupervisorDraining,
    [JsonStringEnumMemberName("CANCELLED")] Cancelled,
    [JsonStringEnumMemberName("OUT_OF_MEMORY")] OutOfMemory,
    [JsonStringEnumMemberName("TRANSIENT_BACKEND")] TransientBackend,
    [JsonStringEnumMemberName("BACKEND_UNAVAILABLE")] BackendUnavailable,
    [JsonStringEnumMemberName("ADAPTER_PROTOCOL_VIOLATION")] AdapterProtocolViolation,
    [JsonStringEnumMemberName("PROTOCOL_MISMATCH")] ProtocolMismatch,
    [JsonStringEnumMemberName("INTERNAL_ERROR")] InternalError,
}
