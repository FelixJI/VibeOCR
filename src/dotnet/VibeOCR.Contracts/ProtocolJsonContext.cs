using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;

namespace VibeOCR.Contracts;

[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    GenerationMode = JsonSourceGenerationMode.Metadata)]
[JsonSerializable(typeof(RpcRequestEnvelope<HandshakeRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<PingRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<ShutdownRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<CancelRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<ReleaseMemoryRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<RecognizeRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<RecognizeBatchRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<ExportOcrRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<OpenPdfRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<ClosePdfRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<PdfCommandRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<RenderPdfPageRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<RotatePdfRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<DeletePdfPagesRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<AddPdfTextLayerRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<DeletePdfTextLayersRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<SavePdfRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<StartPdfOcrRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<DecodeQrCodeRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<GenerateQrCodeRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<GenerateQrCodeSvgRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<PipelineCacheStatusRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<SetPipelineCacheTtlRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<ReleasePipelineCacheRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<PreloadPipelineCacheRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<WarmupPipelineCacheRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<SettingsSnapshotRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<SwitchBackendRequest>))]
[JsonSerializable(typeof(RpcRequestEnvelope<InstallDependencyRequest>))]
[JsonSerializable(typeof(RpcResponseEnvelope<HandshakeResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<PingResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<ShutdownResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<CancelResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<ReleaseMemoryResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<RecognizeResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<RecognizeBatchResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<ExportOcrResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<OpenPdfResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<ClosePdfResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<PdfCommandResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<RenderPdfPageResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<RotatePdfResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<DeletePdfPagesResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<AddPdfTextLayerResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<DeletePdfTextLayersResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<SavePdfResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<StartPdfOcrResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<DecodeQrCodeResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<GenerateQrCodeResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<GenerateQrCodeSvgResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<PipelineCacheStatusResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<SetPipelineCacheTtlResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<ReleasePipelineCacheResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<PreloadPipelineCacheResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<WarmupPipelineCacheResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<SettingsSnapshotResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<SwitchBackendResponse>))]
[JsonSerializable(typeof(RpcResponseEnvelope<InstallDependencyResponse>))]
[JsonSerializable(typeof(RpcErrorEnvelope))]
[JsonSerializable(typeof(RpcEventEnvelope))]
public partial class ProtocolJsonContext : JsonSerializerContext;

public static class ProtocolJson
{
    public static object DeserializeEnvelope(JsonElement element)
    {
        try
        {
            RequireObject(element);
            bool hasMethod = element.TryGetProperty("method", out _);
            bool hasEvent = element.TryGetProperty("event", out _);
            bool hasResult = element.TryGetProperty("result", out _);
            bool hasError = element.TryGetProperty("error", out _);

            if (hasMethod)
            {
                return DeserializeRequest(element);
            }

            if (hasEvent)
            {
                return DeserializeEvent(element);
            }

            if (hasResult == hasError)
            {
                throw new ProtocolContractException(
                    "A response must contain exactly one of result or error.");
            }

            if (hasError)
            {
                return Deserialize(element, typeof(RpcErrorEnvelope));
            }

            throw new ProtocolContractException(
                "A success response requires its correlated request method.");
        }
        catch (ProtocolContractException)
        {
            throw;
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException)
        {
            throw new ProtocolContractException("Invalid WorkerHost envelope.", error);
        }
    }

    public static object DeserializeRequest(JsonElement element)
    {
        try
        {
            RequireObject(element);
            string method = RequiredString(element, "method");
            Type type = method switch
            {
                RpcMethods.Handshake => typeof(RpcRequestEnvelope<HandshakeRequest>),
                RpcMethods.Ping => typeof(RpcRequestEnvelope<PingRequest>),
                RpcMethods.Shutdown => typeof(RpcRequestEnvelope<ShutdownRequest>),
                RpcMethods.Cancel => typeof(RpcRequestEnvelope<CancelRequest>),
                RpcMethods.ReleaseMemory => typeof(RpcRequestEnvelope<ReleaseMemoryRequest>),
                RpcMethods.Recognize => typeof(RpcRequestEnvelope<RecognizeRequest>),
                RpcMethods.RecognizeBatch => typeof(RpcRequestEnvelope<RecognizeBatchRequest>),
                RpcMethods.ExportOcr => typeof(RpcRequestEnvelope<ExportOcrRequest>),
                RpcMethods.OpenPdf => typeof(RpcRequestEnvelope<OpenPdfRequest>),
                RpcMethods.ClosePdf => typeof(RpcRequestEnvelope<ClosePdfRequest>),
                RpcMethods.PdfCommand => typeof(RpcRequestEnvelope<PdfCommandRequest>),
                RpcMethods.RenderPdfPage => typeof(RpcRequestEnvelope<RenderPdfPageRequest>),
                RpcMethods.RotatePdf => typeof(RpcRequestEnvelope<RotatePdfRequest>),
                RpcMethods.DeletePdfPages => typeof(RpcRequestEnvelope<DeletePdfPagesRequest>),
                RpcMethods.AddPdfTextLayer => typeof(RpcRequestEnvelope<AddPdfTextLayerRequest>),
                RpcMethods.DeletePdfTextLayers => typeof(RpcRequestEnvelope<DeletePdfTextLayersRequest>),
                RpcMethods.SavePdf => typeof(RpcRequestEnvelope<SavePdfRequest>),
                RpcMethods.StartPdfOcr => typeof(RpcRequestEnvelope<StartPdfOcrRequest>),
                RpcMethods.DecodeQrCode => typeof(RpcRequestEnvelope<DecodeQrCodeRequest>),
                RpcMethods.GenerateQrCode => typeof(RpcRequestEnvelope<GenerateQrCodeRequest>),
                RpcMethods.GenerateQrCodeSvg => typeof(RpcRequestEnvelope<GenerateQrCodeSvgRequest>),
                RpcMethods.PipelineCacheStatus => typeof(RpcRequestEnvelope<PipelineCacheStatusRequest>),
                RpcMethods.SetPipelineCacheTtl => typeof(RpcRequestEnvelope<SetPipelineCacheTtlRequest>),
                RpcMethods.ReleasePipelineCache => typeof(RpcRequestEnvelope<ReleasePipelineCacheRequest>),
                RpcMethods.PreloadPipelineCache => typeof(RpcRequestEnvelope<PreloadPipelineCacheRequest>),
                RpcMethods.WarmupPipelineCache => typeof(RpcRequestEnvelope<WarmupPipelineCacheRequest>),
                RpcMethods.SettingsSnapshot => typeof(RpcRequestEnvelope<SettingsSnapshotRequest>),
                RpcMethods.SwitchBackend => typeof(RpcRequestEnvelope<SwitchBackendRequest>),
                RpcMethods.InstallDependency => typeof(RpcRequestEnvelope<InstallDependencyRequest>),
                _ => throw new ProtocolContractException($"Unknown WorkerHost method: {method}."),
            };
            object value = Deserialize(element, type);
            if (!string.Equals(GetMethod(value), method, StringComparison.Ordinal))
            {
                throw new ProtocolContractException("Request method does not match its DTO.");
            }

            return value;
        }
        catch (ProtocolContractException)
        {
            throw;
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException)
        {
            throw new ProtocolContractException("Invalid WorkerHost request.", error);
        }
    }

    public static object DeserializeResponse(string method, JsonElement element)
    {
        try
        {
            RequireObject(element);
            RpcMethods.EnsureKnown(method);
            bool hasResult = element.TryGetProperty("result", out _);
            bool hasError = element.TryGetProperty("error", out _);
            if (hasResult == hasError)
            {
                throw new ProtocolContractException(
                    "A response must contain exactly one of result or error.");
            }

            if (hasError)
            {
                return Deserialize(element, typeof(RpcErrorEnvelope));
            }

            Type type = method switch
            {
                RpcMethods.Handshake => typeof(RpcResponseEnvelope<HandshakeResponse>),
                RpcMethods.Ping => typeof(RpcResponseEnvelope<PingResponse>),
                RpcMethods.Shutdown => typeof(RpcResponseEnvelope<ShutdownResponse>),
                RpcMethods.Cancel => typeof(RpcResponseEnvelope<CancelResponse>),
                RpcMethods.ReleaseMemory => typeof(RpcResponseEnvelope<ReleaseMemoryResponse>),
                RpcMethods.Recognize => typeof(RpcResponseEnvelope<RecognizeResponse>),
                RpcMethods.RecognizeBatch => typeof(RpcResponseEnvelope<RecognizeBatchResponse>),
                RpcMethods.ExportOcr => typeof(RpcResponseEnvelope<ExportOcrResponse>),
                RpcMethods.OpenPdf => typeof(RpcResponseEnvelope<OpenPdfResponse>),
                RpcMethods.ClosePdf => typeof(RpcResponseEnvelope<ClosePdfResponse>),
                RpcMethods.PdfCommand => typeof(RpcResponseEnvelope<PdfCommandResponse>),
                RpcMethods.RenderPdfPage => typeof(RpcResponseEnvelope<RenderPdfPageResponse>),
                RpcMethods.RotatePdf => typeof(RpcResponseEnvelope<RotatePdfResponse>),
                RpcMethods.DeletePdfPages => typeof(RpcResponseEnvelope<DeletePdfPagesResponse>),
                RpcMethods.AddPdfTextLayer => typeof(RpcResponseEnvelope<AddPdfTextLayerResponse>),
                RpcMethods.DeletePdfTextLayers => typeof(RpcResponseEnvelope<DeletePdfTextLayersResponse>),
                RpcMethods.SavePdf => typeof(RpcResponseEnvelope<SavePdfResponse>),
                RpcMethods.StartPdfOcr => typeof(RpcResponseEnvelope<StartPdfOcrResponse>),
                RpcMethods.DecodeQrCode => typeof(RpcResponseEnvelope<DecodeQrCodeResponse>),
                RpcMethods.GenerateQrCode => typeof(RpcResponseEnvelope<GenerateQrCodeResponse>),
                RpcMethods.GenerateQrCodeSvg => typeof(RpcResponseEnvelope<GenerateQrCodeSvgResponse>),
                RpcMethods.PipelineCacheStatus => typeof(RpcResponseEnvelope<PipelineCacheStatusResponse>),
                RpcMethods.SetPipelineCacheTtl => typeof(RpcResponseEnvelope<SetPipelineCacheTtlResponse>),
                RpcMethods.ReleasePipelineCache => typeof(RpcResponseEnvelope<ReleasePipelineCacheResponse>),
                RpcMethods.PreloadPipelineCache => typeof(RpcResponseEnvelope<PreloadPipelineCacheResponse>),
                RpcMethods.WarmupPipelineCache => typeof(RpcResponseEnvelope<WarmupPipelineCacheResponse>),
                RpcMethods.SettingsSnapshot => typeof(RpcResponseEnvelope<SettingsSnapshotResponse>),
                RpcMethods.SwitchBackend => typeof(RpcResponseEnvelope<SwitchBackendResponse>),
                RpcMethods.InstallDependency => typeof(RpcResponseEnvelope<InstallDependencyResponse>),
                _ => throw new ProtocolContractException($"Unknown WorkerHost method: {method}."),
            };
            return Deserialize(element, type);
        }
        catch (ProtocolContractException)
        {
            throw;
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException)
        {
            throw new ProtocolContractException("Invalid WorkerHost response.", error);
        }
    }

    public static object DeserializeEvent(JsonElement element) =>
        Deserialize(element, typeof(RpcEventEnvelope));

    public static string Serialize(object value)
    {
        JsonTypeInfo typeInfo = ProtocolJsonContext.Default.GetTypeInfo(value.GetType())
            ?? throw new ProtocolContractException(
                $"No generated JSON metadata for {value.GetType().Name}.");
        return JsonSerializer.Serialize(value, typeInfo);
    }

    public static string GetWireValue(ErrorCode code) => code switch
    {
        ErrorCode.InvalidRequest => "INVALID_REQUEST",
        ErrorCode.DependencyMissing => "DEPENDENCY_MISSING",
        ErrorCode.WorkerUnavailable => "WORKER_UNAVAILABLE",
        ErrorCode.TaskCancelled => "TASK_CANCELLED",
        ErrorCode.TaskTimeout => "TASK_TIMEOUT",
        ErrorCode.ProtocolMismatch => "PROTOCOL_MISMATCH",
        ErrorCode.ResourceExhausted => "RESOURCE_EXHAUSTED",
        ErrorCode.InternalError => "INTERNAL_ERROR",
        _ => throw new ProtocolContractException($"Unknown error code: {code}."),
    };

    public static ErrorCode ParseErrorCode(string wireValue) => wireValue switch
    {
        "INVALID_REQUEST" => ErrorCode.InvalidRequest,
        "DEPENDENCY_MISSING" => ErrorCode.DependencyMissing,
        "WORKER_UNAVAILABLE" => ErrorCode.WorkerUnavailable,
        "TASK_CANCELLED" => ErrorCode.TaskCancelled,
        "TASK_TIMEOUT" => ErrorCode.TaskTimeout,
        "PROTOCOL_MISMATCH" => ErrorCode.ProtocolMismatch,
        "RESOURCE_EXHAUSTED" => ErrorCode.ResourceExhausted,
        "INTERNAL_ERROR" => ErrorCode.InternalError,
        _ => throw new ProtocolContractException($"Unknown error code: {wireValue}."),
    };

    private static object Deserialize(JsonElement element, Type type)
    {
        try
        {
            JsonTypeInfo typeInfo = ProtocolJsonContext.Default.GetTypeInfo(type)
                ?? throw new ProtocolContractException(
                    $"No generated JSON metadata for {type.Name}.");
            object value = JsonSerializer.Deserialize(element.GetRawText(), typeInfo)
                ?? throw new ProtocolContractException("Envelope deserialized to null.");
            ((IProtocolValidatable)value).Validate();
            return value;
        }
        catch (ProtocolContractException)
        {
            throw;
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException)
        {
            throw new ProtocolContractException($"Invalid {type.Name} payload.", error);
        }
    }

    private static void RequireObject(JsonElement element)
    {
        if (element.ValueKind is not JsonValueKind.Object)
        {
            throw new ProtocolContractException("Envelope must be a JSON object.");
        }
    }

    private static string RequiredString(JsonElement element, string name)
    {
        if (!element.TryGetProperty(name, out JsonElement value) ||
            value.ValueKind is not JsonValueKind.String)
        {
            throw new ProtocolContractException($"Envelope is missing string field {name}.");
        }

        return value.GetString()!;
    }

    private static string GetMethod(object request) =>
        (string)(request.GetType().GetProperty(nameof(RpcRequestEnvelope<PingRequest>.Method))!
            .GetValue(request) ?? string.Empty);
}
