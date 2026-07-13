using System.ComponentModel;
using System.Runtime.InteropServices;

namespace VibeOCR.Platform.Windows;

[Flags]
public enum HotkeyModifiers : uint
{
    None = 0,
    Alt = 0x0001,
    Control = 0x0002,
    Shift = 0x0004,
    Windows = 0x0008,
    NoRepeat = 0x4000,
}

public interface IHotkeyNativeMethods
{
    bool Register(nint windowHandle, int id, HotkeyModifiers modifiers, uint virtualKey);
    bool Unregister(nint windowHandle, int id);
}

public sealed class HotkeyRegistrationException(string message, int nativeError = 0)
    : Win32Exception(nativeError, message);

public sealed class GlobalHotkeyService : IDisposable
{
    private readonly Dictionary<int, Registration> _registrations = [];
    private readonly IHotkeyNativeMethods _native;
    private readonly nint _windowHandle;
    private bool _disposed;

    public GlobalHotkeyService(IHotkeyNativeMethods? native = null, nint windowHandle = default)
    {
        _native = native ?? new HotkeyNativeMethods();
        _windowHandle = windowHandle;
    }

    public IDisposable Register(int id, HotkeyModifiers modifiers, uint virtualKey)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (id <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(id));
        }

        if (_registrations.ContainsKey(id))
        {
            throw new InvalidOperationException($"Hotkey id {id} is already registered.");
        }

        if (!_native.Register(_windowHandle, id, modifiers, virtualKey))
        {
            throw new HotkeyRegistrationException(
                "The global hotkey is already in use or could not be registered.",
                Marshal.GetLastPInvokeError());
        }

        var registration = new Registration(this, id);
        _registrations.Add(id, registration);
        return registration;
    }

    private void Release(int id)
    {
        if (_registrations.Remove(id))
        {
            _native.Unregister(_windowHandle, id);
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        foreach (Registration registration in _registrations.Values.ToArray())
        {
            registration.Dispose();
        }
    }

    private sealed class Registration(GlobalHotkeyService owner, int id) : IDisposable
    {
        private GlobalHotkeyService? _owner = owner;

        public void Dispose() => Interlocked.Exchange(ref _owner, null)?.Release(id);
    }

    private sealed class HotkeyNativeMethods : IHotkeyNativeMethods
    {
        public bool Register(nint windowHandle, int id, HotkeyModifiers modifiers, uint virtualKey) =>
            RegisterHotKey(windowHandle, id, modifiers, virtualKey);

        public bool Unregister(nint windowHandle, int id) => UnregisterHotKey(windowHandle, id);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool RegisterHotKey(
            nint windowHandle,
            int id,
            HotkeyModifiers modifiers,
            uint virtualKey);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool UnregisterHotKey(nint windowHandle, int id);
    }
}
