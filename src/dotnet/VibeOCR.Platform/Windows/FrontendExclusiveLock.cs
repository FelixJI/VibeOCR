using System.Runtime.InteropServices;

namespace VibeOCR.Platform.Windows;

/// <summary>
/// Cross-product exclusive lock ensuring the PySide Classic and WinUI Next
/// frontends never run simultaneously within one login session.
/// </summary>
/// <remarks>
/// <para>
/// Per ADR §6 and DUAL_UI_IMPLEMENTATION_PLAN.md §6.1: cross-product mutual
/// exclusion uses a single Windows named Mutex shared by both frontends.
/// Creating a Mutex is atomic and immune to exe renames, PID reuse and
/// simultaneous-launch races; the OS releases it automatically when the
/// frontend process exits or crashes. Supervisor process-tree ownership is
/// enforced separately by the frontend's Job Object.
/// </para>
/// <para>
/// This lock is distinct from the same-product single-instance Mutex
/// (<c>Local\VibeOCR-{profile}</c>): the same-product lock forwards CLI args
/// to the primary and activates it; this cross-product lock only prompts the
/// user to quit the other product — it never forwards, activates, or connects
/// to the other product's Supervisor.
/// </para>
/// <para>
/// The Python
/// <c>vibeocr.classic.utils.frontend_exclusive_lock.FrontendExclusiveLock</c>
/// must use the identical <see cref="MutexName"/> or mutual exclusion silently
/// breaks.
/// </para>
/// </remarks>
public sealed class FrontendExclusiveLock : IDisposable
{
    /// <summary>
    /// The shared cross-product Mutex name. Must match the Python constant
    /// <c>EXCLUSIVE_MUTEX_NAME</c> exactly.
    /// </summary>
    public const string MutexName = @"Local\VibeOCR.Frontend.Exclusive.v2";

    private readonly Mutex _mutex;
    private readonly bool _acquired;

    /// <summary>
    /// Attempts to atomically acquire the cross-product Mutex.
    /// </summary>
    /// <param name="name">
    /// Override Mutex name; defaults to <see cref="MutexName"/>. Used by tests
    /// to isolate from the production lock.
    /// </param>
    public FrontendExclusiveLock(string? name = null)
    {
        _mutex = new Mutex(initiallyOwned: false, name: name ?? MutexName, out bool createdNew);
        _acquired = createdNew;
    }

    /// <summary>
    /// <see langword="true"/> when this process holds the exclusive lock (no
    /// other VibeOCR product is running). <see langword="false"/> means another
    /// product holds it; the caller must show the quit prompt and exit without
    /// starting a Supervisor.
    /// </summary>
    public bool IsAcquired => _acquired;

    public void Dispose()
    {
        _mutex.Dispose();
    }

    /// <summary>
    /// Shows a modal "another VibeOCR product is running" prompt using the
    /// Win32 message box (no WinUI window exists yet at the check point).
    /// Blocks until the user dismisses it, then the caller exits.
    /// </summary>
    public static void ShowAnotherProductRunningPrompt()
    {
        // MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST = 0x1 | 0x40 | 0x10000 | 0x40000
        _ = MessageBoxW(
            IntPtr.Zero,
            "另一套 VibeOCR（经典版）正在运行。\n请先退出它，再重试。",
            "VibeOCR",
            0x00000040 | 0x00010000 | 0x00040000);
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);
}
