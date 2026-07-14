using VibeOCR.App.Features.Shell;
using VibeOCR.App.Features.Update;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class ShellFeatureTests
{
    [Fact]
    public void ApplyHotkeyWithConflictLeavesRegisteredUnchanged()
    {
        var registrar = new FakeHotkeyRegistrar { Accept = false, Conflict = "已占用" };
        var shell = new ShellViewModel(registrar, new FakeStartupRegistrar());

        shell.PendingHotkey = "Ctrl+Shift+Q";
        shell.ApplyHotkey();

        Assert.Equal("Ctrl+Alt+Q", shell.RegisteredHotkey);
        Assert.Contains("已占用", shell.HotkeyStatus);
    }

    [Fact]
    public void ApplyHotkeySuccessUpdatesRegistered()
    {
        var registrar = new FakeHotkeyRegistrar { Accept = true };
        var shell = new ShellViewModel(registrar, new FakeStartupRegistrar());

        shell.PendingHotkey = "Ctrl+Shift+Q";
        shell.ApplyHotkey();

        Assert.Equal("Ctrl+Shift+Q", shell.RegisteredHotkey);
        Assert.Equal("快捷键已更新", shell.HotkeyStatus);
    }

    [Fact]
    public void ApplyHotkeyEmptyRejected()
    {
        var shell = new ShellViewModel(new FakeHotkeyRegistrar(), new FakeStartupRegistrar());
        shell.PendingHotkey = "   ";
        shell.ApplyHotkey();
        Assert.Equal("快捷键不能为空", shell.HotkeyStatus);
    }

    [Fact]
    public void ApplySameHotkeyIsNoOp()
    {
        var shell = new ShellViewModel(new FakeHotkeyRegistrar(), new FakeStartupRegistrar());
        shell.PendingHotkey = shell.RegisteredHotkey;
        shell.ApplyHotkey();
        Assert.Equal("快捷键未改变", shell.HotkeyStatus);
    }

    [Fact]
    public void SetStartWithSystemPersistsAndReflectsFailure()
    {
        var startup = new FakeStartupRegistrar { Ok = false };
        var shell = new ShellViewModel(new FakeHotkeyRegistrar(), startup);

        shell.SetStartWithSystem(true);

        Assert.False(shell.StartWithSystem);
    }

    [Fact]
    public async Task UpdateCheckSurfacesNewVersion()
    {
        var source = new FakeUpdateSource { Version = "1.2.0", Available = true };
        var vm = new UpdateViewModel(source);

        await vm.CheckAsync(TestContext.Current.CancellationToken);

        Assert.True(vm.UpdateAvailable);
        Assert.Equal("1.2.0", vm.LatestVersion);
        Assert.Contains("1.2.0", vm.Status);
    }

    [Fact]
    public async Task UpdateCheckNetworkErrorLocalized()
    {
        var source = new FakeUpdateSource { Throw = true };
        var vm = new UpdateViewModel(source);

        await vm.CheckAsync(TestContext.Current.CancellationToken);

        Assert.Contains("网络", vm.Status);
    }

    [Fact]
    public async Task UpdateDownloadWithoutAvailableIsNoOp()
    {
        var source = new FakeUpdateSource { Available = false };
        var vm = new UpdateViewModel(source);
        await vm.CheckAsync(TestContext.Current.CancellationToken);

        await vm.DownloadAndVerifyAsync(TestContext.Current.CancellationToken);

        Assert.Equal(string.Empty, vm.Status == "已是最新版本" ? string.Empty : vm.Status);
        Assert.False(vm.IsBusy);
    }

    [Fact]
    public async Task UpdateDownloadVerifySuccess()
    {
        var source = new FakeUpdateSource { Version = "1.2.0", Available = true, VerifyOk = true };
        bool shutdownRequested = false;
        var vm = new UpdateViewModel(source, () => shutdownRequested = true);
        await vm.CheckAsync(TestContext.Current.CancellationToken);

        await vm.DownloadAndVerifyAsync(TestContext.Current.CancellationToken);

        Assert.True(source.UpdaterLaunched);
        Assert.True(shutdownRequested);
        Assert.Contains("即将退出", vm.Status);
    }

    [Fact]
    public async Task UpdateDoesNotExitWhenUpdaterFailsToStart()
    {
        var source = new FakeUpdateSource
        {
            Version = "1.2.0",
            Available = true,
            VerifyOk = true,
            LaunchOk = false,
        };
        bool shutdownRequested = false;
        var vm = new UpdateViewModel(source, () => shutdownRequested = true);
        await vm.CheckAsync(TestContext.Current.CancellationToken);

        await vm.DownloadAndVerifyAsync(TestContext.Current.CancellationToken);

        Assert.False(shutdownRequested);
        Assert.Contains("启动失败", vm.Status);
    }

    private sealed class FakeHotkeyRegistrar : IHotkeyRegistrar
    {
        public bool Accept { get; set; } = true;
        public string? Conflict { get; set; }
        public bool Register(string hotkey, out string? conflict)
        {
            conflict = Conflict;
            return Accept;
        }
        public void Unregister() { }
    }

    private sealed class FakeStartupRegistrar : IStartupRegistrar
    {
        public bool Ok { get; set; } = true;
        public bool SetEnabled(bool enabled) => Ok;
    }

    private sealed class FakeUpdateSource : IUpdateSource
    {
        public string Version { get; set; } = "0.0.0";
        public bool Available { get; set; }
        public bool VerifyOk { get; set; } = true;
        public bool LaunchOk { get; set; } = true;
        public bool UpdaterLaunched { get; private set; }
        public bool Throw { get; set; }

        public Task<(string Version, bool Available)> FetchLatestAsync(CancellationToken cancellationToken)
            => Throw ? throw new IOException("network down") : Task.FromResult((Version, Available));

        public Task<bool> DownloadVerifyAsync(CancellationToken cancellationToken)
            => Task.FromResult(VerifyOk);

        public Task<bool> LaunchUpdaterAsync(CancellationToken cancellationToken)
        {
            UpdaterLaunched = true;
            return Task.FromResult(LaunchOk);
        }
    }
}
