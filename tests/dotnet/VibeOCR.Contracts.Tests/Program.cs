using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

if (args.Length != 1)
{
    Console.Error.WriteLine("usage: VibeOCR.Contracts.Tests <contracts/v1/golden.json>");
    return 2;
}

try
{
    GoldenValidator.Validate(Path.GetFullPath(args[0]));
    Console.WriteLine("WorkerHost v1 C# golden contract: PASS");
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine($"WorkerHost v1 C# golden contract: FAIL: {error.Message}");
    return 1;
}

internal static partial class GoldenValidator
{
    private const int ProtocolVersion = 1;

    private static readonly string[] PublicMethods =
    [
        "system.handshake", "system.ping", "system.shutdown", "task.cancel",
        "memory.release", "ocr.recognize", "pdf.open", "qrcode.decode",
        "qrcode.generate", "settings.snapshot",
    ];

    private static readonly HashSet<string> RequestEnvelopeKeys =
    ["protocol_version", "request_id", "task_id", "method", "payload", "deadline_unix_ms"];

    private static readonly HashSet<string> ResponseEnvelopeKeys =
    ["protocol_version", "request_id", "task_id", "result"];

    private static readonly Dictionary<string, Shape> RequestShapes = new()
    {
        ["system.handshake"] = new(["app_version", "protocol_version"], ["max_message_bytes", "max_shared_payload_bytes"]),
        ["system.ping"] = new(["nonce"]),
        ["system.shutdown"] = new([], ["reason"]),
        ["task.cancel"] = new(["task_id"]),
        ["memory.release"] = new(["name"]),
        ["ocr.recognize"] = new(["image"], ["pipeline", "language"]),
        ["pdf.open"] = new(["file_path"]),
        ["qrcode.decode"] = new(["image"]),
        ["qrcode.generate"] = new(["data"], ["format"]),
        ["settings.snapshot"] = new([]),
    };

    private static readonly Dictionary<string, Shape> ResponseShapes = new()
    {
        ["system.handshake"] = new(["worker_version", "protocol_version", "capabilities", "python_version", "backend", "max_message_bytes", "max_shared_payload_bytes"]),
        ["system.ping"] = new(["nonce"]),
        ["system.shutdown"] = new(["acknowledged"]),
        ["task.cancel"] = new(["accepted", "state"]),
        ["memory.release"] = new(["released"]),
        ["ocr.recognize"] = new(["text", "pipeline"], ["raw_blocks"]),
        ["pdf.open"] = new(["session_id", "file_path", "page_count"]),
        ["qrcode.decode"] = new(["codes"]),
        ["qrcode.generate"] = new(["image"]),
        ["settings.snapshot"] = new(["backend", "preload_pipelines", "ttl_seconds"]),
    };

    public static void Validate(string goldenPath)
    {
        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(goldenPath));
        JsonElement root = document.RootElement;
        Require(root.GetProperty("version").GetInt32() == ProtocolVersion, "golden version must be 1");
        JsonElement positive = root.GetProperty("positive");

        foreach (string method in PublicMethods)
        {
            Require(positive.TryGetProperty(method, out JsonElement testCase), $"missing positive case {method}");
            JsonElement request = testCase.GetProperty("request_envelope");
            JsonElement response = testCase.GetProperty("response_envelope");
            ValidateRequestEnvelope(method, request);
            ValidateResponseEnvelope(method, response);
            SemanticRoundTrip(request, $"{method} request");
            SemanticRoundTrip(response, $"{method} response");
        }

        ValidateEvent(positive.GetProperty("task.progress_event").GetProperty("event_envelope"));
        ValidateNegativeCases(root.GetProperty("negative"));
    }

    private static void ValidateRequestEnvelope(string method, JsonElement envelope)
    {
        ExactKeys(envelope, RequestEnvelopeKeys, $"{method} request envelope");
        Require(envelope.GetProperty("protocol_version").GetInt32() == ProtocolVersion, $"{method}: wrong protocol version");
        ValidateUuid(envelope.GetProperty("request_id").GetString(), $"{method}.request_id");
        ValidateUuid(envelope.GetProperty("task_id").GetString(), $"{method}.task_id");
        Require(envelope.GetProperty("method").GetString() == method, $"{method}: method mismatch");
        Require(envelope.GetProperty("deadline_unix_ms").GetInt64() >= 0, $"{method}: negative deadline");
        JsonElement payload = envelope.GetProperty("payload");
        RequestShapes[method].Validate(payload, $"{method} request payload");
        ValidateMethodSpecific(method, payload, isResponse: false);
    }

    private static void ValidateResponseEnvelope(string method, JsonElement envelope)
    {
        ExactKeys(envelope, ResponseEnvelopeKeys, $"{method} response envelope");
        Require(envelope.GetProperty("protocol_version").GetInt32() == ProtocolVersion, $"{method}: wrong response protocol version");
        ValidateUuid(envelope.GetProperty("request_id").GetString(), $"{method}.response.request_id");
        ValidateUuid(envelope.GetProperty("task_id").GetString(), $"{method}.response.task_id");
        JsonElement result = envelope.GetProperty("result");
        ResponseShapes[method].Validate(result, $"{method} response payload");
        ValidateMethodSpecific(method, result, isResponse: true);
    }

    private static void ValidateMethodSpecific(string method, JsonElement payload, bool isResponse)
    {
        if (!isResponse && method is "ocr.recognize" or "qrcode.decode")
            ValidateSharedRef(payload.GetProperty("image"), $"{method}.image");
        if (isResponse && method == "qrcode.generate")
            ValidateSharedRef(payload.GetProperty("image"), "qrcode.generate.image");
        if (!isResponse && method == "task.cancel")
            ValidateUuid(payload.GetProperty("task_id").GetString(), "task.cancel.task_id");
        if (!isResponse && method == "system.handshake")
            Require(payload.GetProperty("protocol_version").GetInt32() == ProtocolVersion, "handshake protocol mismatch");
        if (method == "settings.snapshot" && isResponse)
        {
            Require(payload.GetProperty("backend").GetString() is "cpu" or "gpu", "invalid settings backend");
            Require(payload.GetProperty("preload_pipelines").ValueKind == JsonValueKind.Array, "preload_pipelines must be array");
        }
    }

    private static void ValidateSharedRef(JsonElement reference, string label)
    {
        Shape shape = new(["name", "size", "media_type", "sha256", "owner", "expires_unix_ms"]);
        shape.Validate(reference, label);
        Require(SharedNameRegex().IsMatch(reference.GetProperty("name").GetString() ?? ""), $"{label}: invalid name");
        Require(reference.GetProperty("size").GetInt64() >= 0, $"{label}: invalid size");
        Require(ShaRegex().IsMatch(reference.GetProperty("sha256").GetString() ?? ""), $"{label}: invalid SHA-256");
        Require(reference.GetProperty("owner").GetString() is "client" or "worker", $"{label}: invalid owner");
        Require(reference.GetProperty("expires_unix_ms").GetInt64() >= 0, $"{label}: invalid expiry");
    }

    private static void ValidateEvent(JsonElement envelope)
    {
        ExactKeys(envelope, ["protocol_version", "task_id", "event", "sequence", "payload"], "event envelope");
        Require(envelope.GetProperty("protocol_version").GetInt32() == ProtocolVersion, "event protocol mismatch");
        ValidateUuid(envelope.GetProperty("task_id").GetString(), "event.task_id");
        Require(envelope.GetProperty("sequence").GetInt64() >= 0, "negative event sequence");
    }

    private static void ValidateNegativeCases(JsonElement negative)
    {
        Require(negative.GetProperty("wrong_protocol_version").GetProperty("protocol_version").GetInt32() != ProtocolVersion, "wrong version fixture is not negative");
        Require(!negative.GetProperty("missing_request_id").TryGetProperty("request_id", out _), "missing_request_id fixture contains request_id");
        Require(!negative.GetProperty("missing_task_id").TryGetProperty("task_id", out _), "missing_task_id fixture contains task_id");
        Require(!UuidRegex().IsMatch(negative.GetProperty("malformed_uuid").GetProperty("request_id").GetString() ?? ""), "malformed UUID fixture is valid");
        Require(negative.GetProperty("unknown_method").GetProperty("method").GetString() is not null and not "", "unknown method fixture malformed");
        Require(!ShaRegex().IsMatch(negative.GetProperty("invalid_shared_payload_bad_sha").GetProperty("payload").GetProperty("image").GetProperty("sha256").GetString() ?? ""), "bad SHA fixture is valid");
        Require(!SharedNameRegex().IsMatch(negative.GetProperty("invalid_shared_payload_bad_name").GetProperty("payload").GetProperty("image").GetProperty("name").GetString() ?? ""), "bad name fixture is valid");
    }

    private static void SemanticRoundTrip(JsonElement value, string label)
    {
        JsonNode original = JsonNode.Parse(value.GetRawText()) ?? throw new InvalidDataException($"{label}: null JSON");
        string serialized = JsonSerializer.Serialize(original);
        JsonNode reparsed = JsonNode.Parse(serialized) ?? throw new InvalidDataException($"{label}: roundtrip null JSON");
        Require(JsonNode.DeepEquals(original, reparsed), $"{label}: semantic roundtrip drift");
    }

    private static void ExactKeys(JsonElement value, IEnumerable<string> expected, string label)
    {
        Require(value.ValueKind == JsonValueKind.Object, $"{label} must be object");
        HashSet<string> actual = value.EnumerateObject().Select(property => property.Name).ToHashSet(StringComparer.Ordinal);
        HashSet<string> wanted = expected.ToHashSet(StringComparer.Ordinal);
        Require(actual.SetEquals(wanted), $"{label}: keys [{string.Join(",", actual)}] != [{string.Join(",", wanted)}]");
    }

    private static void ValidateUuid(string? value, string label) =>
        Require(value is not null && UuidRegex().IsMatch(value), $"{label}: invalid lowercase v4 UUID");

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidDataException(message);
    }

    private sealed record Shape(string[] Required, string[]? Optional = null)
    {
        public void Validate(JsonElement value, string label)
        {
            HashSet<string> allowed = Required.Concat(Optional ?? []).ToHashSet(StringComparer.Ordinal);
            HashSet<string> actual = value.EnumerateObject().Select(property => property.Name).ToHashSet(StringComparer.Ordinal);
            Require(Required.All(actual.Contains), $"{label}: missing required field");
            Require(actual.IsSubsetOf(allowed), $"{label}: unknown field");
        }
    }

    [GeneratedRegex("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
    private static partial Regex UuidRegex();

    [GeneratedRegex("^Local\\\\VibeOCR-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
    private static partial Regex SharedNameRegex();

    [GeneratedRegex("^[0-9a-f]{64}$")]
    private static partial Regex ShaRegex();
}
