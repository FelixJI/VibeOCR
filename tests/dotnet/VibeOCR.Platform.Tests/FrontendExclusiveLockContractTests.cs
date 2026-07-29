using VibeOCR.Platform.Windows;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class FrontendExclusiveLockContractTests
{
    [Fact]
    public void UsesVersionTwoCrossProductMutexName() =>
        Assert.Equal(
            @"Local\VibeOCR.Frontend.Exclusive.v2",
            FrontendExclusiveLock.MutexName);
}
