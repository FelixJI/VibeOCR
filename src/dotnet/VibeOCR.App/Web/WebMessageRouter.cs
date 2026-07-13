using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;

namespace VibeOCR.App.Web;

public sealed class WebBridgeProtocolException(string message, Exception? innerException = null)
    : Exception(message, innerException);

public sealed record WebBridgeMessage(
    int Version,
    string Kind,
    Guid Id,
    string Type,
    JsonElement Payload);

public sealed class WebMessageRouter
{
    public const int ProtocolVersion = 1;
    public const int DefaultMaxMessageBytes = 64 * 1024;
    private static readonly HashSet<string> InboundTypes =
    [
        "preview.ready",
        "editor.changed",
        "selection.changed",
        "action.copy",
    ];
    private static readonly HashSet<string> OutboundTypes =
    [
        "preview.setState",
        "preview.setImage",
        "preview.setResult",
        "editor.apply",
    ];
    private static readonly HashSet<string> ExpectedFields =
        ["version", "kind", "id", "type", "payload"];
    private readonly int _maxMessageBytes;
    private readonly ConcurrentDictionary<Guid, PendingRequest> _pending = new();

    public WebMessageRouter(int maxMessageBytes = DefaultMaxMessageBytes)
    {
        if (maxMessageBytes < 128)
        {
            throw new ArgumentOutOfRangeException(nameof(maxMessageBytes));
        }

        _maxMessageBytes = maxMessageBytes;
    }

    public event Action<WebBridgeMessage>? MessageReceived;

    public int PendingCount => _pending.Count;

    public Task<JsonElement> RequestAsync(
        string type,
        object payload,
        Action<string> postMessage,
        CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(type);
        ArgumentNullException.ThrowIfNull(payload);
        ArgumentNullException.ThrowIfNull(postMessage);
        if (!OutboundTypes.Contains(type))
        {
            throw new WebBridgeProtocolException($"Unknown outbound message type: {type}.");
        }

        Guid id = Guid.NewGuid();
        var completion = new TaskCompletionSource<JsonElement>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var pending = new PendingRequest(type, completion);
        if (!_pending.TryAdd(id, pending))
        {
            throw new InvalidOperationException("Duplicate Web bridge request id.");
        }

        pending.Cancellation = cancellationToken.Register(() =>
        {
            if (_pending.TryRemove(id, out PendingRequest? removed))
            {
                removed.Completion.TrySetCanceled(cancellationToken);
            }
        });
        if (!_pending.ContainsKey(id))
        {
            return completion.Task;
        }

        string json = JsonSerializer.Serialize(new
        {
            version = ProtocolVersion,
            kind = "request",
            id,
            type,
            payload,
        });
        if (Encoding.UTF8.GetByteCount(json) > _maxMessageBytes)
        {
            _pending.TryRemove(id, out _);
            pending.Cancellation.Dispose();
            throw new WebBridgeProtocolException("Outbound Web message exceeds the size limit.");
        }

        try
        {
            postMessage(json);
        }
        catch
        {
            _pending.TryRemove(id, out _);
            pending.Cancellation.Dispose();
            throw;
        }

        return completion.Task;
    }

    public bool Handle(string json, string source)
    {
        WebBridgeMessage message = Parse(json, source);
        if (message.Kind == "response")
        {
            if (!_pending.TryRemove(message.Id, out PendingRequest? pending))
            {
                throw new WebBridgeProtocolException("Unsolicited Web bridge response.");
            }

            pending.Cancellation.Dispose();
            if (!string.Equals(pending.Type, message.Type, StringComparison.Ordinal))
            {
                var error = new WebBridgeProtocolException("Web bridge response type mismatch.");
                pending.Completion.TrySetException(error);
                throw error;
            }

            pending.Completion.TrySetResult(message.Payload);
            return true;
        }

        MessageReceived?.Invoke(message);
        return true;
    }

    private WebBridgeMessage Parse(string json, string source)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(json);
        if (!Uri.TryCreate(source, UriKind.Absolute, out Uri? sourceUri) ||
            !PreviewHost.IsNavigationAllowed(sourceUri))
        {
            throw new WebBridgeProtocolException("Web message came from an untrusted origin.");
        }

        if (Encoding.UTF8.GetByteCount(json) > _maxMessageBytes)
        {
            throw new WebBridgeProtocolException("Inbound Web message exceeds the size limit.");
        }

        try
        {
            using JsonDocument document = JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 32,
            });
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                throw new WebBridgeProtocolException("Web bridge message must be an object.");
            }

            JsonProperty[] properties = root.EnumerateObject().ToArray();
            if (properties.Length != ExpectedFields.Count ||
                properties.Any(property => !ExpectedFields.Contains(property.Name)))
            {
                throw new WebBridgeProtocolException("Web bridge message fields are invalid.");
            }

            int version = root.GetProperty("version").GetInt32();
            string kind = root.GetProperty("kind").GetString() ?? string.Empty;
            Guid id = root.GetProperty("id").GetGuid();
            string type = root.GetProperty("type").GetString() ?? string.Empty;
            JsonElement payload = root.GetProperty("payload");
            if (version != ProtocolVersion || kind is not ("event" or "request" or "response") ||
                !IsVersion4(id) || payload.ValueKind != JsonValueKind.Object)
            {
                throw new WebBridgeProtocolException("Web bridge envelope is invalid.");
            }

            if (kind is "event" or "request" && !InboundTypes.Contains(type))
            {
                throw new WebBridgeProtocolException($"Unknown inbound message type: {type}.");
            }

            if (kind == "response" && !OutboundTypes.Contains(type))
            {
                throw new WebBridgeProtocolException($"Unknown response message type: {type}.");
            }

            return new WebBridgeMessage(version, kind, id, type, payload.Clone());
        }
        catch (WebBridgeProtocolException)
        {
            throw;
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException or FormatException)
        {
            throw new WebBridgeProtocolException("Malformed Web bridge message.", error);
        }
    }

    private static bool IsVersion4(Guid value)
    {
        byte[] bytes = value.ToByteArray();
        return value != Guid.Empty && (bytes[7] >> 4) == 4 && (bytes[8] & 0xc0) == 0x80;
    }

    private sealed record PendingRequest(
        string Type,
        TaskCompletionSource<JsonElement> Completion)
    {
        public CancellationTokenRegistration Cancellation { get; set; }
    }
}
