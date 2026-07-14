using System.Runtime.InteropServices;

namespace VibeOCR.Platform.Windows;

public sealed record WindowMessage(uint Id, nuint WParam, nint LParam);

/// <summary>Routes Win32 messages for an unpackaged WinUI window.</summary>
public sealed class WindowMessageService : IDisposable
{
    private const nuint SubclassId = 0x564F4352;
    private readonly nint _windowHandle;
    private readonly SubclassProc _callback;
    private bool _disposed;

    public WindowMessageService(nint windowHandle)
    {
        if (windowHandle == 0)
        {
            throw new ArgumentException("A valid window handle is required.", nameof(windowHandle));
        }

        _windowHandle = windowHandle;
        _callback = OnWindowMessage;
        if (!SetWindowSubclass(_windowHandle, _callback, SubclassId, 0))
        {
            throw new InvalidOperationException("Failed to install the WinUI message router.");
        }
    }

    public event EventHandler<WindowMessage>? MessageReceived;

    private nint OnWindowMessage(
        nint windowHandle,
        uint message,
        nuint wParam,
        nint lParam,
        nuint subclassId,
        nuint referenceData)
    {
        MessageReceived?.Invoke(this, new WindowMessage(message, wParam, lParam));
        return DefSubclassProc(windowHandle, message, wParam, lParam);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        RemoveWindowSubclass(_windowHandle, _callback, SubclassId);
    }

    private delegate nint SubclassProc(
        nint windowHandle,
        uint message,
        nuint wParam,
        nint lParam,
        nuint subclassId,
        nuint referenceData);

    [DllImport("comctl32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowSubclass(
        nint windowHandle,
        SubclassProc callback,
        nuint subclassId,
        nuint referenceData);

    [DllImport("comctl32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool RemoveWindowSubclass(
        nint windowHandle,
        SubclassProc callback,
        nuint subclassId);

    [DllImport("comctl32.dll")]
    private static extern nint DefSubclassProc(
        nint windowHandle,
        uint message,
        nuint wParam,
        nint lParam);
}
