// Typed exception for the v2 supervisor client. Mirrors the Python
// InferenceClientError: never carries a backend traceback, always a typed code.
using System.Text.Json;
using VibeOCR.Contracts.HttpV2;

namespace VibeOCR.Platform.Inference;

/// <summary>
/// Raised by <see cref="IInferenceClient"/> on any non-success response.
/// The UI maps <see cref="Code"/> to user-visible behaviour.
/// </summary>
public sealed class InferenceClientException : Exception
{
    public InferenceClientException(HttpV2ErrorCode code, string message, bool retryable, IDictionary<string, JsonElement>? detail = null)
        : base(message)
    {
        Code = code;
        Retryable = retryable;
        Detail = detail ?? new Dictionary<string, JsonElement>();
    }

    /// <summary>Typed error code matching the v2 errors.json registry.</summary>
    public HttpV2ErrorCode Code { get; }

    /// <summary>Whether the UI may auto-retry once (transient codes).</summary>
    public bool Retryable { get; }

    /// <summary>Free-form typed detail from the server.</summary>
    public IDictionary<string, JsonElement> Detail { get; }
}
