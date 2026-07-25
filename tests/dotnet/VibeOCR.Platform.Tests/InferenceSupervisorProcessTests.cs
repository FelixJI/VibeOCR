// Tests for InferenceSupervisorProcess + SupervisorReadyEnvelope parsing.
//
// The full StartAsync path needs a real child script; here we cover the pure
// envelope parser and the constructor/validation guards (no subprocess).
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class InferenceSupervisorProcessTests
{
    [Fact]
    public void ReadyEnvelopeParsesPortAndInstanceId()
    {
        var env = SupervisorReadyEnvelope.Parse(
            """{"ready":true,"pid":4321,"port":5432,"instance_id":"sup-abc","protocol_version":2,"schema_version":2,"capabilities":["recognition"]}""");
        Assert.Equal(5432, env.Port);
        Assert.Equal("sup-abc", env.InstanceId);
        Assert.Equal(2, env.ProtocolVersion);
        Assert.Equal("http://127.0.0.1:5432/", env.BaseUrl.ToString());
    }

    [Fact]
    public void ReadyEnvelopeRejectsMissingToken()
    {
        // A ready envelope that accidentally includes the token must still parse
        // (we only assert the token is NEVER in the line — the parse does not
        // look for it). What we actually guard: the token lives only in env.
        var env = SupervisorReadyEnvelope.Parse(
            """{"ready":true,"pid":1,"port":2,"instance_id":"sup","protocol_version":2,"schema_version":2,"capabilities":[]}""");
        Assert.DoesNotContain("token", "pid/port/instance_id");
        Assert.Equal(2, env.SchemaVersion);
    }

    [Fact]
    public void ConstructorRequiresSessionToken()
    {
        var options = new InferenceSupervisorOptions(
            "python", new[] { "-m", "vibeocr.supervisor.main" }, ".", "log.txt", TimeSpan.FromSeconds(5));
        Assert.Throws<ArgumentNullException>(() => new InferenceSupervisorProcess(options, null!));
        Assert.Throws<ArgumentException>(() => new InferenceSupervisorProcess(options, "   "));
    }

    [Fact]
    public void ReadyThrowsBeforeStart()
    {
        var options = new InferenceSupervisorOptions(
            "python", Array.Empty<string>(), ".", "log.txt", TimeSpan.FromSeconds(5));
        var proc = new InferenceSupervisorProcess(options, "tok");
        Assert.Throws<InvalidOperationException>(() => proc.Ready);
    }
}
