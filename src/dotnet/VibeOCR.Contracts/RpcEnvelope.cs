using System.Text.Json;
using System.Text.Json.Serialization;

namespace VibeOCR.Contracts;

public static class ProtocolConstants
{
    public const int Version = 1;
}

public sealed class ProtocolContractException(string message, Exception? innerException = null)
    : Exception(message, innerException);

public interface IProtocolValidatable
{
    void Validate();
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RpcRequestEnvelope<TPayload> : IProtocolValidatable
    where TPayload : IProtocolValidatable
{
    public required int ProtocolVersion { get; init; }
    public required Guid RequestId { get; init; }
    public required Guid TaskId { get; init; }
    public required string Method { get; init; }
    public required TPayload Payload { get; init; }
    public required long DeadlineUnixMs { get; init; }

    public void Validate()
    {
        ContractValidation.Version(ProtocolVersion);
        ContractValidation.Version4Uuid(RequestId, nameof(RequestId));
        ContractValidation.Version4Uuid(TaskId, nameof(TaskId));
        if (DeadlineUnixMs < 0)
        {
            throw new ProtocolContractException("deadline_unix_ms must be non-negative.");
        }

        RpcMethods.EnsureKnown(Method);
        Payload.Validate();
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RpcResponseEnvelope<TResult> : IProtocolValidatable
    where TResult : IProtocolValidatable
{
    public required int ProtocolVersion { get; init; }
    public required Guid RequestId { get; init; }
    public required Guid TaskId { get; init; }
    public required TResult Result { get; init; }

    public void Validate()
    {
        ContractValidation.Version(ProtocolVersion);
        ContractValidation.Version4Uuid(RequestId, nameof(RequestId));
        ContractValidation.Version4Uuid(TaskId, nameof(TaskId));
        Result.Validate();
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RpcErrorEnvelope : IProtocolValidatable
{
    public required int ProtocolVersion { get; init; }
    public required Guid RequestId { get; init; }
    public required Guid TaskId { get; init; }
    public required RpcErrorBody Error { get; init; }

    public void Validate()
    {
        ContractValidation.Version(ProtocolVersion);
        ContractValidation.Version4Uuid(RequestId, nameof(RequestId));
        ContractValidation.Version4Uuid(TaskId, nameof(TaskId));
        Error.Validate();
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RpcEventEnvelope : IProtocolValidatable
{
    public required int ProtocolVersion { get; init; }
    public required Guid TaskId { get; init; }
    public required string Event { get; init; }
    public required long Sequence { get; init; }
    public required JsonElement Payload { get; init; }

    public void Validate()
    {
        ContractValidation.Version(ProtocolVersion);
        ContractValidation.Version4Uuid(TaskId, nameof(TaskId));
        ContractValidation.NonEmpty(Event, nameof(Event));
        if (Sequence < 0 || Payload.ValueKind is not JsonValueKind.Object)
        {
            throw new ProtocolContractException("Event sequence/payload is invalid.");
        }
    }
}

[JsonConverter(typeof(JsonStringEnumConverter<ErrorCode>))]
public enum ErrorCode
{
    [JsonStringEnumMemberName("INVALID_REQUEST")]
    InvalidRequest,
    [JsonStringEnumMemberName("DEPENDENCY_MISSING")]
    DependencyMissing,
    [JsonStringEnumMemberName("WORKER_UNAVAILABLE")]
    WorkerUnavailable,
    [JsonStringEnumMemberName("TASK_CANCELLED")]
    TaskCancelled,
    [JsonStringEnumMemberName("TASK_TIMEOUT")]
    TaskTimeout,
    [JsonStringEnumMemberName("PROTOCOL_MISMATCH")]
    ProtocolMismatch,
    [JsonStringEnumMemberName("RESOURCE_EXHAUSTED")]
    ResourceExhausted,
    [JsonStringEnumMemberName("INTERNAL_ERROR")]
    InternalError,
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RpcErrorBody : IProtocolValidatable
{
    public required ErrorCode Code { get; init; }
    public required string Message { get; init; }
    public required bool Retryable { get; init; }

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Detail { get; init; }

    public void Validate()
    {
        if (!Enum.IsDefined(Code))
        {
            throw new ProtocolContractException("Unknown error code.");
        }

        ContractValidation.NonEmpty(Message, nameof(Message));
    }
}

public static class RpcErrors
{
    public static bool IsRetryable(ErrorCode code) => code is
        ErrorCode.WorkerUnavailable or ErrorCode.TaskTimeout or ErrorCode.ResourceExhausted;
}

internal static class ContractValidation
{
    public static void Version(int version)
    {
        if (version != ProtocolConstants.Version)
        {
            throw new ProtocolContractException(
                $"protocol_version must be {ProtocolConstants.Version}, got {version}.");
        }
    }

    public static void Version4Uuid(Guid value, string name)
    {
        byte[] bytes = value.ToByteArray();
        if (value == Guid.Empty || (bytes[7] >> 4) != 4 || (bytes[8] & 0xc0) != 0x80)
        {
            throw new ProtocolContractException($"{name} must be an RFC 4122 version 4 UUID.");
        }
    }

    public static void NonEmpty(string? value, string name)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ProtocolContractException($"{name} must not be empty.");
        }
    }

    public static void OneOf(string? value, string name, params string[] allowed)
    {
        if (value is null || !allowed.Contains(value, StringComparer.Ordinal))
        {
            throw new ProtocolContractException(
                $"{name} must be one of: {string.Join(", ", allowed)}.");
        }
    }
}
