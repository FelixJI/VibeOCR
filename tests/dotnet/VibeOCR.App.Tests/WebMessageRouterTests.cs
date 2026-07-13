using System.Text.Json;
using VibeOCR.App.Web;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class WebMessageRouterTests
{
    [Theory]
    [InlineData("https://evil.example/index.html")]
    [InlineData("http://app.vibeocr/index.html")]
    [InlineData("file:///C:/preview/index.html")]
    [InlineData("javascript:alert(1)")]
    public void PreviewHostRejectsArbitraryNavigation(string uri)
    {
        Assert.False(PreviewHost.IsNavigationAllowed(new Uri(uri)));
    }

    [Fact]
    public void PreviewHostAllowsOnlyMappedHttpsResources()
    {
        Assert.True(PreviewHost.IsNavigationAllowed(new Uri("https://app.vibeocr/index.html")));
        Assert.True(PreviewHost.IsNavigationAllowed(new Uri("https://app.vibeocr/assets/preview.js")));
        Assert.False(PreviewHost.IsNavigationAllowed(new Uri("https://app.vibeocr.evil.test/index.html")));
    }

    [Theory]
    [InlineData("{\"version\":2,\"kind\":\"event\",\"id\":\"7acddbb5-3c18-4bf1-bd97-49a8ddaf5414\",\"type\":\"preview.ready\",\"payload\":{}}")]
    [InlineData("{\"version\":1,\"kind\":\"event\",\"id\":\"7acddbb5-3c18-4bf1-bd97-49a8ddaf5414\",\"type\":\"unknown.event\",\"payload\":{}}")]
    [InlineData("{\"version\":1,\"kind\":\"event\",\"id\":\"7acddbb5-3c18-4bf1-bd97-49a8ddaf5414\",\"type\":\"preview.ready\",\"payload\":{},\"extra\":true}")]
    [InlineData("{\"version\":1,\"kind\":\"mystery\",\"id\":\"7acddbb5-3c18-4bf1-bd97-49a8ddaf5414\",\"type\":\"preview.ready\",\"payload\":{}}")]
    public void RouterRejectsUnknownVersionMessageAndFields(string json)
    {
        var router = new WebMessageRouter();

        Assert.Throws<WebBridgeProtocolException>(() =>
            router.Handle(json, "https://app.vibeocr/index.html"));
    }

    [Fact]
    public void RouterRejectsExternalSourceAndOversizedMessage()
    {
        var router = new WebMessageRouter(maxMessageBytes: 128);
        string valid = EventMessage("preview.ready", new { });

        Assert.Throws<WebBridgeProtocolException>(() =>
            router.Handle(valid, "https://evil.example/index.html"));
        Assert.Throws<WebBridgeProtocolException>(() =>
            router.Handle(EventMessage("preview.ready", new { value = new string('x', 200) }),
                "https://app.vibeocr/index.html"));
    }

    [Fact]
    public async Task RequestResponseCorrelationCompletesMatchingPendingCall()
    {
        var router = new WebMessageRouter();
        string? outbound = null;
        Task<JsonElement> pending = router.RequestAsync(
            "preview.setState",
            new { status = "ready" },
            message => outbound = message,
            TestContext.Current.CancellationToken);
        Assert.NotNull(outbound);
        using JsonDocument request = JsonDocument.Parse(outbound);
        Guid id = request.RootElement.GetProperty("id").GetGuid();

        bool handled = router.Handle(
            JsonSerializer.Serialize(new
            {
                version = 1,
                kind = "response",
                id,
                type = "preview.setState",
                payload = new { accepted = true },
            }),
            "https://app.vibeocr/index.html");
        JsonElement response = await pending;

        Assert.True(handled);
        Assert.True(response.GetProperty("accepted").GetBoolean());
        Assert.Equal(0, router.PendingCount);
    }

    [Fact]
    public async Task PreCancelledRequestIsNeverPosted()
    {
        var router = new WebMessageRouter();
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        bool posted = false;

        Task<JsonElement> pending = router.RequestAsync(
            "preview.setState",
            new { status = "ready" },
            _ => posted = true,
            cancellation.Token);

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => pending);
        Assert.False(posted);
        Assert.Equal(0, router.PendingCount);
    }

    [Fact]
    public void HtmlPayloadRemainsDataAndNeverBecomesMarkup()
    {
        var router = new WebMessageRouter();
        WebBridgeMessage? received = null;
        router.MessageReceived += message => received = message;
        string attack = "<img src=x onerror=alert(1)>";

        router.Handle(EventMessage("editor.changed", new { text = attack }),
            "https://app.vibeocr/index.html");

        Assert.NotNull(received);
        Assert.Equal(attack, received.Payload.GetProperty("text").GetString());
    }

    private static string EventMessage(string type, object payload) => JsonSerializer.Serialize(new
    {
        version = 1,
        kind = "event",
        id = Guid.NewGuid(),
        type,
        payload,
    });
}
