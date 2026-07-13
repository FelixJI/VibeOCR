using System.Text.Json;
using System.Text.Json.Nodes;
using VibeOCR.Contracts;
using Xunit;

namespace VibeOCR.Contracts.Tests;

public sealed class GoldenContractTests
{
    private static readonly string ContractsDirectory = FindContractsDirectory();

    [Fact]
    public void EveryPublicMethodRoundTripsThroughGeneratedMetadata()
    {
        using JsonDocument golden = LoadJson("golden.json");
        JsonElement positive = golden.RootElement.GetProperty("positive");

        foreach (string method in RpcMethods.All)
        {
            JsonElement fixture = positive.GetProperty(method);
            AssertRoundTrip(
                fixture.GetProperty("request_envelope"),
                ProtocolJson.DeserializeRequest);
            AssertRoundTrip(
                fixture.GetProperty("response_envelope"),
                element => ProtocolJson.DeserializeResponse(method, element));
        }
    }

    [Fact]
    public void EventRoundTripsThroughGeneratedMetadata()
    {
        using JsonDocument golden = LoadJson("golden.json");
        JsonElement fixture = golden.RootElement
            .GetProperty("positive")
            .GetProperty("task.progress_event")
            .GetProperty("event_envelope");

        AssertRoundTrip(fixture, ProtocolJson.DeserializeEvent);
    }

    [Theory]
    [InlineData("unknown_extra_field_request")]
    [InlineData("wrong_protocol_version")]
    [InlineData("missing_request_id")]
    [InlineData("missing_task_id")]
    [InlineData("malformed_uuid")]
    [InlineData("unknown_method")]
    [InlineData("response_both_result_and_error")]
    [InlineData("response_neither_result_nor_error")]
    [InlineData("invalid_shared_payload_bad_sha")]
    [InlineData("invalid_shared_payload_bad_name")]
    [InlineData("unknown_error_code")]
    public void NegativeGoldenCasesAreRejected(string caseName)
    {
        using JsonDocument golden = LoadJson("golden.json");
        JsonElement fixture = golden.RootElement.GetProperty("negative").GetProperty(caseName);

        Assert.Throws<ProtocolContractException>(() => ProtocolJson.DeserializeEnvelope(fixture));
    }

    [Fact]
    public void ErrorRegistryMatchesTheWireEnumAndRetryPolicy()
    {
        using JsonDocument registry = LoadJson("errors.json");
        var entries = registry.RootElement.GetProperty("codes").EnumerateArray().ToArray();
        string[] registered = entries.Select(entry => entry.GetProperty("code").GetString()!).ToArray();
        string[] declared = Enum.GetValues<ErrorCode>().Select(ProtocolJson.GetWireValue).ToArray();

        Assert.Equal(registered.Order(), declared.Order());
        foreach (JsonElement entry in entries)
        {
            ErrorCode code = ProtocolJson.ParseErrorCode(entry.GetProperty("code").GetString()!);
            Assert.Equal(entry.GetProperty("retryable").GetBoolean(), RpcErrors.IsRetryable(code));
        }
    }

    private static void AssertRoundTrip(JsonElement expected, Func<JsonElement, object> deserialize)
    {
        object value = deserialize(expected);
        Assert.NotNull(ProtocolJsonContext.Default.GetTypeInfo(value.GetType()));
        JsonNode expectedNode = JsonNode.Parse(expected.GetRawText())!;
        JsonNode actualNode = JsonNode.Parse(ProtocolJson.Serialize(value))!;
        Assert.True(JsonNode.DeepEquals(expectedNode, actualNode));
    }

    private static JsonDocument LoadJson(string name) =>
        JsonDocument.Parse(File.ReadAllText(Path.Combine(ContractsDirectory, name)));

    private static string FindContractsDirectory()
    {
        foreach (string? seed in new[]
                 {
                     Environment.GetEnvironmentVariable("VIBEOCR_REPOSITORY_ROOT"),
                     Directory.GetCurrentDirectory(),
                     AppContext.BaseDirectory,
                 })
        {
            DirectoryInfo? directory = string.IsNullOrWhiteSpace(seed) ? null : new(seed);
            while (directory is not null)
            {
                string candidate = Path.Combine(directory.FullName, "contracts", "v1");
                if (File.Exists(Path.Combine(candidate, "golden.json")))
                {
                    return candidate;
                }

                directory = directory.Parent;
            }
        }

        throw new DirectoryNotFoundException("Could not locate contracts/v1 from test output.");
    }
}
