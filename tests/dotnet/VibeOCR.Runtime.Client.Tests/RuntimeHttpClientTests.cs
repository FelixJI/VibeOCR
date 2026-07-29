using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Runtime.Client;
using VibeOCR.Runtime.Contracts.Generated;
using Xunit;

namespace VibeOCR.Runtime.Client.Tests;

public sealed class RuntimeHttpClientTests
{
    [Fact]
    public async Task SendsGeneratedPathWithBearerTokenAsync()
    {
        var handler = new FakeHandler("""{"ready":true}""");
        await using var client = new RuntimeHttpClient(
            new Uri("http://127.0.0.1:1"),
            "session-token",
            handler);

        using HttpResponseMessage response = await client.GetAsync(
            RuntimeOperationPaths.GetRuntimeHealth,
            TestContext.Current.CancellationToken);
        await client.EnsureSuccessAsync(
            response,
            TestContext.Current.CancellationToken);

        Assert.Equal("/v2/health", handler.Path);
        Assert.Equal("Bearer", handler.AuthorizationScheme);
        Assert.Equal("session-token", handler.AuthorizationParameter);
    }

    [Fact]
    public async Task ConvertsErrorEnvelopeToTypedExceptionAsync()
    {
        var handler = new FakeHandler(
            """
            {"schema_version":2,"instance_id":"sup-1","code":"OUT_OF_MEMORY","message":"oom","category":"oom","retryable":true,"detail":{},"job_id":null}
            """,
            HttpStatusCode.InsufficientStorage);
        await using var client = new RuntimeHttpClient(
            new Uri("http://localhost:1"),
            "token",
            handler);

        using HttpResponseMessage response = await client.GetAsync(
            RuntimeOperationPaths.GetRuntimeResidency,
            TestContext.Current.CancellationToken);
        RuntimeClientException error = await Assert.ThrowsAsync<RuntimeClientException>(
            () => client.EnsureSuccessAsync(
                response,
                TestContext.Current.CancellationToken));

        Assert.Equal(HttpV2ErrorCode.OutOfMemory, error.Code);
        Assert.True(error.Retryable);
    }

    [Fact]
    public async Task RejectsRemoteAndUnboundPathsAsync()
    {
        Assert.Throws<ArgumentException>(
            () => new RuntimeHttpClient(new Uri("http://10.0.0.5:1"), "token"));

        var client = new RuntimeHttpClient(
            new Uri("http://[::1]:1"),
            "token",
            new FakeHandler("{}"));
        await Assert.ThrowsAsync<ArgumentException>(
            () => client.GetAsync(
                RuntimeOperationPaths.ObserveJob,
                TestContext.Current.CancellationToken));
    }

    [Theory]
    [InlineData("//example.com/v2/health")]
    [InlineData("/\\example.com/v2/health")]
    [InlineData("/v2\\health")]
    [InlineData("/v2/health\r\nX-Test: injected")]
    [InlineData("http://127.0.0.1:2/v2/health")]
    public async Task RejectsPathsThatCanEscapeAuthorityWithoutSendingBearerAsync(
        string path)
    {
        var handler = new FakeHandler("{}");
        await using var client = new RuntimeHttpClient(
            new Uri("http://127.0.0.1:1"),
            "secret-bearer",
            handler);

        await Assert.ThrowsAsync<ArgumentException>(
            () => client.GetAsync(path, TestContext.Current.CancellationToken));

        Assert.Equal(0, handler.RequestCount);
        Assert.Null(handler.AuthorizationParameter);
    }

    [Fact]
    public async Task OwnsMultipartJsonBinaryAndNdjsonCodecsAsync()
    {
        var handler = new FakeHandler("{}");
        await using var client = new RuntimeHttpClient(
            new Uri("http://127.0.0.1:1"),
            "token",
            handler);
        using MultipartFormDataContent multipart = client.CreateMultipartContent(
            """{"schema_version":2}""",
            new Dictionary<string, RuntimeUpload>
            {
                ["file-a"] = new(
                    "a.png",
                    new byte[] { 1, 2, 3 },
                    "image/png"),
            });
        using HttpResponseMessage sent = await client.PostAsync(
            RuntimeOperationPaths.SubmitJob,
            multipart,
            TestContext.Current.CancellationToken);

        Assert.StartsWith("multipart/form-data", handler.ContentType);
        Assert.Contains("name=manifest", handler.RequestBody);
        Assert.Contains("name=file-a", handler.RequestBody);
        Assert.Contains("filename=a.png", handler.RequestBody);

        using var jsonResponse = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("""{"value":"ok"}""", Encoding.UTF8, "application/json"),
        };
        TestPayload payload = await client.ReadJsonAsync(
            jsonResponse,
            TypeInfo<TestPayload>(),
            TestContext.Current.CancellationToken);
        Assert.Equal("ok", payload.Value);

        using var binaryResponse = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new ByteArrayContent(new byte[] { 4, 5, 6 }),
        };
        binaryResponse.Content.Headers.ContentType = new MediaTypeHeaderValue("image/png");
        Assert.Equal(
            new byte[] { 4, 5, 6 },
            await client.ReadBinaryAsync(
                binaryResponse,
                "image/png",
                TestContext.Current.CancellationToken));

        using var ndjsonResponse = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                "{\"value\":\"one\"}\n{\"value\":\"two\"}\n",
                Encoding.UTF8,
                "application/x-ndjson"),
        };
        var values = new List<string>();
        await foreach (TestPayload item in client.ReadNdjsonAsync(
            ndjsonResponse,
            TypeInfo<TestPayload>(),
            TestContext.Current.CancellationToken))
        {
            values.Add(item.Value);
        }
        Assert.Equal(["one", "two"], values);
    }

    [Fact]
    public async Task PropagatesCancellationToTransportAsync()
    {
        var handler = new CancellationHandler();
        await using var client = new RuntimeHttpClient(
            new Uri("http://127.0.0.1:1"),
            "token",
            handler);
        using var cancellation = new CancellationTokenSource();

        Task<HttpResponseMessage> request = client.GetAsync(
            RuntimeOperationPaths.GetRuntimeHealth,
            cancellation.Token);
        await handler.Started.Task;
        await cancellation.CancelAsync();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => request);
        Assert.True(handler.ObservedCancellation);
    }

    private static JsonTypeInfo<T> TypeInfo<T>() =>
        (JsonTypeInfo<T>)JsonSerializerOptions.Default.GetTypeInfo(typeof(T));

    public sealed record TestPayload(
        [property: JsonPropertyName("value")] string Value);

    private sealed class FakeHandler : HttpMessageHandler
    {
        private readonly string _body;
        private readonly HttpStatusCode _status;

        public FakeHandler(
            string body,
            HttpStatusCode status = HttpStatusCode.OK)
        {
            _body = body;
            _status = status;
        }

        public string? Path { get; private set; }

        public string? AuthorizationScheme { get; private set; }

        public string? AuthorizationParameter { get; private set; }

        public int RequestCount { get; private set; }

        public string? ContentType { get; private set; }

        public string? RequestBody { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            RequestCount++;
            Path = request.RequestUri?.AbsolutePath;
            AuthorizationScheme = request.Headers.Authorization?.Scheme;
            AuthorizationParameter = request.Headers.Authorization?.Parameter;
            ContentType = request.Content?.Headers.ContentType?.MediaType;
            RequestBody = request.Content is null
                ? null
                : await request.Content.ReadAsStringAsync(cancellationToken);
            return new HttpResponseMessage(_status)
            {
                Content = new StringContent(_body),
            };
        }
    }

    private sealed class CancellationHandler : HttpMessageHandler
    {
        public TaskCompletionSource Started { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public bool ObservedCancellation { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Started.SetResult();
            try
            {
                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            }
            catch (OperationCanceledException)
            {
                ObservedCancellation = true;
                throw;
            }

            throw new InvalidOperationException("unreachable");
        }
    }
}
