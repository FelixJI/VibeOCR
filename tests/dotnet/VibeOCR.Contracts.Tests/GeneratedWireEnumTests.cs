using System.Text.Json;
using VibeOCR.Runtime.Contracts.Generated.Wire;
using Xunit;

namespace VibeOCR.Contracts.Tests;

public sealed class GeneratedWireEnumTests
{
    [Fact]
    public void GeneratedEnumUsesPinnedWireString()
    {
        Assert.Equal("\"load\"", JsonSerializer.Serialize(ProgressPhase.Load));
        Assert.Equal(
            ProgressPhase.Delete,
            JsonSerializer.Deserialize<ProgressPhase>("\"delete\""));
    }

    [Theory]
    [InlineData("\"LOAD\"")]
    [InlineData("\"unknown\"")]
    [InlineData("0")]
    public void GeneratedEnumRejectsNonWireValues(string json)
    {
        Assert.Throws<JsonException>(
            () => JsonSerializer.Deserialize<ProgressPhase>(json));
    }
}
