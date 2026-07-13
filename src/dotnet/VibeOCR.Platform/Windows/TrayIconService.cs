using System.ComponentModel;
using System.Runtime.InteropServices;

namespace VibeOCR.Platform.Windows;

public interface ITrayIconNativeMethods
{
    bool Add(Guid id, nint windowHandle, uint callbackMessage, string tooltip);
    bool Delete(Guid id, nint windowHandle);
}

public sealed class TrayIconService : IDisposable
{
    private readonly Guid _id = Guid.NewGuid();
    private readonly ITrayIconNativeMethods _native;
    private nint _windowHandle;
    private bool _visible;
    private bool _disposed;

    public TrayIconService(ITrayIconNativeMethods? native = null)
    {
        _native = native ?? new TrayIconNativeMethods();
    }

    public void Show(nint windowHandle, uint callbackMessage, string tooltip)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentException.ThrowIfNullOrWhiteSpace(tooltip);
        if (_visible)
        {
            throw new InvalidOperationException("Tray icon is already visible.");
        }

        if (!_native.Add(_id, windowHandle, callbackMessage, tooltip))
        {
            throw new Win32Exception(Marshal.GetLastPInvokeError(), "Failed to add tray icon.");
        }

        _windowHandle = windowHandle;
        _visible = true;
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        if (_visible)
        {
            _native.Delete(_id, _windowHandle);
            _visible = false;
        }
    }

    private sealed class TrayIconNativeMethods : ITrayIconNativeMethods
    {
        private const uint AddMessage = 0;
        private const uint DeleteMessage = 2;
        private const uint MessageFlag = 0x0001;
        private const uint IconFlag = 0x0002;
        private const uint TipFlag = 0x0004;
        private const uint GuidFlag = 0x0020;

        public bool Add(Guid id, nint windowHandle, uint callbackMessage, string tooltip)
        {
            NotifyIconData data = CreateData(id, windowHandle);
            data.Flags = MessageFlag | IconFlag | TipFlag | GuidFlag;
            data.CallbackMessage = callbackMessage;
            data.Icon = LoadIcon(0, (nint)32512);
            data.Tip = tooltip.Length > 127 ? tooltip[..127] : tooltip;
            return ShellNotifyIcon(AddMessage, ref data);
        }

        public bool Delete(Guid id, nint windowHandle)
        {
            NotifyIconData data = CreateData(id, windowHandle);
            data.Flags = GuidFlag;
            return ShellNotifyIcon(DeleteMessage, ref data);
        }

        private static NotifyIconData CreateData(Guid id, nint windowHandle) =>
            new()
            {
                Size = (uint)Marshal.SizeOf<NotifyIconData>(),
                WindowHandle = windowHandle,
                Id = 1,
                Guid = id,
                Tip = string.Empty,
                Info = string.Empty,
                InfoTitle = string.Empty,
            };

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct NotifyIconData
        {
            public uint Size;
            public nint WindowHandle;
            public uint Id;
            public uint Flags;
            public uint CallbackMessage;
            public nint Icon;

            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string Tip;

            public uint State;
            public uint StateMask;

            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
            public string Info;

            public uint TimeoutOrVersion;

            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
            public string InfoTitle;

            public uint InfoFlags;
            public Guid Guid;
            public nint BalloonIcon;
        }

        [DllImport(
            "shell32.dll",
            EntryPoint = "Shell_NotifyIconW",
            CharSet = CharSet.Unicode,
            ExactSpelling = true,
            SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool ShellNotifyIcon(uint message, ref NotifyIconData data);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern nint LoadIcon(nint instance, nint iconName);
    }
}
