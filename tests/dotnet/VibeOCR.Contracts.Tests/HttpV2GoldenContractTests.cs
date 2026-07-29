// Phase 1 (.NET side) + Phase 7B golden contract tests for the HTTP v2 protocol.
//
// These prove .NET and Python agree on the same v2 wire payloads (plan §1
// exit criterion: "Python 与 C# golden 100% 一致"). Fixtures are loaded from
// the Python package's runtime_contracts/golden/golden.json and errors.json, exactly
// like the v1 tests read protocol/v1. Round-trips use structural DeepEquals
// (order/whitespace-insensitive), mirroring GoldenContractTests.
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using VibeOCR.Contracts.HttpV2;
using Xunit;

namespace VibeOCR.Contracts.Tests;

public sealed class HttpV2GoldenContractTests
{
    private static readonly string GoldenDirectory = FindV2Directory();

    // ------------------------------------------------------------------
    // JobSnapshot golden round-trips
    // ------------------------------------------------------------------

    [Theory]
    [InlineData("job_snapshot_running")]
    [InlineData("job_snapshot_completed_with_errors")]
    [InlineData("job_snapshot_cancelled")]
    public void JobSnapshotsRoundTrip(string key)
    {
        JsonElement fixture = LoadGolden().RootElement.GetProperty(key);
        AssertDeepRoundTrip(fixture, json => HttpV2Json.Deserialize<JobSnapshot>(json)!);
    }

    [Fact]
    public void JobRefRoundTrips()
    {
        JsonElement fixture = LoadGolden().RootElement.GetProperty("job_ref");
        AssertDeepRoundTrip(fixture, json => HttpV2Json.Deserialize<JobRef>(json)!);
    }

    // ------------------------------------------------------------------
    // Error payload golden round-trips
    // ------------------------------------------------------------------

    [Theory]
    [InlineData("error_validation")]
    [InlineData("error_oom")]
    [InlineData("error_cancelled")]
    public void ErrorPayloadsRoundTrip(string key)
    {
        JsonElement fixture = LoadGolden().RootElement.GetProperty(key);
        AssertDeepRoundTrip(fixture, json => HttpV2Json.Deserialize<HttpV2ErrorPayload>(json)!);
    }

    // ------------------------------------------------------------------
    // Residency / settings golden round-trips
    // ------------------------------------------------------------------

    [Fact]
    public void ResidencyStatusRoundTrips()
    {
        JsonElement fixture = LoadGolden().RootElement.GetProperty("residency_status");
        AssertDeepRoundTrip(fixture, json => HttpV2Json.Deserialize<ResidencyStatus>(json)!);
    }

    [Fact]
    public void SettingsSnapshotRoundTrips()
    {
        JsonElement fixture = LoadGolden().RootElement.GetProperty("settings_snapshot");
        AssertDeepRoundTrip(fixture, json => HttpV2Json.Deserialize<SettingsSnapshot>(json)!);
    }

    // ------------------------------------------------------------------
    // Error registry ↔ wire enum + retryable flag
    // ------------------------------------------------------------------

    [Fact]
    public void ErrorRegistryMatchesTheWireEnumAndRetryPolicy()
    {
        using JsonDocument registry = LoadErrorsRegistry();
        JsonElement[] entries = registry.RootElement.GetProperty("codes").EnumerateArray().ToArray();

        string[] registered = entries.Select(e => e.GetProperty("code").GetString()!).ToArray();
        string[] declared = Enum.GetValues<HttpV2ErrorCode>()
            .Select<HttpV2ErrorCode, string>(WireName)
            .ToArray();
        Assert.Equal(registered.Order(), declared.Order());
        Assert.Equal(18, registered.Length);

        foreach (JsonElement entry in entries)
        {
            HttpV2ErrorCode code = ParseCode(entry.GetProperty("code").GetString()!);
            string expectedCategory = entry.GetProperty("category").GetString()!;
            ErrorCategory category = Enum.GetValues<ErrorCategory>()
                .First(c => WireName<ErrorCategory>(c) == expectedCategory);
            Assert.Equal(expectedCategory, WireName<ErrorCategory>(category));
            Assert.Equal(entry.GetProperty("retryable").GetBoolean(), IsRetryable(code));
        }
    }

    [Fact]
    public void SchemaVersionIsTwo()
    {
        Assert.Equal(2, HttpV2Schema.Version);
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private static void AssertDeepRoundTrip(JsonElement expected, Func<string, object> deserialize)
    {
        object value = deserialize(expected.GetRawText());
        JsonNode expectedNode = JsonNode.Parse(expected.GetRawText())!;
        JsonNode actualNode = JsonNode.Parse(HttpV2Json.Serialize(value, value.GetType()))!;
        Assert.True(JsonNode.DeepEquals(expectedNode, actualNode),
            $"round-trip mismatch for {value.GetType().Name}:\nexpected: {expectedNode}\nactual:   {actualNode}");
    }

    private static JsonDocument LoadGolden() =>
        JsonDocument.Parse(File.ReadAllText(Path.Combine(GoldenDirectory, "golden", "golden.json")));

    private static JsonDocument LoadErrorsRegistry() =>
        JsonDocument.Parse(File.ReadAllText(Path.Combine(GoldenDirectory, "errors.json")));

    private static string WireName<T>(T value) where T : struct, Enum =>
        typeof(T).GetField(value.ToString())?
            .GetCustomAttributes(typeof(JsonStringEnumMemberNameAttribute), false)
            .Cast<JsonStringEnumMemberNameAttribute>()
            .SingleOrDefault()?.Name ?? value.ToString();

    private static HttpV2ErrorCode ParseCode(string wire) =>
        Enum.GetValues<HttpV2ErrorCode>().First(c => WireName(c) == wire);

    private static bool IsRetryable(HttpV2ErrorCode code) => code switch
    {
        HttpV2ErrorCode.QuotaExceeded => false,
        HttpV2ErrorCode.ValidationError => false,
        HttpV2ErrorCode.Unauthorized => false,
        HttpV2ErrorCode.ForbiddenLoopback => false,
        HttpV2ErrorCode.JobNotFound => false,
        HttpV2ErrorCode.ResourceNotFound => false,
        HttpV2ErrorCode.JobNotCancellable => false,
        HttpV2ErrorCode.JobNotRetryable => false,
        HttpV2ErrorCode.InputExpired => false,
        HttpV2ErrorCode.PinCapacityConflict => false,
        HttpV2ErrorCode.Cancelled => false,
        HttpV2ErrorCode.AdapterProtocolViolation => false,
        HttpV2ErrorCode.ProtocolMismatch => false,
        // Retryable: OOM, transient, draining, backend unavailable, internal.
        _ => true,
    };

    private static string FindV2Directory()
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
                string candidate = Path.Combine(
                    directory.FullName,
                    "packages",
                    "vibeocr-contracts-py",
                    "src",
                    "vibeocr",
                    "runtime_contracts");
                if (File.Exists(Path.Combine(candidate, "errors.json")))
                {
                    return candidate;
                }

                directory = directory.Parent;
            }
        }

        throw new DirectoryNotFoundException(
            "Could not locate vibeocr/runtime_contracts from test output.");
    }
}
