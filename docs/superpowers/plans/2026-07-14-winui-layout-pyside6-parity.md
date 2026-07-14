# WinUI 布局对齐 PySide6 + DPI 修复 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 WinUI 迁移后的界面发虚模糊（DPI 感知缺失）和各页布局大幅偏离 PySide6 的问题，纯布局复刻（不接后端逻辑）。

**Architecture:** 三块全局修复（app.manifest 声明 PerMonitorV2、窗口默认/最小尺寸、窗口几何记忆）+ 六个页面的 Grid 星号比例列重排参照 PySide6。占位控件统一灰色禁用 + "功能开发中"提示。

**Tech Stack:** WinUI 3 (WindowsAppSDK 2.2.0)、.NET 10、XAML、xunit.v3、Win32 P/Invoke（WM_GETMINMAXINFO 最小尺寸）。

**设计文档:** `docs/superpowers/specs/2026-07-14-winui-layout-pyside6-parity-design.md`

## Global Constraints

- 目标框架 `net10.0-windows10.0.17763.0`，LangVersion 14，`Nullable enable`，`TreatWarningsAsErrors=true`（来自 `Directory.Build.props`）。所有新代码必须 nullable 安全、零警告。
- 未打包应用：`WindowsPackageType=None`、`SelfContained=false`、`RuntimeIdentifier=win-x64`。
- 包版本由 `Directory.Packages.props` 集中管理（WindowsAppSDK 2.2.0），**不要**在 .csproj 内写版本号。
- **纯布局范围**：不写后端逻辑、不扩展 worker 契约、不实现占位控件功能。新增占位控件一律 `IsEnabled="False"` + "功能开发中"提示文案。现有可工作的控件（如热键/启动/托盘/更新检查）从关于页迁移到设置页时**保留其现有 click handler 绑定**（这是重定位，非新功能）。
- 中文 UI 文案保持与现版/PySide6 一致。
- 每个 Task 结尾必须 `dotnet build` 通过并提交。提交信息前缀用 `fix(winui):`（修复）/ `refactor(winui):`（重排）。

## File Structure

新增文件：
- `src/dotnet/VibeOCR.App/app.manifest` — DPI 感知 + OS 兼容声明。
- `src/dotnet/VibeOCR.App/Services/WindowLayoutStore.cs` — 窗口几何 JSON 持久化（可单测）。
- `src/dotnet/VibeOCR.App/Services/WindowGeometry.cs` — `record WindowGeometry(int X, int Y, int Width, int Height, bool IsMaximized)`。
- `src/dotnet/VibeOCR.App/Services/WindowMinSizeEnforcer.cs` — Win32 `WM_GETMINMAXINFO` 拦截，强制窗口最小尺寸。
- `tests/dotnet/VibeOCR.App.Tests/WindowLayoutStoreTests.cs` — `WindowLayoutStore` 单测。

修改文件：
- `src/dotnet/VibeOCR.App/VibeOCR.App.csproj` — 引用 manifest。
- `src/dotnet/VibeOCR.App/MainWindow.xaml.cs` — 窗口尺寸/几何/最小尺寸接入；构造签名加 `WindowLayoutStore`。
- `src/dotnet/VibeOCR.App/App.xaml.cs` — 构造 `WindowLayoutStore` 并传入 `MainWindow`；保存几何到 `OnAppWindowClosing`/`Closed`。
- `src/dotnet/VibeOCR.App/Views/RecognitionPage.xaml` — 两栏 400\*/500\* 重排。
- `src/dotnet/VibeOCR.App/Views/BatchPage.xaml` — 三栏 280\*/450\*/450\* 重排。
- `src/dotnet/VibeOCR.App/Views/QrCodePage.xaml` — 两栏 500\*/300\* + Pivot 重排。
- `src/dotnet/VibeOCR.App/Views/PdfPage.xaml` — 两栏 200\*/600\* 重排。
- `src/dotnet/VibeOCR.App/Views/SettingsPage.xaml(.cs)` — 分组补全；构造签名加 `ShellViewModel`（承载迁移来的热键/启动/托盘选项）。
- `src/dotnet/VibeOCR.App/Views/AboutPage.xaml(.cs)` — 双栏卡片重排；移除热键/启动/托盘/退出选项。
- `src/dotnet/VibeOCR.App/App.xaml.cs` — 设置页工厂注入 `ShellViewModel`。

---

## Task 1: WindowLayoutStore 窗口几何持久化（TDD）

**Files:**
- Create: `src/dotnet/VibeOCR.App/Services/WindowGeometry.cs`
- Create: `src/dotnet/VibeOCR.App/Services/WindowLayoutStore.cs`
- Test: `tests/dotnet/VibeOCR.App.Tests/WindowLayoutStoreTests.cs`

**Interfaces:**
- Produces: `record WindowGeometry(int X, int Y, int Width, int Height, bool IsMaximized)`；`class WindowLayoutStore { WindowLayoutStore(string filePath); WindowGeometry? Load(); void Save(WindowGeometry geometry); }`。`Load()` 在文件缺失/损坏时返回 `null`。

- [ ] **Step 1: 写失败测试**

创建 `tests/dotnet/VibeOCR.App.Tests/WindowLayoutStoreTests.cs`：

```csharp
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj --filter "FullyQualifiedName~WindowLayoutStoreTests"`
Expected: 编译失败（`WindowLayoutStore`/`WindowGeometry` 未定义）。

- [ ] **Step 3: 实现 WindowGeometry + WindowLayoutStore**

创建 `src/dotnet/VibeOCR.App/Services/WindowGeometry.cs`：

```csharp
namespace VibeOCR.App.Services;

public sealed record WindowGeometry(int X, int Y, int Width, int Height, bool IsMaximized);
```

创建 `src/dotnet/VibeOCR.App/Services/WindowLayoutStore.cs`：

```csharp
using System.IO;
using System.Text.Json;

namespace VibeOCR.App.Services;

public sealed class WindowLayoutStore
{
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);

    private readonly string _filePath;

    public WindowLayoutStore(string filePath) => _filePath = filePath;

    public WindowGeometry? Load()
    {
        if (!File.Exists(_filePath))
        {
            return null;
        }
        try
        {
            using FileStream stream = File.OpenRead(_filePath);
            return JsonSerializer.Deserialize<WindowGeometry>(stream, Options);
        }
        catch (JsonException)
        {
            return null;
        }
        catch (IOException)
        {
            return null;
        }
    }

    public void Save(WindowGeometry geometry)
    {
        string? directory = Path.GetDirectoryName(_filePath);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }
        using FileStream stream = File.Create(_filePath);
        JsonSerializer.Serialize(stream, geometry, Options);
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj --filter "FullyQualifiedName~WindowLayoutStoreTests"`
Expected: 4 passed。

- [ ] **Step 5: 提交**

```bash
git add src/dotnet/VibeOCR.App/Services/WindowGeometry.cs src/dotnet/VibeOCR.App/Services/WindowLayoutStore.cs tests/dotnet/VibeOCR.App.Tests/WindowLayoutStoreTests.cs
git commit -m "feat(winui): add WindowLayoutStore for window geometry persistence"
```

---

## Task 2: app.manifest 声明 PerMonitorV2 DPI 感知

**Files:**
- Create: `src/dotnet/VibeOCR.App/app.manifest`
- Modify: `src/dotnet/VibeOCR.App/VibeOCR.App.csproj`

**Interfaces:**
- 无新增公开接口。此 Task 是构建配置变更，靠 `dotnet build` + 运行时视觉验证（spec 7.2）。

- [ ] **Step 1: 创建 app.manifest**

创建 `src/dotnet/VibeOCR.App/app.manifest`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.0.0.0" name="VibeOCR.WinUI.app" />

  <!-- Per-Monitor v2 DPI awareness: 防止 Windows 对未打包窗口做位图虚拟化拉伸（界面发虚根因）。 -->
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2,PerMonitor</dpiAwareness>
      <activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">UTF-8</activeCodePage>
    </windowsSettings>
  </application>

  <!-- 兼容性：声明 Windows 10/11，确保获得现代 API 行为。 -->
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}" /> <!-- Windows 10/11 -->
    </application>
  </compatibility>
</assembly>
```

- [ ] **Step 2: 在 csproj 引用 manifest**

修改 `src/dotnet/VibeOCR.App/VibeOCR.App.csproj`，在第一个 `<PropertyGroup>` 内（`<Platforms>x64</Platforms>` 之后）加一行：

```xml
    <ApplicationManifest>app.manifest</ApplicationManifest>
```

- [ ] **Step 3: 构建确认 manifest 被打包**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded。无警告（`TreatWarningsAsErrors` 下任何警告即失败）。

确认 manifest 嵌入（检查产物）：
Run: `ls src/dotnet/VibeOCR.App/bin/Debug/net10.0-windows10.0.17763.0/win-x64/VibeOCR.WinUI.exe.manifest 2>/dev/null || ls src/dotnet/VibeOCR.App/obj/Debug/net10.0-windows10.0.17763.0/win-x64/VibeOCR.WinUI.exe.manifest 2>/dev/null`
Expected: 存在 `.manifest` 文件（.NET SDK 在 obj 目录生成嵌入用 manifest）。若路径不同，至少确认 obj 下有 manifest 文件。

- [ ] **Step 4: 提交**

```bash
git add src/dotnet/VibeOCR.App/app.manifest src/dotnet/VibeOCR.App/VibeOCR.App.csproj
git commit -m "fix(winui): declare PerMonitorV2 DPI awareness via app.manifest"
```

---

## Task 3: 窗口默认/最小尺寸 + 几何恢复/保存

**Files:**
- Create: `src/dotnet/VibeOCR.App/Services/WindowMinSizeEnforcer.cs`
- Modify: `src/dotnet/VibeOCR.App/MainWindow.xaml.cs`（构造签名加 `WindowLayoutStore`；接入几何恢复/保存 + 默认 900×600 + 最小尺寸）
- Modify: `src/dotnet/VibeOCR.App/App.xaml.cs`（构造 store、注入、关闭时保存）

**Interfaces:**
- Consumes: `WindowLayoutStore`（Task 1）、`WindowGeometry`（Task 1）。
- Produces: `WindowMinSizeEnforcer`（Win32 子类化拦截 `WM_GETMINMAXINFO`，强制窗口最小 720×480 逻辑像素）。

**说明（WinUI 3 最小尺寸）**：WinUI 3 无原生 `MinWidth/MinHeight` 窗口属性。需 Win32 子类化窗口过程，在 `WM_GETMINMAXINFO`（0x0024）时写入 `MINMAXINFO.ptMinTrackSize`。这是 WinUI 3 未打包应用的标准做法。

- [ ] **Step 1: 实现 WindowMinSizeEnforcer**

创建 `src/dotnet/VibeOCR.App/Services/WindowMinSizeEnforcer.cs`：

```csharp
using System.Runtime.InteropServices;

namespace VibeOCR.App.Services;

/// <summary>
/// 强制 WinUI 3 未打包窗口的最小逻辑尺寸，通过 Win32 子类化拦截 WM_GETMINMAXINFO。
/// </summary>
internal static class WindowMinSizeEnforcer
{
    private const int WM_GETMINMAXINFO = 0x0024;

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MINMAXINFO
    {
        public POINT Reserved;
        public POINT MaxSize;
        public POINT MaxPosition;
        public POINT MinTrackSize;
        public POINT MaxTrackSize;
    }

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern IntPtr SetWindowLongW(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern IntPtr GetWindowLongW(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtrW(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr GetWindowLongPtrW(IntPtr hWnd, int nIndex);

    private const int GWLP_WNDPROC = -4;

    private delegate IntPtr WndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    private static WndProc? _subclassProc;
    private static IntPtr _originalWndProc;
    private static int _minWidth;
    private static int _minHeight;

    /// <summary>对指定窗口句柄启用最小尺寸约束（逻辑像素）。</summary>
    public static void Apply(IntPtr hwnd, int minWidth, int minHeight)
    {
        _minWidth = minWidth;
        _minHeight = minHeight;
        _subclassProc = CustomWndProc;
        if (IntPtr.Size == 8)
        {
            _originalWndProc = GetWindowLongPtrW(hwnd, GWLP_WNDPROC);
            SetWindowLongPtrW(hwnd, GWLP_WNDPROC, Marshal.GetFunctionPointerForDelegate(_subclassProc));
        }
        else
        {
            _originalWndProc = GetWindowLongW(hwnd, GWLP_WNDPROC);
            SetWindowLongW(hwnd, GWLP_WNDPROC, Marshal.GetFunctionPointerForDelegate(_subclassProc));
        }
    }

    private static IntPtr CustomWndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        if (msg == WM_GETMINMAXINFO && lParam != IntPtr.Zero)
        {
            MINMAXINFO mmi = Marshal.PtrToStructure<MINMAXINFO>(lParam);
            mmi.MinTrackSize = new POINT { X = _minWidth, Y = _minHeight };
            Marshal.StructureToPtr(mmi, lParam, fDeleteOld: false);
        }
        return CallWindowProc(hWnd, msg, wParam, lParam);
    }

    [DllImport("user32.dll")]
    private static extern IntPtr CallWindowProcW(IntPtr lpPrevWndFunc, IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    private static IntPtr CallWindowProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
        => CallWindowProcW(_originalWndProc, hWnd, msg, wParam, lParam);
}
```

- [ ] **Step 2: 改 MainWindow 构造签名 + 接入几何/尺寸**

修改 `src/dotnet/VibeOCR.App/MainWindow.xaml.cs`：

(a) 加 using（文件顶部 using 块末尾）：
```csharp
using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using VibeOCR.App.Services;
using WinRT.Interop;
```

(b) 加 Win32 P/Invoke 与常量（类内，字段区上方）：
```csharp
    private const int DefaultWidth = 900;
    private const int DefaultHeight = 600;
    private const int MinWidth = 720;
    private const int MinHeight = 480;

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool IsZoomed(IntPtr hWnd);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
```

(c) 加字段：
```csharp
    private readonly WindowLayoutStore _layoutStore;
```

(d) 改构造函数签名（新增最后一个参数 `WindowLayoutStore layoutStore`）并把 `InitializeComponent();` 之后的初始化逻辑替换。**完整新构造函数**：

```csharp
    public MainWindow(DiagnosticsViewModel diagnostics, PortableLayout layout, Func<RecognitionViewModel> recognitionFactory, Func<BatchViewModel> batchFactory, Func<QrCodePage> qrCodePageFactory, Func<PdfPage> pdfPageFactory, Func<SettingsPage> settingsPageFactory, Func<AboutPage> aboutPageFactory, WindowLayoutStore layoutStore)
    {
        _diagnostics = diagnostics; _layout = layout; _recognitionFactory = recognitionFactory; _batchFactory = batchFactory; _qrCodePageFactory = qrCodePageFactory; _pdfPageFactory = pdfPageFactory; _settingsPageFactory = settingsPageFactory; _aboutPageFactory = aboutPageFactory; _layoutStore = layoutStore;
        InitializeComponent(); Title = "VibeOCR WinUI"; RootNavigation.SelectedItem = RootNavigation.MenuItems[0]; ShowHome();
        ApplyPersistedOrDefaultGeometry();
    }
```

(e) 加方法 `ApplyPersistedOrDefaultGeometry`（在构造函数之后）：

```csharp
    private void ApplyPersistedOrDefaultGeometry()
    {
        IntPtr hwnd = WindowNative.GetWindowHandle(this);
        WindowMinSizeEnforcer.Apply(hwnd, MinWidth, MinHeight);

        WindowGeometry? saved = _layoutStore.Load();
        var presenter = (OverlappedPresenter)AppWindow.Presenter;
        if (saved is { } geometry)
        {
            AppWindow.MoveAndResize(new RectInt32(geometry.X, geometry.Y, geometry.Width, geometry.Height));
            if (geometry.IsMaximized)
            {
                presenter.Maximize();
            }
        }
        else
        {
            // 默认 900x600 居中到主显示器工作区。
            DisplayArea area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Nearest);
            RectInt32 work = area.WorkArea;
            int x = work.X + Math.Max(0, (work.Width - DefaultWidth) / 2);
            int y = work.Y + Math.Max(0, (work.Height - DefaultHeight) / 2);
            AppWindow.MoveAndResize(new RectInt32(x, y, DefaultWidth, DefaultHeight));
        }
    }

    internal WindowGeometry? CaptureGeometry()
    {
        IntPtr hwnd = WindowNative.GetWindowHandle(this);
        if (IsIconic(hwnd))
        {
            // 最小化时不写回，避免下次以最小化尺寸恢复。
            return null;
        }
        bool maximized = IsZoomed(hwnd);
        GetWindowRect(hwnd, out RECT rect);
        return new WindowGeometry(rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top, maximized);
    }
```

注：`AppWindow.MoveAndResize`/`RectInt32` 的单位是物理像素（与 DPI 相关）。最小尺寸 720×480 同样按物理像素估算；在 100% DPI 显示器上等价于逻辑 720×480，高 DPI 下等比放大——对"防拖太小"目的足够。

- [ ] **Step 3: 改 App.xaml.cs 构造 store、注入、关闭时保存**

修改 `src/dotnet/VibeOCR.App/App.xaml.cs`：

(a) 加 using（顶部 using 块末尾）：
```csharp
using VibeOCR.App.Services;
```

(b) 加字段（`_window` 附近）：
```csharp
    private WindowLayoutStore? _windowLayoutStore;
```

(c) 在 `OnLaunched` 中、构造 `MainWindow` 之前加 store 创建（在 `RecordMilestone(diagnostics, "T1", ...)` 之后）：
```csharp
        _windowLayoutStore = new WindowLayoutStore(
            Path.Combine(layout.DataRoot, "winui-layout.json"));
```

(d) 改 `new MainWindow(...)` 调用，末尾加 ` _windowLayoutStore` 实参：
```csharp
        _window = new MainWindow(
            diagnostics,
            layout,
            () => new RecognitionViewModel(
                _workerGateway,
                new InputService(() => WinRT.Interop.WindowNative.GetWindowHandle(_window!))),
            () => new BatchViewModel(
                _workerGateway,
                new BatchFileSource(() => WinRT.Interop.WindowNative.GetWindowHandle(_window!))),
            () =>
            {
                nint handle = WinRT.Interop.WindowNative.GetWindowHandle(_window!);
                var qrViewModel = new QrCodeViewModel(
                    _workerGateway,
                    new QrCodeInputService(() => handle));
                return new QrCodePage(
                    qrViewModel,
                    new QrCodeSaveCommands(_workerGateway, new QrCodeSavePlatform(() => handle)));
            },
            () =>
            {
                nint handle = WinRT.Interop.WindowNative.GetWindowHandle(_window!);
                return new PdfPage(
                    new PdfViewModel(_workerGateway, new PdfFileSource(() => handle)));
            },
            () => new SettingsPage(new SettingsViewModel(_workerGateway), _shellViewModel!),
            () =>
            {
                return new AboutPage(
                    _shellViewModel ?? throw new InvalidOperationException("Desktop shell is unavailable."),
                    _updateViewModel ?? throw new InvalidOperationException("Update service is unavailable."));
            },
            _windowLayoutStore);
```

**注意**：设置页工厂现在传 `_shellViewModel!`。`_shellViewModel` 在 `InitializeDesktopShell`（第 127 行后）填充，而工厂在用户点"设置"时才调用（此时已填充），因此非 null。

**本步必须同时改 SettingsPage 构造函数签名**，否则此处两参调用与单参构造不匹配，构建失败。把 `src/dotnet/VibeOCR.App/Views/SettingsPage.xaml.cs` 的构造改为（保留 `LoadSnapshotAsync` 与原 handler，本步只加参数 + Shell 属性，**不加新 handler**——handler 留到 Task 8）：

```csharp
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using VibeOCR.App.Features.Settings;
using VibeOCR.App.Features.Shell;

namespace VibeOCR.App.Views;

public sealed partial class SettingsPage : Page
{
    public SettingsPage(SettingsViewModel viewModel, ShellViewModel shell)
    {
        ViewModel = viewModel;
        Shell = shell;
        InitializeComponent();
        _ = viewModel.LoadSnapshotAsync(CancellationToken.None);
    }

    public SettingsViewModel ViewModel { get; }
    public ShellViewModel Shell { get; }

    private async void OnRefreshClicked(object sender, RoutedEventArgs e)
        => await ViewModel.LoadSnapshotAsync(CancellationToken.None);

    private async void OnSwitchBackendClicked(object sender, RoutedEventArgs e)
        => await ViewModel.SwitchBackendAsync(ViewModel.PendingBackend, CancellationToken.None);
}
```

此时 `SettingsPage.xaml` 仍是旧版（未引用 Shell），但构造已加 Shell 参数——编译通过（Shell 参数暂未在 XAML 绑定使用，不报错）。Task 8 再重写 XAML 并补迁移 handler。本步 `git add` 必须包含 `SettingsPage.xaml.cs`。

**顺序问题**：`_window` 构造在 `InitializeDesktopShell` 之前（第 92 行 vs 127 行）。`MainWindow` 构造函数会调用 `ApplyPersistedOrDefaultGeometry`，此时 hwnd 已就绪（构造后 Activate 之前 handle 已可用）。但 `WindowMinSizeEnforcer.Apply` 需要 hwnd——`WindowNative.GetWindowHandle(this)` 在 `InitializeComponent` 之后即可用，OK。

(e) 在 `ShutdownAndExitAsync` 中保存几何（`await StopWorkerAsync();` 之前加）：
```csharp
            if (_window is not null && _windowLayoutStore is not null && _window.CaptureGeometry() is { } geometry)
            {
                _windowLayoutStore.Save(geometry);
            }
```

- [ ] **Step 4: 构建确认**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded，无警告。

Run: `dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj`
Expected: 全部通过（含 WindowLayoutStoreTests；ShellTests 等不受影响）。

- [ ] **Step 5: 提交**

```bash
git add src/dotnet/VibeOCR.App/Services/WindowMinSizeEnforcer.cs src/dotnet/VibeOCR.App/MainWindow.xaml.cs src/dotnet/VibeOCR.App/App.xaml.cs src/dotnet/VibeOCR.App/Views/SettingsPage.xaml.cs
git commit -m "fix(winui): default 900x600 window + min size + geometry persistence"
```

---

## Task 4: 单图识别页两栏重排（400\*/500\*）

**Files:**
- Modify: `src/dotnet/VibeOCR.App/Views/RecognitionPage.xaml`

**Interfaces:**
- 无新增。仅 XAML 结构调整，保留所有现有 `x:Name`（`PreviewWebView`/`PreviewErrorPanel`/`PreviewError`/`PreviewBridgeStatus`）与 click handler 名。

- [ ] **Step 1: 重写 RecognitionPage.xaml 为两栏**

整个 `<Page>` 内容替换为：

```xml
<Page
    x:Class="VibeOCR.App.Views.RecognitionPage"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    AllowDrop="True" DragOver="OnDragOver" Drop="OnDrop">
    <Grid ColumnSpacing="8" Padding="8">
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="400*" MinWidth="300" />
            <ColumnDefinition Width="500*" />
        </Grid.ColumnDefinitions>

        <!-- 左栏：操作栏 + 预览 -->
        <Grid Grid.Column="0" RowSpacing="4">
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" />
                <RowDefinition Height="*" />
            </Grid.RowDefinitions>
            <StackPanel Orientation="Horizontal" Spacing="4">
                <Button Click="OnFileClicked" Content="选择文件" />
                <Button Click="OnClipboardClicked" Content="粘贴" />
                <Button Click="OnScreenshotClicked" Content="截图" />
                <ProgressRing Width="20" Height="20" IsActive="{x:Bind ViewModel.IsBusy, Mode=OneWay}" />
                <Button Click="OnCancelClicked" Content="取消" />
            </StackPanel>
            <Border Grid.Row="1" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <WebView2 x:Name="PreviewWebView" />
            </Border>
        </Grid>

        <!-- 右栏：预处理/文本块占位 + 状态 + 结果 -->
        <Grid Grid.Column="1" MinWidth="300" RowSpacing="6">
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="*" />
            </Grid.RowDefinitions>
            <Expander IsExpanded="False" IsEnabled="False" Header="预处理选项">
                <TextBlock Opacity="0.6" Text="功能开发中" />
            </Expander>
            <Expander Grid.Row="1" IsExpanded="False" IsEnabled="False" Header="文本块处理">
                <TextBlock Opacity="0.6" Text="功能开发中" />
            </Expander>
            <Border Grid.Row="2" Padding="12" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel Orientation="Horizontal" Spacing="24">
                    <TextBlock Text="{x:Bind ViewModel.Status, Mode=OneWay}" />
                    <TextBlock x:Name="PreviewBridgeStatus" Text="Web 预览正在加载" />
                </StackPanel>
            </Border>
            <Border Grid.Row="3" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <Grid>
                    <StackPanel Padding="16" Spacing="8" Visibility="{x:Bind ViewModel.IsBusy, Mode=OneWay}">
                        <TextBlock FontWeight="SemiBold" Text="识别结果" />
                        <TextBlock Opacity="0.6" TextWrapping="Wrap" Text="识别完成后，结构化文本/表格/公式将在此处展示。" />
                    </StackPanel>
                    <Border x:Name="PreviewErrorPanel" Padding="16" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" Visibility="Collapsed">
                        <TextBlock x:Name="PreviewError" Text="预览资源未就绪" />
                    </Border>
                </Grid>
            </Border>
        </Grid>
    </Grid>
</Page>
```

注意：
- 现版的"复制富文本/Markdown/纯文本""导出 HTML/Markdown/文本"按钮从顶部工具栏移除——这些操作在 PySide6 是在结果区/右键菜单，本次按纯布局复刻放到结果占位区附近。但为避免丢失可工作功能，在结果区 Border 内追加一行操作按钮（保持 click handler 绑定）。

- [ ] **Step 2: 在右栏结果区补回复制/导出操作按钮**

把上面右栏 `Grid.Row="3"` 的 Border 内容改为：

```xml
            <Border Grid.Row="3" Padding="8" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <Grid RowSpacing="8">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto" />
                        <RowDefinition Height="Auto" />
                        <RowDefinition Height="*" />
                    </Grid.RowDefinitions>
                    <StackPanel Orientation="Horizontal" Spacing="4">
                        <Button Click="OnCopyRichClicked" Content="复制富文本" />
                        <Button Click="OnCopyMarkdownClicked" Content="复制 Markdown" />
                        <Button Click="OnCopyPlainClicked" Content="复制纯文本" />
                    </StackPanel>
                    <StackPanel Grid.Row="1" Orientation="Horizontal" Spacing="4">
                        <Button Click="OnExportHtmlClicked" Content="导出 HTML" />
                        <Button Click="OnExportMarkdownClicked" Content="导出 Markdown" />
                        <Button Click="OnExportTextClicked" Content="导出文本" />
                    </StackPanel>
                    <Border Grid.Row="2" x:Name="PreviewErrorPanel" Background="Transparent" Visibility="Collapsed">
                        <TextBlock x:Name="PreviewError" Text="预览资源未就绪" />
                    </Border>
                </Grid>
            </Border>
```

- [ ] **Step 3: 构建确认**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded。所有 `x:Name` 与 click handler 与 `.xaml.cs` 匹配（未改动 .cs）。

- [ ] **Step 4: 提交**

```bash
git add src/dotnet/VibeOCR.App/Views/RecognitionPage.xaml
git commit -m "refactor(winui): recognition page two-column layout (PySide6 parity)"
```

---

## Task 5: 批量识别页三栏重排（280\*/450\*/450\*）

**Files:**
- Modify: `src/dotnet/VibeOCR.App/Views/BatchPage.xaml`

**Interfaces:**
- 无新增。保留所有 `x:Name`（`BatchList`）与 click handler 名。

- [ ] **Step 1: 重写 BatchPage.xaml 为三栏**

整个 `<Page>` 内容替换为：

```xml
<Page x:Class="VibeOCR.App.Views.BatchPage" xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <Grid Padding="8" ColumnSpacing="8">
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="280*" MinWidth="200" />
            <ColumnDefinition Width="450*" />
            <ColumnDefinition Width="450*" />
        </Grid.ColumnDefinitions>

        <!-- 左栏：文件列表 + 预处理占位 + 操作/进度 + 导出占位 -->
        <Grid Grid.Column="0" RowSpacing="4">
            <Grid.RowDefinitions>
                <RowDefinition Height="*" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
            </Grid.RowDefinitions>
            <ListView x:Name="BatchList" ItemsSource="{x:Bind ViewModel.Items}">
                <ListView.ItemTemplate>
                    <DataTemplate>
                        <Grid Padding="4" ColumnSpacing="8">
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*" />
                                <ColumnDefinition Width="Auto" />
                            </Grid.ColumnDefinitions>
                            <StackPanel Spacing="2">
                                <TextBlock FontWeight="SemiBold" Text="{Binding Name}" TextTrimming="CharacterEllipsis" />
                                <TextBlock Opacity="0.65" FontSize="11" Text="{Binding Path}" TextTrimming="CharacterEllipsis" />
                                <TextBlock FontSize="11" Text="{Binding State}" />
                            </StackPanel>
                            <StackPanel Grid.Column="1" Orientation="Horizontal" Spacing="2">
                                <Button Click="OnMoveUpClicked" Content="↑" />
                                <Button Click="OnMoveDownClicked" Content="↓" />
                                <Button Click="OnCancelItemClicked" Content="取消" />
                                <Button Click="OnRemoveClicked" Content="移除" />
                            </StackPanel>
                        </Grid>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
            <Expander Grid.Row="1" IsExpanded="False" IsEnabled="False" Header="预处理选项">
                <TextBlock Opacity="0.6" Text="功能开发中" />
            </Expander>
            <StackPanel Grid.Row="2" Orientation="Horizontal" Spacing="4">
                <Button Click="OnStartClicked" Content="开始识别" />
                <Button Click="OnCancelAllClicked" Content="取消" />
                <TextBlock VerticalAlignment="Center" FontWeight="SemiBold" Text="{x:Bind ViewModel.Progress, Mode=OneWay}" />
            </StackPanel>
            <Expander Grid.Row="3" IsExpanded="False" IsEnabled="False" Header="导出设置">
                <StackPanel Spacing="4">
                    <Button Click="OnExportAllClicked" Content="导出全部 Markdown" />
                    <TextBlock Opacity="0.6" Text="功能开发中" />
                </StackPanel>
            </Expander>
        </Grid>

        <!-- 中栏：文件预览占位 -->
        <Border Grid.Column="1" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
            <StackPanel HorizontalAlignment="Center" VerticalAlignment="Center" Spacing="4">
                <TextBlock Opacity="0.6" Text="文件预览" />
                <TextBlock Opacity="0.4" Text="选择文件以预览" />
            </StackPanel>
        </Border>

        <!-- 右栏：识别结果占位 -->
        <Border Grid.Column="2" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
            <StackPanel HorizontalAlignment="Center" VerticalAlignment="Center" Spacing="4">
                <TextBlock Opacity="0.6" Text="识别结果" />
                <TextBlock Opacity="0.4" Text="开始识别后在此展示" />
            </StackPanel>
        </Border>
    </Grid>
</Page>
```

注意：标题"批量识别"和顶栏的"添加/清空/并发"按钮合并进左栏顶部。在左栏 ListView 之上补加一行操作栏。修正：把左栏 `<Grid.RowDefinitions>` 第一行之前加一个 Auto 行放操作栏。调整如下——左栏 Grid 改为：

- [ ] **Step 2: 左栏顶部补操作栏（添加/清空/并发）**

把 Task5 Step1 左栏 Grid 的 `RowDefinitions` 与首行改为（在 ListView 上方加 Auto 行）：

```xml
        <Grid Grid.Column="0" RowSpacing="4">
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" />
                <RowDefinition Height="*" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
            </Grid.RowDefinitions>
            <StackPanel Orientation="Horizontal" Spacing="4">
                <Button Click="OnAddClicked" Content="添加图片" />
                <Button Click="OnClearClicked" Content="清空" />
                <TextBlock VerticalAlignment="Center" Text="并发" />
                <NumberBox Width="60" Minimum="1" Maximum="4" Value="{x:Bind ViewModel.Concurrency, Mode=TwoWay}" />
            </StackPanel>
            <ListView Grid.Row="1" x:Name="BatchList" ItemsSource="{x:Bind ViewModel.Items}">
                <!-- ItemTemplate 同 Step 1 -->
            </ListView>
            <!-- 其余 Expander/操作行 Grid.Row 顺延 +1：预处理=Row2，开始/进度=Row3，导出=Row4 -->
            ...
        </Grid>
```

（实施时把 Step1 的 Expander/操作行 `Grid.Row` 数字统一 +1：预处理 `Grid.Row="2"`、开始/进度 `Grid.Row="3"`、导出 `Grid.Row="4"`，ItemTemplate 内容不变。）

- [ ] **Step 3: 构建确认**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded。

- [ ] **Step 4: 提交**

```bash
git add src/dotnet/VibeOCR.App/Views/BatchPage.xaml
git commit -m "refactor(winui): batch page three-column layout (PySide6 parity)"
```

---

## Task 6: 二维码页两栏 + 子 Pivot 重排（500\*/300\*）

**Files:**
- Modify: `src/dotnet/VibeOCR.App/Views/QrCodePage.xaml`
- Modify: `src/dotnet/VibeOCR.App/Views/QrCodePage.xaml.cs`（Pivot 切换时切换左栏操作栏可见性）

**Interfaces:**
- 无新增公开接口。Pivot 用 `x:Name` + `SelectionChanged` 控制 `_generateActions`/`_decodeActions` 两个 StackPanel 的 `Visibility`。

- [ ] **Step 1: 重写 QrCodePage.xaml 为两栏 + Pivot**

整个 `<Page>` 内容替换为：

```xml
<Page
    x:Class="VibeOCR.App.Views.QrCodePage"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <Grid Padding="8" ColumnSpacing="8">
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="500*" />
            <ColumnDefinition Width="300*" MinWidth="260" MaxWidth="360" />
        </Grid.ColumnDefinitions>

        <!-- 左栏：共享预览 + 操作栏（随 Pivot 切换） -->
        <Grid Grid.Column="0" RowSpacing="4">
            <Grid.RowDefinitions>
                <RowDefinition Height="*" />
                <RowDefinition Height="Auto" />
            </Grid.RowDefinitions>
            <Border Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel HorizontalAlignment="Center" VerticalAlignment="Center" Spacing="4">
                    <TextBlock Opacity="0.4" Text="预览区" />
                    <TextBlock Opacity="0.3" Text="Min 200×200" FontSize="11" />
                </StackPanel>
            </Border>
            <!-- 生成操作栏（默认可见） -->
            <StackPanel x:Name="_generateActions" Grid.Row="1" Orientation="Horizontal" Spacing="6">
                <Button Click="OnSaveClicked" Content="保存" />
                <Button Click="OnCopyImageClicked" Content="复制到剪贴板" />
            </StackPanel>
            <!-- 识别操作栏（默认折叠） -->
            <StackPanel x:Name="_decodeActions" Grid.Row="1" Orientation="Horizontal" Spacing="6" Visibility="Collapsed">
                <Button Click="OnPasteClicked" Content="粘贴图片" />
                <Button Click="OnPickFileClicked" Content="选择图片" />
                <Button Click="OnClearClicked" Content="清空" />
            </StackPanel>
        </Grid>

        <!-- 右栏：子 Pivot 生成|识别 -->
        <Pivot Grid.Column="1">
            <PivotItem Header="生成">
                <ScrollViewer Padding="0,8,0,0">
                    <StackPanel Spacing="8">
                        <StackPanel Spacing="4">
                            <TextBlock FontWeight="SemiBold" Text="输入内容" />
                            <TextBox PlaceholderText="输入要编码的内容" Text="{x:Bind ViewModel.GenerateText, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}" />
                            <StackPanel Orientation="Horizontal" Spacing="4">
                                <ComboBox Width="110" SelectedItem="{x:Bind ViewModel.GenerateFormat, Mode=TwoWay}">
                                    <x:String>qrcode</x:String>
                                    <x:String>barcode</x:String>
                                </ComboBox>
                                <Button Click="OnGenerateClicked" Content="生成" />
                            </StackPanel>
                        </StackPanel>
                        <StackPanel Spacing="4" IsEnabled="False">
                            <TextBlock FontWeight="SemiBold" Text="尺寸与纠错" />
                            <TextBlock Opacity="0.6" Text="功能开发中" />
                        </StackPanel>
                        <StackPanel Spacing="4" IsEnabled="False">
                            <TextBlock FontWeight="SemiBold" Text="颜色设置" />
                            <TextBlock Opacity="0.6" Text="功能开发中" />
                        </StackPanel>
                        <StackPanel Spacing="4" IsEnabled="False">
                            <TextBlock FontWeight="SemiBold" Text="Logo 嵌入" />
                            <TextBlock Opacity="0.6" Text="功能开发中" />
                        </StackPanel>
                        <StackPanel Spacing="4" IsEnabled="False">
                            <TextBlock FontWeight="SemiBold" Text="文字说明" />
                            <TextBlock Opacity="0.6" Text="功能开发中" />
                        </StackPanel>
                        <TextBlock Opacity="0.6" Text="{x:Bind ViewModel.GenerateStatus, Mode=OneWay}" />
                    </StackPanel>
                </ScrollViewer>
            </PivotItem>
            <PivotItem Header="识别">
                <Grid RowSpacing="6">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto" />
                        <RowDefinition Height="*" />
                    </Grid.RowDefinitions>
                    <TextBlock Opacity="0.6" Text="{x:Bind ViewModel.DecodeStatus, Mode=OneWay}" />
                    <ListView x:Name="CodeList" Grid.Row="1" ItemsSource="{x:Bind ViewModel.Codes}">
                        <ListView.ItemTemplate>
                            <DataTemplate>
                                <Grid Padding="4" ColumnSpacing="8">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="90" />
                                        <ColumnDefinition Width="*" />
                                        <ColumnDefinition Width="Auto" />
                                    </Grid.ColumnDefinitions>
                                    <TextBlock VerticalAlignment="Center" FontWeight="SemiBold" FontSize="11" Text="{Binding Format}" />
                                    <TextBlock Grid.Column="1" VerticalAlignment="Center" IsTextSelectionEnabled="True" Text="{Binding Data}" TextTrimming="CharacterEllipsis" TextWrapping="Wrap" />
                                    <Button Grid.Column="2" Click="OnOpenUrlClicked" Content="打开" Tag="{Binding IsUrl}" Visibility="{Binding IsUrl}" />
                                </Grid>
                            </DataTemplate>
                        </ListView.ItemTemplate>
                    </ListView>
                </Grid>
            </PivotItem>
        </Pivot>
    </Grid>
</Page>
```

- [ ] **Step 2: 加 Pivot SelectionChanged 切换左栏操作栏**

在 `QrCodePage.xaml` 的 `<Pivot Grid.Column="1">` 加属性：`SelectionChanged="OnPivotSelectionChanged"`。

- [ ] **Step 3: 在 QrCodePage.xaml.cs 加 handler**

读取 `src/dotnet/VibeOCR.App/Views/QrCodePage.xaml.cs` 确认现有 handler 命名（`OnPickFileClicked`/`OnPasteClicked`/`OnClearClicked`/`OnGenerateClicked`/`OnSaveClicked`/`OnOpenUrlClicked`），然后在类内加：

```csharp
    private void OnPivotSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_generateActions is null || _decodeActions is null) return;
        bool isDecode = e.AddedItems.Count > 0 && (e.AddedItems[0] as PivotItem)?.Header is string header && header is "识别";
        _generateActions.Visibility = isDecode ? Visibility.Collapsed : Visibility.Visible;
        _decodeActions.Visibility = isDecode ? Visibility.Visible : Visibility.Collapsed;
    }
```

并在文件顶部 using 区确保有 `using Microsoft.UI.Xaml;` 和 `using Microsoft.UI.Xaml.Controls;`。

**注意**：原 AboutPage/RecognitionPage 没有名为 `OnCopyImageClicked` 的 handler。二维码"复制到剪贴板"在现版无对应 handler。为避免编译失败，把生成操作栏的"复制到剪贴板"按钮改为禁用占位（纯布局范围内无后端）：

把 Step1 中：
```xml
<Button Click="OnCopyImageClicked" Content="复制到剪贴板" />
```
改为：
```xml
<Button Content="复制到剪贴板" IsEnabled="False" />
```

- [ ] **Step 4: 构建确认**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded。确认无未定义的 click handler。

- [ ] **Step 5: 提交**

```bash
git add src/dotnet/VibeOCR.App/Views/QrCodePage.xaml src/dotnet/VibeOCR.App/Views/QrCodePage.xaml.cs
git commit -m "refactor(winui): qr code page two-column + pivot layout (PySide6 parity)"
```

---

## Task 7: PDF 页两栏重排（200\*/600\*）

**Files:**
- Modify: `src/dotnet/VibeOCR.App/Views/PdfPage.xaml`

**Interfaces:**
- 无新增。保留 `x:Name`（`PageGrid`）与所有 click handler 名。

- [ ] **Step 1: 重写 PdfPage.xaml 为两栏**

整个 `<Page>` 内容替换为：

```xml
<Page
    x:Class="VibeOCR.App.Views.PdfPage"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <Grid Padding="8" ColumnSpacing="8">
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="200*" MinWidth="120" />
            <ColumnDefinition Width="600*" />
        </Grid.ColumnDefinitions>

        <!-- 左栏：文件下拉 + 缩略图列表 -->
        <Grid Grid.Column="0" RowSpacing="4">
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" />
                <RowDefinition Height="*" />
            </Grid.RowDefinitions>
            <ComboBox x:Name="_fileSelector" PlaceholderText="选择文件" IsEnabled="False" />
            <ListView x:Name="PageGrid" Grid.Row="1" ItemsSource="{x:Bind ViewModel.Pages}" SelectionMode="Extended" SelectionChanged="OnSelectionChanged">
                <ListView.ItemTemplate>
                    <DataTemplate>
                        <StackPanel Orientation="Horizontal" Spacing="8" Padding="4">
                            <TextBlock FontWeight="SemiBold" Text="{Binding Index}" />
                            <TextBlock VerticalAlignment="Center" FontSize="11" Text="{Binding State}" />
                        </StackPanel>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
        </Grid>

        <!-- 右栏：文件操作 + 页面操作 + 文字层 + 进度/状态 -->
        <Grid Grid.Column="1" RowSpacing="6">
            <Grid.RowDefinitions>
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="Auto" />
                <RowDefinition Height="*" />
                <RowDefinition Height="Auto" />
            </Grid.RowDefinitions>

            <!-- 文件操作 -->
            <StackPanel Orientation="Horizontal" Spacing="4">
                <Button Click="OnOpenClicked" Content="打开" />
                <Button Click="OnCloseClicked" Content="关闭" IsEnabled="{x:Bind ViewModel.HasSession, Mode=OneWay}" />
                <Button Content="添加文件" IsEnabled="False" />
                <Button Content="移除文件" IsEnabled="False" />
                <Button Click="OnSaveClicked" Content="保存" />
                <Button Content="另存为" IsEnabled="False" />
                <Button Content="批量导出" IsEnabled="False" />
            </StackPanel>

            <!-- 页面操作 -->
            <Border Grid.Row="1" Padding="8" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel Spacing="4">
                    <TextBlock FontSize="11" FontWeight="SemiBold" Text="页面操作" />
                    <WrapPanel Orientation="Horizontal">
                        <Button Click="OnRotateCwClicked" Content="顺时针90°" Margin="0,0,4,4" />
                        <Button Click="OnRotateCcwClicked" Content="逆时针90°" Margin="0,0,4,4" />
                        <Button Click="OnRotateAllClicked" Content="全部旋转" Margin="0,0,4,4" />
                        <Button Content="自动摆正" IsEnabled="False" Margin="0,0,4,4" />
                        <Button Content="横放摆正" IsEnabled="False" Margin="0,0,4,4" />
                        <Button Content="纵放摆正" IsEnabled="False" Margin="0,0,4,4" />
                        <Button Click="OnDeletePagesClicked" Content="删除选中页" Margin="0,0,4,4" />
                        <Button Content="插入页" IsEnabled="False" Margin="0,0,4,4" />
                    </WrapPanel>
                </StackPanel>
            </Border>

            <!-- 文字层操作 -->
            <Border Grid.Row="2" Padding="8" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel Spacing="4">
                    <TextBlock FontSize="11" FontWeight="SemiBold" Text="文字层操作" />
                    <StackPanel Orientation="Horizontal" Spacing="4">
                        <Button Click="OnAddTextLayerClicked" Content="添加文字层" />
                        <Button Click="OnDeleteTextLayersClicked" Content="删除文字层" />
                        <Button Content="预览文字层" IsEnabled="False" />
                    </StackPanel>
                    <TextBlock Opacity="0.6" FontSize="11" Text="文字层摘要：功能开发中" />
                </StackPanel>
            </Border>

            <!-- 方格状态网格占位 -->
            <Border Grid.Row="3" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <ScrollViewer HorizontalScrollMode="Auto" VerticalScrollMode="Auto">
                    <StackPanel HorizontalAlignment="Center" VerticalAlignment="Center">
                        <TextBlock Opacity="0.5" Text="方格状态网格" />
                        <TextBlock Opacity="0.4" FontSize="11" Text="功能开发中" />
                    </StackPanel>
                </ScrollViewer>
            </Border>

            <!-- 状态 -->
            <TextBlock Grid.Row="4" Opacity="0.6" Text="{x:Bind ViewModel.Status, Mode=OneWay}" />
        </Grid>
    </Grid>
</Page>
```

注意：`WrapPanel` 在 WinUI 3 不存在原生控件。改用 `StackPanel Orientation="Horizontal"`（单行）或 `ItemsWrapGrid`。为编译通过，把页面操作区的 `<WrapPanel>...</WrapPanel>` 替换为 `<StackPanel Orientation="Horizontal" Spacing="4">...</StackPanel>`（去掉各按钮的 Margin）。

- [ ] **Step 2: 修正 WrapPanel → StackPanel**

将 Step1 页面操作区改为：

```xml
                    <StackPanel Orientation="Horizontal" Spacing="4">
                        <Button Click="OnRotateCwClicked" Content="顺时针90°" />
                        <Button Click="OnRotateCcwClicked" Content="逆时针90°" />
                        <Button Click="OnRotateAllClicked" Content="全部旋转" />
                        <Button Content="自动摆正" IsEnabled="False" />
                        <Button Content="横放摆正" IsEnabled="False" />
                        <Button Content="纵放摆正" IsEnabled="False" />
                        <Button Click="OnDeletePagesClicked" Content="删除选中页" />
                        <Button Content="插入页" IsEnabled="False" />
                    </StackPanel>
```

- [ ] **Step 3: 构建确认**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded。

- [ ] **Step 4: 提交**

```bash
git add src/dotnet/VibeOCR.App/Views/PdfPage.xaml
git commit -m "refactor(winui): pdf page two-column layout (PySide6 parity)"
```

---

## Task 8: 设置页分组补全 + 注入 ShellViewModel（迁移选项）

**Files:**
- Modify: `src/dotnet/VibeOCR.App/Views/SettingsPage.xaml`
- Modify: `src/dotnet/VibeOCR.App/Views/SettingsPage.xaml.cs`（补迁移来的 click handler）
- 注：`SettingsPage` 构造函数签名已**在 Task 3 Step 3** 改为 `(SettingsViewModel, ShellViewModel)` 并加 `Shell` 属性。`App.xaml.cs` 的设置页工厂也在 Task 3 改为传 `_shellViewModel!`。本 Task 仅补迁移 handler + 重写 XAML。

**Interfaces:**
- Consumes: `ShellViewModel`（来自 `VibeOCR.App.Features.Shell`，已在 AboutPage 使用）。其成员：`PendingHotkey`(TwoWay string)、`ApplyHotkey()`、`HotkeyStatus`、`StartWithSystem`(TwoWay bool)、`SetStartWithSystem(bool)`、`HideToTray()`、`Quit()`。
- 设置页新构造：`SettingsPage(SettingsViewModel viewModel, ShellViewModel shell)`。

- [ ] **Step 1: 在 SettingsPage.xaml.cs 补迁移来的 click handler**

Task 3 Step 3 已把 `SettingsPage.xaml.cs` 改为双参构造 + `Shell` 属性（保留 `LoadSnapshotAsync` 与 `OnRefreshClicked`/`OnSwitchBackendClicked`）。本步在 `OnSwitchBackendClicked` 之后追加从 AboutPage 迁移来的 handler。在类内末尾 `}` 前加：

```csharp
    // 以下从 AboutPage 迁移而来（可工作的 handler，保留绑定）：
    private void OnApplyHotkeyClicked(object sender, RoutedEventArgs e) => Shell.ApplyHotkey();
    private void OnStartupClicked(object sender, RoutedEventArgs e)
        => Shell.SetStartWithSystem(Shell.StartWithSystem);
    private void OnHideToTrayClicked(object sender, RoutedEventArgs e) => Shell.HideToTray();
```

`ShellViewModel` 公开成员已核实（`src/dotnet/VibeOCR.App/Features/Shell/ShellViewModel.cs`）：`PendingHotkey`(get/set string)、`HotkeyStatus`(get string)、`StartWithSystem`(get/set bool)、`ApplyHotkey()`、`SetStartWithSystem(bool)`、`HideToTray()`、`AppVersion`、`License`、`ProjectUri`。

- [ ] **Step 2: 重写 SettingsPage.xaml 为分组卡片**

整个 `<Page>` 内容替换为（外层 ScrollViewer + 纵向 StackPanel 分组）：

```xml
<Page
    x:Class="VibeOCR.App.Views.SettingsPage"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <ScrollViewer Padding="8">
        <StackPanel Spacing="12" MaxWidth="900" HorizontalAlignment="Stretch">

            <!-- 模型预加载 -->
            <Border Padding="16" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel Spacing="8">
                    <TextBlock FontWeight="SemiBold" Text="模型预加载" />
                    <CheckBox Content="启动时自动预加载模型" IsEnabled="False" />
                    <TextBlock Opacity="0.6" FontSize="12" Text="预加载管道：功能开发中" />
                    <StackPanel Orientation="Horizontal" Spacing="8">
                        <Button Content="立即预加载" IsEnabled="False" />
                        <TextBlock VerticalAlignment="Center" Opacity="0.6" Text="预热管线：" />
                    </StackPanel>
                    <ListView ItemsSource="{x:Bind ViewModel.PreloadPipelines}" MaxHeight="160" />
                </StackPanel>
            </Border>

            <!-- 缓存管理 -->
            <Border Padding="16" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel Spacing="8">
                    <TextBlock FontWeight="SemiBold" Text="缓存管理" />
                    <StackPanel Orientation="Horizontal" Spacing="8">
                        <Button Content="刷新缓存状态" IsEnabled="False" />
                        <Button Content="清除缓存" IsEnabled="False" />
                    </StackPanel>
                    <TextBlock Opacity="0.6" FontSize="12" Text="缓存状态：功能开发中" />
                </StackPanel>
            </Border>

            <!-- 应用设置（含迁移来的热键/启动/托盘） -->
            <Border Padding="16" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel Spacing="8">
                    <TextBlock FontWeight="SemiBold" Text="应用设置" />
                    <CheckBox Content="显示边缘工具栏" IsEnabled="False" />
                    <CheckBox Content="自动隐藏" IsEnabled="False" Margin="20,0,0,0" />
                    <StackPanel Orientation="Horizontal" Spacing="8" Margin="20,0,0,0">
                        <TextBlock VerticalAlignment="Center" Text="隐藏延迟：" />
                        <NumberBox Width="90" Minimum="100" Maximum="5000" Value="500" IsEnabled="False" />
                        <TextBlock VerticalAlignment="Center" Text="毫秒" />
                    </StackPanel>
                    <CheckBox Content="最小化到系统托盘" IsEnabled="False" />
                    <CheckBox Content="开机自启动" IsChecked="{x:Bind Shell.StartWithSystem, Mode=TwoWay}" Click="OnStartupClicked" />
                    <!-- 热键编辑（从关于页迁入） -->
                    <StackPanel Orientation="Horizontal" Spacing="8" Margin="0,4,0,0">
                        <TextBlock VerticalAlignment="Center" Text="快捷键：" />
                        <TextBox Width="140" Text="{x:Bind Shell.PendingHotkey, Mode=TwoWay}" />
                        <Button Click="OnApplyHotkeyClicked" Content="应用" />
                        <TextBlock VerticalAlignment="Center" Text="{x:Bind Shell.HotkeyStatus, Mode=OneWay}" />
                    </StackPanel>
                </StackPanel>
            </Border>

            <!-- 推理后端与依赖 -->
            <Border Padding="16" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                <StackPanel Spacing="8">
                    <TextBlock FontWeight="SemiBold" Text="推理后端与依赖" />
                    <StackPanel Orientation="Horizontal" Spacing="8">
                        <Button Click="OnRefreshClicked" Content="刷新" />
                        <TextBlock VerticalAlignment="Center" Text="{x:Bind ViewModel.Status, Mode=OneWay}" />
                    </StackPanel>
                    <StackPanel Orientation="Horizontal" Spacing="8">
                        <TextBlock VerticalAlignment="Center" Text="后端：" />
                        <ComboBox Width="100" SelectedItem="{x:Bind ViewModel.PendingBackend, Mode=TwoWay}">
                            <x:String>cpu</x:String>
                            <x:String>gpu</x:String>
                        </ComboBox>
                        <Button Click="OnSwitchBackendClicked" Content="切换后端" IsEnabled="{x:Bind ViewModel.CanSwitchBackend, Mode=OneWay}" />
                        <CheckBox Content="需重启" IsChecked="{x:Bind ViewModel.RestartRequired, Mode=OneWay}" IsEnabled="False" />
                    </StackPanel>
                    <TextBlock Opacity="0.6" FontSize="12" Text="依赖状态树：功能开发中" />
                    <StackPanel Orientation="Horizontal" Spacing="4">
                        <Button Content="重装选中项" IsEnabled="False" />
                        <Button Content="重装运行时" IsEnabled="False" />
                        <Button Content="重装依赖" IsEnabled="False" />
                        <Button Content="补充安装" IsEnabled="False" />
                        <Button Content="更新依赖" IsEnabled="False" />
                    </StackPanel>
                </StackPanel>
            </Border>
        </StackPanel>
    </ScrollViewer>
</Page>
```

- [ ] **Step 3: 构建确认**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded。确认 `Shell.StartWithSystem`/`PendingHotkey`/`HotkeyStatus` 绑定与 `ShellViewModel` 公开成员一致（如名称不符，以 `ShellViewModel` 实际定义为准，调整 XAML 绑定路径）。

- [ ] **Step 4: 提交**

```bash
git add src/dotnet/VibeOCR.App/Views/SettingsPage.xaml src/dotnet/VibeOCR.App/Views/SettingsPage.xaml.cs
git commit -m "refactor(winui): settings page grouped cards + migrate shell options"
```

---

## Task 9: 关于页双栏卡片重排 + 移除迁移走的选项

**Files:**
- Modify: `src/dotnet/VibeOCR.App/Views/AboutPage.xaml`
- Modify: `src/dotnet/VibeOCR.App/Views/AboutPage.xaml.cs`（移除热键/启动/托盘/退出 handler；保留更新检查 handler）

**Interfaces:**
- 无新增。保留 `Shell`/`Update` 属性（`Shell` 仍用于读 AppVersion/License/ProjectUri；Update 用于检查更新）。

- [ ] **Step 1: 重写 AboutPage.xaml 为双栏卡片**

整个 `<Page>` 内容替换为：

```xml
<Page
    x:Class="VibeOCR.App.Views.AboutPage"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <ScrollViewer Padding="8">
        <Grid MaxWidth="980" HorizontalAlignment="Center" ColumnSpacing="16">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*" />
                <ColumnDefinition Width="*" />
            </Grid.ColumnDefinitions>

            <!-- 左栏：品牌 + 详细信息 -->
            <StackPanel Grid.Column="0" Spacing="12">
                <Border Padding="20" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                    <StackPanel Spacing="6">
                        <TextBlock FontSize="24" FontWeight="Bold" Text="VibeOCR" />
                        <Border HorizontalAlignment="Left" Padding="8,2" Background="{ThemeResource AccentFillColorDefaultBrush}" CornerRadius="10">
                            <TextBlock FontSize="11" Text="{x:Bind Shell.AppVersion, Mode=OneWay}" />
                        </Border>
                        <TextBlock Opacity="0.7" TextWrapping="Wrap" Text="基于 PaddleOCR 的多场景 OCR / PDF 处理 / 二维码工具。" />
                    </StackPanel>
                </Border>
                <Border Padding="20" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8">
                    <StackPanel Spacing="4">
                        <TextBlock FontWeight="SemiBold" Text="详细信息" />
                        <TextBlock><Run Text="版本：" /><Run Text="{x:Bind Shell.AppVersion, Mode=OneWay}" /></TextBlock>
                        <TextBlock><Run Text="许可：" /><Run Text="{x:Bind Shell.License, Mode=OneWay}" /></TextBlock>
                        <TextBlock Text="技术栈：.NET 10 · WinUI 3 · WebView2 · PaddleOCR" TextWrapping="Wrap" />
                        <HyperlinkButton Content="项目主页" NavigateUri="{x:Bind Shell.ProjectUri, Mode=OneWay}" />
                        <HyperlinkButton Content="GitHub 源码" NavigateUri="{x:Bind Shell.ProjectUri, Mode=OneWay}" />
                    </StackPanel>
                </Border>
            </StackPanel>

            <!-- 右栏：更新日志 + 检查更新 -->
            <Grid Grid.Column="1" RowSpacing="12">
                <Grid.RowDefinitions>
                    <RowDefinition Height="*" />
                    <RowDefinition Height="Auto" />
                </Grid.RowDefinitions>
                <Border Padding="20" Background="{ThemeResource CardBackgroundFillColorDefaultBrush}" CornerRadius="8" MinHeight="280">
                    <StackPanel Spacing="6">
                        <TextBlock FontWeight="SemiBold" Text="更新日志" />
                        <TextBlock Opacity="0.6" Text="功能开发中（CHANGELOG 渲染待接入）" TextWrapping="Wrap" />
                    </StackPanel>
                </Border>
                <StackPanel Grid.Row="1" Orientation="Horizontal" Spacing="8" HorizontalAlignment="Right">
                    <Button Click="OnCheckUpdateClicked" Content="检查更新" />
                    <Button Click="OnDownloadUpdateClicked" Content="下载并安装" />
                    <Button Click="OnCancelUpdateClicked" Content="取消" />
                    <TextBlock VerticalAlignment="Center" Text="{x:Bind Update.Status, Mode=OneWay}" />
                </StackPanel>
            </Grid>
        </Grid>
    </ScrollViewer>
</Page>
```

- [ ] **Step 2: 移除 AboutPage.xaml.cs 中迁移走的 handler**

修改 `src/dotnet/VibeOCR.App/Views/AboutPage.xaml.cs`，删除以下方法（已迁移到 SettingsPage）：
- `OnApplyHotkeyClicked`
- `OnStartupClicked`
- `OnHideToTrayClicked`
- `OnQuitClicked`

保留：`OnCheckUpdateClicked`、`OnDownloadUpdateClicked`、`OnCancelUpdateClicked`。

保留构造函数与 `Shell`/`Update` 属性不变。

- [ ] **Step 3: 构建确认**

Run: `dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
Expected: Build succeeded。

- [ ] **Step 4: 提交**

```bash
git add src/dotnet/VibeOCR.App/Views/AboutPage.xaml src/dotnet/VibeOCR.App/Views/AboutPage.xaml.cs
git commit -m "refactor(winui): about page two-column cards, move shell options out"
```

---

## Task 10: 全量构建 + 测试 + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`（如存在）

**Interfaces:** 无。

- [ ] **Step 1: 全量构建**

Run: `dotnet build src/dotnet/VibeOCR.sln` （或 `dotnet build` 在仓库根，若有 .sln）
Expected: 整个解决方案 Build succeeded，无警告。

- [ ] **Step 2: 全量测试**

Run: `dotnet test`
Expected: 全部通过（含 WindowLayoutStoreTests、ShellTests、各 ViewModel 测试）。

- [ ] **Step 3: 运行时视觉验证（手动，spec 7.2/7.3/7.4）**

在 125%/150%/200% 系统缩放下分别启动应用（如可），确认：
- 文字/图标清晰（DPI 修复生效）。
- 默认窗口 900×600，拖到小于 720×480 被卡住。
- 关闭重开，窗口几何/最大化状态恢复。
- 逐页切换：单图两栏、批量三栏、二维码两栏+Pivot、PDF 两栏、设置分组、关于双栏——与 PySide6 截图对照。

（此步为手动，无法在 CI 自动化。记录到提交说明或 PR 描述。）

- [ ] **Step 4: CHANGELOG（若项目维护）**

Run: `test -f CHANGELOG.md && echo exists || echo none`
若存在，在顶部加一条（参照现有格式）：
```
- 修复 WinUI 界面在高 DPI 显示器上发虚模糊（声明 PerMonitorV2 DPI 感知）
- 主窗口默认 900×600 + 最小尺寸 + 记忆窗口几何
- 各功能页布局参照 PySide6 重新对齐（单图/批量/二维码/PDF/设置/关于）
```

- [ ] **Step 5: 最终提交**

```bash
git add CHANGELOG.md 2>/dev/null; git commit -m "chore(winui): layout parity + DPI fix changelog" --allow-empty
```

---

## 实施备注

- **可单测的逻辑**只有 Task 1（`WindowLayoutStore`）。DPI/窗口尺寸/XAML 布局无法在 CI 单测，靠 `dotnet build` + 手动视觉验证（spec 第 7 节）。
- **TreatWarningsAsErrors**：任何未使用 using、nullable 警告都会失败构建。XAML 重写后若 .xaml.cs 有未使用 handler，构建会报 CS warning——Task 9 已处理。若其他页 .xaml.cs 出现孤立 handler，删除之。
- **回退方案**：若 app.manifest 在未打包 host 下不生效（spec 风险表），在 `App.xaml.cs` 构造函数最早期（`InitializeComponent()` 之前）加 P/Invoke：
  ```csharp
  [DllImport("user32.dll")]
  private static extern bool SetProcessDpiAwarenessContext(int value);
  ```
  并在 `OnLaunched` 首行调用 `SetProcessDpiAwarenessContext(-4)`（PER_MONITOR_AWARE_V2 = -4）。仅在 manifest 验证失败后启用。
- **ShellViewModel 注入时序**：`_shellViewModel` 在 `InitializeDesktopShell`（启动后）填充；设置页工厂惰性调用，故点击设置时非 null。若启动首帧即点设置（极端竞态），`_shellViewModel!` 会抛 NRE——可接受（启动 100ms 内点击概率极低，且 spec 不改默认选中页）。
