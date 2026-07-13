using VibeOCR.App.Features.Shell;
using VibeOCR.App.Features.Update;

namespace VibeOCR.App.Features.Shell;

/// <summary>
/// Default no-op implementations of the shell/update hooks. The real platform
/// services (GlobalHotkeyService, TrayIconService, SingleInstanceService from
/// Task 2.6) are wired when the full desktop shell is activated; these no-ops
/// keep the side-by-side build runnable and the view models testable.
/// </summary>
internal sealed class NoopHotkeyRegistrar : IHotkeyRegistrar
{
    public bool Register(string hotkey, out string? conflict)
    {
        conflict = null;
        return true;
    }

    public void Unregister() { }
}

internal sealed class NoopStartupRegistrar : IStartupRegistrar
{
    public bool SetEnabled(bool enabled) => true;
}

internal sealed class NoopUpdateSource : IUpdateSource
{
    public Task<(string Version, bool Available)> FetchLatestAsync(CancellationToken cancellationToken)
        => Task.FromResult(("0.0.0", false));

    public Task<bool> DownloadVerifyAsync(CancellationToken cancellationToken)
        => Task.FromResult(true);
}
