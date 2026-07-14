using VibeOCR.App.Services;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class WindowLayoutStoreTests
{
    [Fact]
    public void LoadReturnsNullWhenFileMissing()
    {
        var store = new WindowLayoutStore(Path.Combine(Path.GetTempPath(), $"none-{Guid.NewGuid():N}.json"));
        Assert.Null(store.Load());
    }

    [Fact]
    public void SaveThenLoadRoundTripsGeometry()
    {
        string path = Path.Combine(Path.GetTempPath(), $"layout-{Guid.NewGuid():N}.json");
        try
        {
            var store = new WindowLayoutStore(path);
            var expected = new WindowGeometry(100, 200, 900, 600, IsMaximized: true);
            store.Save(expected);
            WindowGeometry? actual = store.Load();
            Assert.Equal(expected, actual);
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }

    [Fact]
    public void LoadReturnsNullOnCorruptJson()
    {
        string path = Path.Combine(Path.GetTempPath(), $"corrupt-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, "{ this is not json");
        try
        {
            var store = new WindowLayoutStore(path);
            Assert.Null(store.Load());
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void SaveOverwritesPreviousGeometry()
    {
        string path = Path.Combine(Path.GetTempPath(), $"overwrite-{Guid.NewGuid():N}.json");
        try
        {
            var store = new WindowLayoutStore(path);
            store.Save(new WindowGeometry(1, 2, 3, 4, false));
            store.Save(new WindowGeometry(5, 6, 7, 8, true));
            WindowGeometry? actual = store.Load();
            Assert.Equal(new WindowGeometry(5, 6, 7, 8, true), actual);
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }
}
