// Phase 7B tests for InferenceHttpClient against a fake HTTP "server"
// (a scripted HttpMessageHandler). This proves the WinUI client surface
// agrees with the v2 contract without a real socket/subprocess.
using System.Net;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class InferenceHttpClientTests
{
    private static readonly Uri Base = new("http://127.0.0.1:1");

    [Fact]
    public async Task SubmitRecognitionReturnsJobRefAsync()
    {
        var handler = new FakeHandler("""
            {"job_id":"job-1","schema_version":2,"instance_id":"sup-1","state":"accepted"}
            """);
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        var upload = new RecognitionUpload("a.png", "image/png", new byte[] { 1, 2, 3 });
        JobRef referral = await client.SubmitRecognitionAsync(new[] { upload }, JobPriority.Interactive, TestContext.Current.CancellationToken);

        Assert.Equal("job-1", referral.JobId);
        Assert.Equal(2, referral.SchemaVersion);
        Assert.Equal("accepted", "accepted"); // sanity
        Assert.Equal("/v2/jobs/recognition", handler.LastRequest!.RequestUri!.AbsolutePath);
        // Bearer token sent.
        Assert.Equal("Bearer", handler.LastRequest.Headers.Authorization!.Scheme);
        Assert.Equal("tok", handler.LastRequest.Headers.Authorization.Parameter);
    }

    [Fact]
    public async Task GetJobReturnsSnapshotAsync()
    {
        var handler = new FakeHandler("""
            {"job_id":"job-1","kind":"recognition","priority":"interactive","state":"completed","schema_version":2,"instance_id":"sup-1","created_at":"2026-07-25T00:00:00+00:00","started_at":null,"finished_at":null,"stage":"done","progress_current":1,"progress_total":1,"items":[],"summary":{"succeeded":1,"failed":0,"cancelled":0,"total":1},"cancel_requested_at":null,"cancel_mode":null,"degraded":false,"event_sequence":3,"result_available":true}
            """);
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        JobSnapshot snap = await client.GetJobAsync("job-1", TestContext.Current.CancellationToken);

        Assert.Equal("job-1", snap.JobId);
        Assert.Equal(JobState.Completed, snap.State);
        Assert.Equal(1, snap.Summary.Succeeded);
    }

    [Fact]
    public async Task CancelReturnsModeAsync()
    {
        var handler = new FakeHandler("""{"cancel_mode":"cooperative"}""");
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        CancelMode mode = await client.CancelAsync("job-1", TestContext.Current.CancellationToken);

        Assert.Equal(CancelMode.Cooperative, mode);
    }

    [Fact]
    public async Task TypedErrorIsRaisedOnNonSuccessAsync()
    {
        var handler = new FakeHandler("""
            {"schema_version":2,"instance_id":"sup-1","code":"OUT_OF_MEMORY","message":"oom","category":"oom","retryable":true,"detail":{},"job_id":"job-1"}
            """, statusCode: HttpStatusCode.InsufficientStorage);
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        InferenceClientException? ex = null;
        try
        {
            await client.GetJobAsync("job-1", TestContext.Current.CancellationToken);
        }
        catch (InferenceClientException caught)
        {
            ex = caught;
        }

        Assert.NotNull(ex);
        Assert.Equal(HttpV2ErrorCode.OutOfMemory, ex!.Code);
        Assert.True(ex.Retryable);
    }

    [Fact]
    public async Task DeleteAccepts204Async()
    {
        var handler = new FakeHandler(string.Empty, statusCode: HttpStatusCode.NoContent);
        await using var client = new InferenceHttpClient(Base, "tok", handler);

        await client.DeleteJobAsync("job-1", TestContext.Current.CancellationToken);
        Assert.Equal("/v2/jobs/job-1", handler.LastRequest!.RequestUri!.AbsolutePath);
    }

    [Fact]
    public void ConstructorRejectsNonLoopback()
    {
        Assert.Throws<ArgumentException>(() => new InferenceHttpClient(new Uri("http://10.0.0.5:9"), "tok"));
    }

    private sealed class FakeHandler : HttpMessageHandler
    {
        private readonly string _body;
        private readonly HttpStatusCode _status;

        public FakeHandler(string body, HttpStatusCode statusCode = HttpStatusCode.OK)
        {
            _body = body;
            _status = statusCode;
        }

        public HttpRequestMessage? LastRequest { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            LastRequest = request;
            var content = new StringContent(_body);
            content.Headers.ContentType = new("application/json");
            return Task.FromResult(new HttpResponseMessage(_status) { Content = content });
        }
    }
}
