using System.Text.Json;
using System.Text.Json.Nodes;
using VibeOCR.Contracts.HttpV2;
using Xunit;

namespace VibeOCR.Contracts.Tests;

public sealed class HttpV2JobInterfaceContractTests
{
    [Fact]
    public void SubmitRequestSerializesLikePythonToPayload()
    {
        var request = new SubmitRequest
        {
            RequestId = "req-1",
            Kind = JobKind.Recognition,
            Priority = JobPriority.Background,
            Pipeline = new PipelineSelection
            {
                PipelineId = "OCR",
                Options =
                {
                    ["use_doc_orientation_classify"] = Element("false"),
                },
            },
            Items =
            [
                new SubmitItem
                {
                    ClientItemKey = "file-a",
                    Ordinal = 0,
                    DisplayName = "a.png",
                    Source = new Dictionary<string, JsonElement>
                    {
                        ["type"] = Element("\"upload.v1\""),
                        ["attachment"] = Element("\"file-a\""),
                    },
                },
            ],
        };

        AssertJsonEqual(
            """
            {
              "request_id": "req-1",
              "kind": "recognition",
              "priority": "background",
              "pipeline": {
                "pipeline_id": "OCR",
                "options_version": 1,
                "options": {"use_doc_orientation_classify": false}
              },
              "items": [{
                "client_item_key": "file-a",
                "ordinal": 0,
                "display_name": "a.png",
                "source": {"type": "upload.v1", "attachment": "file-a"}
              }],
              "schema_version": 2,
              "parameters": {}
            }
            """,
            HttpV2Json.Serialize(request));
    }

    [Fact]
    public void JobRefAndSnapshotCarryStableItemMappingAndSemanticIntent()
    {
        var item = new JobItem
        {
            ItemId = "it-1",
            DisplayName = "a.png",
            State = ItemState.Queued,
            ClientItemKey = "file-a",
            Ordinal = 0,
        };
        var reference = new JobRef
        {
            JobId = "job-1",
            State = JobState.Queued,
            Items = [item],
        };
        var snapshot = new JobSnapshot
        {
            JobId = "job-1",
            Kind = JobKind.Recognition,
            Priority = JobPriority.Background,
            State = JobState.Queued,
            RequestId = "req-1",
            SourceJobId = "job-0",
            Pipeline = new PipelineSelection { PipelineId = "OCR" },
            Items = [item],
        };

        JobRef parsedRef = HttpV2Json.Deserialize<JobRef>(HttpV2Json.Serialize(reference))!;
        JobSnapshot parsedSnapshot =
            HttpV2Json.Deserialize<JobSnapshot>(HttpV2Json.Serialize(snapshot))!;

        Assert.Equal("file-a", parsedRef.Items[0].ClientItemKey);
        Assert.Equal(0, parsedRef.Items[0].Ordinal);
        Assert.Null(parsedRef.Items[0].SourceItemId);
        Assert.Equal("req-1", parsedSnapshot.RequestId);
        Assert.Equal("job-0", parsedSnapshot.SourceJobId);
        Assert.Equal("OCR", parsedSnapshot.Pipeline!.PipelineId);
        Assert.Equal(1, parsedSnapshot.Pipeline.OptionsVersion);
    }

    [Fact]
    public void JobUpdateSerializesAtomicOutcomeDefaultsLikePythonToPayload()
    {
        var update = new JobUpdate
        {
            Snapshot = new JobSnapshot
            {
                JobId = "job-1",
                Kind = JobKind.Recognition,
                Priority = JobPriority.Interactive,
                State = JobState.Completed,
            },
            Events =
            [
                new StageEvent
                {
                    Sequence = 3,
                    Stage = "item_succeeded",
                    ItemId = "it-1",
                },
            ],
            Outcomes =
            [
                new ItemOutcome
                {
                    ItemId = "it-1",
                    State = ItemState.Succeeded,
                    Attempt = 0,
                    PayloadType = "ocr.v1",
                    Payload = new Dictionary<string, JsonElement>
                    {
                        ["raw_text"] = Element("\"\""),
                    },
                },
            ],
            ThroughSequence = 3,
        };

        string json = HttpV2Json.Serialize(update);
        JsonElement root = JsonDocument.Parse(json).RootElement;
        JsonElement outcome = root.GetProperty("outcomes")[0];

        Assert.Equal(2, root.GetProperty("schema_version").GetInt32());
        Assert.False(root.GetProperty("more").GetBoolean());
        Assert.Equal(3, root.GetProperty("through_sequence").GetInt32());
        Assert.Equal("ocr.v1", outcome.GetProperty("payload_type").GetString());
        Assert.Equal("", outcome.GetProperty("payload").GetProperty("raw_text").GetString());
        Assert.Equal(JsonValueKind.Null, outcome.GetProperty("error_code").ValueKind);
        Assert.Empty(outcome.GetProperty("error_detail").EnumerateObject());
    }

    [Theory]
    [InlineData(JobCommandKind.Cancel, "cancel")]
    [InlineData(JobCommandKind.Retry, "retry")]
    [InlineData(JobCommandKind.Forget, "forget")]
    public void JobCommandKindsUsePythonWireNames(JobCommandKind kind, string wireName)
    {
        var command = new JobCommand
        {
            CommandId = "cmd-1",
            Kind = kind,
            JobId = "job-1",
        };

        JsonElement root = JsonDocument.Parse(HttpV2Json.Serialize(command)).RootElement;

        Assert.Equal(wireName, root.GetProperty("kind").GetString());
        Assert.Empty(root.GetProperty("item_ids").EnumerateArray());
        Assert.Equal(JsonValueKind.Null, root.GetProperty("priority_override").ValueKind);
    }

    [Theory]
    [InlineData(
        """{"pipeline_id":"OCR","options_version":1,"options":{},"unexpected":true}""",
        typeof(PipelineSelection))]
    [InlineData(
        """{"command_id":"cmd-1","kind":"cancel","job_id":"job-1","item_ids":[],"priority_override":null,"unexpected":true}""",
        typeof(JobCommand))]
    [InlineData(
        """{"item_id":"it-1","state":"failed","attempt":1,"payload_type":null,"payload":null,"error_code":"BAD_INPUT","error_detail":{},"unexpected":true}""",
        typeof(ItemOutcome))]
    public void NewRecordsRejectUnknownMembers(string json, Type type)
    {
        Assert.Throws<JsonException>(() =>
            JsonSerializer.Deserialize(json, HttpV2JsonContext.Default.GetTypeInfo(type)!));
    }

    [Fact]
    public void SourceGeneratedContextRegistersAllJobInterfaceTypes()
    {
        Type[] types =
        [
            typeof(PipelineSelection),
            typeof(SubmitItem),
            typeof(SubmitRequest),
            typeof(ItemOutcome),
            typeof(JobUpdate),
            typeof(JobCommand),
        ];

        foreach (Type type in types)
        {
            Assert.NotNull(HttpV2JsonContext.Default.GetTypeInfo(type));
        }
    }

    private static JsonElement Element(string json) =>
        JsonDocument.Parse(json).RootElement.Clone();

    private static void AssertJsonEqual(string expected, string actual)
    {
        JsonNode expectedNode = JsonNode.Parse(expected)!;
        JsonNode actualNode = JsonNode.Parse(actual)!;
        Assert.True(
            JsonNode.DeepEquals(expectedNode, actualNode),
            $"JSON mismatch:\nexpected: {expectedNode}\nactual:   {actualNode}");
    }
}
