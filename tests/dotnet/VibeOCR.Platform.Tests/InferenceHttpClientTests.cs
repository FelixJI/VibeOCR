using System.Net;
using System.Text.Json;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class InferenceHttpClientTests
{
    private static readonly Uri Base = new("http://127.0.0.1:1");

    [Fact]
    public async Task SubmitPostsManifestAndAttachmentsToGenericJobsRouteAsync()
    {
        var handler = new FakeHandler("""
            {"job_id":"job-1","schema_version":2,"instance_id":"sup-1","state":"accepted","items":[]}
            """);
        await using var client = new InferenceHttpClient(Base, "tok", handler);
        SubmitRequest request = UploadRequest();

        JobRef referral = await client.SubmitAsync(
            request,
            new Dictionary<string, SubmitUpload>
            {
                ["file-a"] = new("image/png", new byte[] { 1, 2, 3 }),
            },
            TestContext.Current.CancellationToken);

        Assert.Equal("job-1", referral.JobId);
        Assert.Equal(HttpMethod.Post, handler.LastMethod);
        Assert.Equal("/v2/jobs", handler.LastPath);
        Assert.Equal("Bearer", handler.LastAuthorizationScheme);
        Assert.Equal("tok", handler.LastAuthorizationParameter);
        Assert.StartsWith("multipart/form-data", handler.LastContentType);
        Assert.Contains("name=manifest", handler.LastBody);
        Assert.Contains("\"request_id\":\"request-1\"", handler.LastBody);
        Assert.Contains("name=file-a", handler.LastBody);
        Assert.Contains("filename=a.png", handler.LastBody);
    }

    [Fact]
    public async Task SubmitRequiresUploadsToExactlyMatchManifestAsync()
    {
        var handler = new FakeHandler("{}");
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        await Assert.ThrowsAsync<ArgumentException>(
            () => client.SubmitAsync(
                UploadRequest(),
                new Dictionary<string, SubmitUpload>(),
                TestContext.Current.CancellationToken));

        Assert.Null(handler.LastPath);
    }

    [Fact]
    public async Task ObserveReturnsAtomicJobUpdateAsync()
    {
        var handler = new FakeHandler("""
            {
              "snapshot":{"job_id":"job-1","kind":"recognition","priority":"interactive","state":"running"},
              "events":[{"sequence":4,"stage":"recognize","item_id":"item-1","timestamp":null,"detail":{}}],
              "outcomes":[],
              "through_sequence":4,
              "more":false,
              "schema_version":2
            }
            """);
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        JobUpdate update = await client.ObserveAsync(
            "job-1",
            3,
            TestContext.Current.CancellationToken);

        Assert.Equal(JobState.Running, update.Snapshot.State);
        Assert.Equal(4, update.ThroughSequence);
        Assert.Single(update.Events);
        Assert.Equal("/v2/jobs/job-1/observe", handler.LastPath);
        Assert.Equal("?after_sequence=3", handler.LastQuery);
    }

    [Fact]
    public async Task CommandPostsTypedRequestAndParsesCancelResultAsync()
    {
        var handler = new FakeHandler("""
            {
              "schema_version":2,
              "instance_id":"sup-1",
              "command_id":"command-1",
              "kind":"cancel",
              "cancel_mode":"cooperative",
              "job_ref":null
            }
            """);
        await using var client = new InferenceHttpClient(Base, "tok", handler);
        var command = new JobCommand
        {
            CommandId = "command-1",
            Kind = JobCommandKind.Cancel,
            JobId = "job-1",
        };

        JobCommandResult result = await client.CommandAsync(
            command,
            TestContext.Current.CancellationToken);

        Assert.Equal(JobCommandKind.Cancel, result.Kind);
        Assert.Equal(CancelMode.Cooperative, result.CancelMode);
        Assert.Null(result.JobRef);
        Assert.Equal("/v2/jobs/command", handler.LastPath);
        Assert.Equal("application/json", handler.LastContentType);
        Assert.Contains("\"command_id\":\"command-1\"", handler.LastBody);
        Assert.Contains("\"kind\":\"cancel\"", handler.LastBody);
    }

    [Fact]
    public async Task CommandParsesRetryJobRefAsync()
    {
        var handler = new FakeHandler("""
            {
              "schema_version":2,
              "instance_id":"sup-1",
              "command_id":"command-2",
              "kind":"retry",
              "cancel_mode":null,
              "job_ref":{"job_id":"job-2","schema_version":2,"instance_id":"sup-1","state":"accepted","items":[]}
            }
            """);
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        JobCommandResult result = await client.CommandAsync(
            new JobCommand
            {
                CommandId = "command-2",
                Kind = JobCommandKind.Retry,
                JobId = "job-1",
            },
            TestContext.Current.CancellationToken);

        Assert.Equal("job-2", result.JobRef?.JobId);
        Assert.Null(result.CancelMode);
    }

    [Fact]
    public async Task TypedErrorIsRaisedOnNonSuccessAsync()
    {
        var handler = new FakeHandler("""
            {"schema_version":2,"instance_id":"sup-1","code":"OUT_OF_MEMORY","message":"oom","category":"oom","retryable":true,"detail":{},"job_id":"job-1"}
            """, statusCode: HttpStatusCode.InsufficientStorage);
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        InferenceClientException exception = await Assert.ThrowsAsync<InferenceClientException>(
            () => client.ObserveAsync(
                "job-1",
                0,
                TestContext.Current.CancellationToken));

        Assert.Equal(HttpV2ErrorCode.OutOfMemory, exception.Code);
        Assert.True(exception.Retryable);
    }

    [Fact]
    public void ConstructorRejectsNonLoopback()
    {
        Assert.Throws<ArgumentException>(
            () => new InferenceHttpClient(new Uri("http://10.0.0.5:9"), "tok"));
    }

    private static SubmitRequest UploadRequest() => new()
    {
        RequestId = "request-1",
        Kind = JobKind.Recognition,
        Priority = JobPriority.Interactive,
        Pipeline = new PipelineSelection { PipelineId = "OCR" },
        Items =
        [
            new SubmitItem
            {
                ClientItemKey = "client-a",
                Ordinal = 0,
                DisplayName = "a.png",
                Source = new Dictionary<string, JsonElement>
                {
                    ["type"] = JsonSerializer.Deserialize<JsonElement>("\"upload.v1\""),
                    ["attachment"] = JsonSerializer.Deserialize<JsonElement>("\"file-a\""),
                },
            },
        ],
    };

    private sealed class FakeHandler : HttpMessageHandler
    {
        private readonly string _body;
        private readonly HttpStatusCode _status;

        public FakeHandler(string body, HttpStatusCode statusCode = HttpStatusCode.OK)
        {
            _body = body;
            _status = statusCode;
        }

        public HttpMethod? LastMethod { get; private set; }
        public string? LastPath { get; private set; }
        public string? LastQuery { get; private set; }
        public string? LastAuthorizationScheme { get; private set; }
        public string? LastAuthorizationParameter { get; private set; }
        public string? LastContentType { get; private set; }
        public string? LastBody { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            LastMethod = request.Method;
            LastPath = request.RequestUri?.AbsolutePath;
            LastQuery = request.RequestUri?.Query;
            LastAuthorizationScheme = request.Headers.Authorization?.Scheme;
            LastAuthorizationParameter = request.Headers.Authorization?.Parameter;
            LastContentType = request.Content?.Headers.ContentType?.MediaType;
            LastBody = request.Content is null
                ? null
                : await request.Content.ReadAsStringAsync(cancellationToken);
            var content = new StringContent(_body);
            content.Headers.ContentType = new("application/json");
            return new HttpResponseMessage(_status) { Content = content };
        }
    }
}
