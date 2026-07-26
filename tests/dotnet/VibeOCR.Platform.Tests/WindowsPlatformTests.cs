using VibeOCR.Platform.Windows;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class WindowsPlatformTests
{
    [Fact]
    public async Task SingleInstanceForwardsSecondaryArguments()
    {
        string name = $"VibeOCR-test-{Guid.NewGuid():N}";
        var forwarded = new TaskCompletionSource<IReadOnlyList<string>>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        await using var primary = new SingleInstanceService(
            name,
            args =>
            {
                forwarded.TrySetResult(args);
                return Task.CompletedTask;
            });
        await using var secondary = new SingleInstanceService(name, _ => Task.CompletedTask);

        Assert.True(primary.IsPrimary);
        Assert.False(secondary.IsPrimary);
        await secondary.ForwardAsync(
            ["--open", @"C:\input files\scan.png"],
            TestContext.Current.CancellationToken);

        IReadOnlyList<string> received = await forwarded.Task.WaitAsync(
            TimeSpan.FromSeconds(5),
            TestContext.Current.CancellationToken);
        Assert.Equal(["--open", @"C:\input files\scan.png"], received);
    }

    [Fact]
    public void HotkeyReportsConflictAndAlwaysReleasesRegistration()
    {
        var native = new FakeHotkeyNative { CanRegister = false };
        using var service = new GlobalHotkeyService(native);
        Assert.Throws<HotkeyRegistrationException>(() =>
            service.Register(42, HotkeyModifiers.Control | HotkeyModifiers.Shift, 0x4F));

        native.CanRegister = true;
        using IDisposable registration = service.Register(
            42,
            HotkeyModifiers.Control | HotkeyModifiers.Shift,
            0x4F);
        registration.Dispose();
        registration.Dispose();

        Assert.Equal(2, native.RegisterCalls);
        Assert.Equal(1, native.UnregisterCalls);
    }

    [Fact]
    public void TrayIconDisposeRemovesIconExactlyOnce()
    {
        var native = new FakeTrayNative();
        var service = new TrayIconService(native);
        service.Show((nint)123, 0x401, "VibeOCR");

        service.Dispose();
        service.Dispose();

        Assert.Equal(1, native.AddCalls);
        Assert.Equal(1, native.DeleteCalls);
    }

    [Theory]
    [InlineData(120, 125, 63)]
    [InlineData(144, 150, 75)]
    [InlineData(192, 200, 100)]
    public void DpiTransformHandlesNegativeMonitorOrigins(
        int dpi,
        int expectedX,
        int expectedY)
    {
        var monitor = new MonitorGeometry(
            new LogicalPoint(-1600, -200),
            new PhysicalRectangle(-2000, -250, 2000, 1250),
            dpi,
            dpi);

        PhysicalPoint actual = ScreenCaptureService.LogicalToPhysical(
            new LogicalPoint(-1500, -150),
            monitor);

        Assert.Equal(-2000 + expectedX, actual.X);
        Assert.Equal(-250 + expectedY, actual.Y);
    }

    [Fact]
    public async Task CaptureReturnsSharedBgraDescriptorForNegativeCoordinates()
    {
        var native = new FakeScreenCaptureNative();
        await using var service = new ScreenCaptureService(Guid.NewGuid(), native);
        var bounds = new PhysicalRectangle(-640, -120, 2, 2);

        CapturedFrame frame = service.Capture(bounds, TimeSpan.FromMinutes(1));
        byte[] bytes = service.Read(frame);

        Assert.Equal(bounds, native.LastBounds);
        Assert.Equal(2, frame.Width);
        Assert.Equal(2, frame.Height);
        Assert.Equal(8, frame.Stride);
        Assert.Equal("BGRA8", frame.PixelFormat);
        Assert.Equal(16, frame.Pixels.Length);
        Assert.Equal(Enumerable.Range(0, 16).Select(value => (byte)value), bytes);
    }

    [Fact]
    [Trait("Category", "WindowsIntegration")]
    public async Task RealGdiCaptureProducesReadableSharedBgra()
    {
        await using var service = new ScreenCaptureService(Guid.NewGuid());

        CapturedFrame frame = service.Capture(
            new PhysicalRectangle(0, 0, 2, 2),
            TimeSpan.FromMinutes(1));

        Assert.Equal(16, service.Read(frame).Length);
        Assert.Equal(8, frame.Stride);
    }

    [Fact]
    [Trait("Category", "WindowsIntegration")]
    public void RealHotkeyCanBeReleasedAndRegisteredAgain()
    {
        using var service = new GlobalHotkeyService();
        IDisposable? first = null;
        uint selectedKey = 0;
        for (uint key = 0x87; key >= 0x82; key--)
        {
            try
            {
                first = service.Register(
                    0x6A10,
                    HotkeyModifiers.Control | HotkeyModifiers.Alt | HotkeyModifiers.Shift |
                        HotkeyModifiers.NoRepeat,
                    key);
                selectedKey = key;
                break;
            }
            catch (HotkeyRegistrationException)
            {
            }
        }

        Assert.NotNull(first);
        first.Dispose();
        using IDisposable second = service.Register(
            0x6A10,
            HotkeyModifiers.Control | HotkeyModifiers.Alt | HotkeyModifiers.Shift |
                HotkeyModifiers.NoRepeat,
            selectedKey);
    }

    private sealed class FakeHotkeyNative : IHotkeyNativeMethods
    {
        public bool CanRegister { get; set; }
        public int RegisterCalls { get; private set; }
        public int UnregisterCalls { get; private set; }

        public bool Register(nint windowHandle, int id, HotkeyModifiers modifiers, uint virtualKey)
        {
            RegisterCalls++;
            return CanRegister;
        }

        public bool Unregister(nint windowHandle, int id)
        {
            UnregisterCalls++;
            return true;
        }
    }

    private sealed class FakeTrayNative : ITrayIconNativeMethods
    {
        public int AddCalls { get; private set; }
        public int DeleteCalls { get; private set; }

        public bool Add(Guid id, nint windowHandle, uint callbackMessage, string tooltip)
        {
            AddCalls++;
            return true;
        }

        public bool Delete(Guid id, nint windowHandle)
        {
            DeleteCalls++;
            return true;
        }
    }

    private sealed class FakeScreenCaptureNative : IScreenCaptureNativeMethods
    {
        public PhysicalRectangle LastBounds { get; private set; }

        public byte[] CaptureBgra(PhysicalRectangle bounds)
        {
            LastBounds = bounds;
            return Enumerable.Range(0, checked(bounds.Width * bounds.Height * 4))
                .Select(value => (byte)value)
                .ToArray();
        }
    }

}
