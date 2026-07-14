# WinUI 布局对齐 PySide6 + DPI 修复设计

日期：2026-07-14
状态：设计已获用户逐节确认，等待书面审阅
关联文档：`docs/superpowers/specs/2026-07-11-winui3-worker-migration-design.md`（迁移总设计）

## 1. 目标

修复 WinUI 迁移后出现的两个界面问题：

1. **界面发虚模糊（"分辨率降低"）**：文字、图标在高 DPI（125%/150%/200% 缩放）显示器上发虚，根因是未打包 WinUI 应用（`WindowsPackageType=None`）缺少 DPI 感知声明，Windows 回退到 System DPI 虚拟化（位图拉伸）。
2. **各页布局大幅偏离 PySide6**：迁移时页面内部布局被简化，丢失了 PySide6 里的 splitter 分栏、选项面板、参数区等结构。

本设计把各功能页布局**参照 PySide6 重新对齐**（顶部标签已在迁移时改为左侧 NavigationView，保持不变），并修复 DPI 模糊。

**本次为纯布局复刻**：只调整页面骨架、分栏比例、控件位置和密度，**不新增后端逻辑、不扩展 Python worker 契约、不实现新控件的实际功能**。

## 2. 非目标

- 不写任何新后端逻辑，不扩展 Python↔.NET worker 契约。
- 不实现占位控件（预处理参数、二维码 Logo/颜色、依赖重装、缓存清除等）的实际功能。
- 不改主题系统（仍用默认 `XamlControlsResources`，跟随系统深浅色）。
- 不改 NavigationView 导航项、不改动侧边栏结构、不改默认选中页（仍停在主页）。
- 不改标题字号/padding 密度（全局外观仅确认用默认 NavigationView）。
- 不引入 GridSplitter / 可拖动分隔条。

## 3. 已确认决策

| 主题 | 决策 |
|---|---|
| 分辨率问题根因 | DPI 感知缺失 → 位图拉伸模糊 |
| DPI 修复方式 | 新建 `app.manifest` 声明 `PerMonitorV2`（路径 1） |
| 窗口尺寸 | 默认 900×600（对齐 PySide6）+ 设最小尺寸 + 记忆窗口几何 |
| 窗口几何持久化 | 独立 `winui-layout.json`（`%LOCALAPPDATA%\VibeOCR\`） |
| 分栏技术 | Grid + 星号比例列（方案 A），不引入 GridSplitter |
| 分栏组件约束 | 部分组件加 `MinWidth`/`MaxWidth` |
| 单图识别页 | 完全复刻 PySide6（两栏 400\*/500\*） |
| 批量识别页 | 完全复刻 PySide6（三栏 280\*/450\*/450\*） |
| 二维码页 | 完全复刻 PySide6（两栏 500\*/300\* + 子 Pivot） |
| PDF 页 | 完全复刻 PySide6（两栏 200\*/600\*） |
| 设置页 | 单页分组补全（按 PySide6 分组顺序） |
| 关于页 | 完全复刻 PySide6（双栏卡片，max 980） |
| 主页 | 暂留空白（不改） |
| 诊断页 | 保留在侧边栏末尾（不改） |
| 关于页选项迁移 | 热键/启动/托盘/退出选项移到设置页"应用设置"分组 |
| 占位控件表现 | 灰色禁用态 + "功能开发中"提示 |
| 复刻范围 | 纯布局复刻（不接后端） |

## 4. 全局修复

### 4.1 DPI 感知 manifest

新建 `src/dotnet/VibeOCR.App/app.manifest`：

- 声明 `<dpiAwareness>PerMonitorV2,PerMonitor</dpiAwareness>`
- 声明 `<dpiAware>true/pm</dpiAware>`（老系统/兼容回退）
- 声明 `<supportedOS>` Windows 10/11
- 声明 Windows 通用控件隔离（`active`）、UTF-8 代码页等标准项

在 `src/dotnet/VibeOCR.App/VibeOCR.App.csproj` 增加：

```xml
<ApplicationManifest>app.manifest</ApplicationManifest>
```

**预期效果**：Windows 不再对窗口做位图虚拟化，文字/图标在 125%/150%/200% 缩放下清晰。

**回退方案**：若 manifest 在未打包应用 host 进程下不生效，在 `App.xaml.cs` 极早阶段（窗口创建前）P/Invoke `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` 作为补充/替代。

### 4.2 窗口默认/最小尺寸

在 `MainWindow.xaml.cs` 构造完成、窗口 handle 就绪后：

- `AppWindow.Resize(new SizeInt32(900, 600))` 设默认大小（对齐 PySide6 的 `self.resize(900, 600)`）
- 通过 `AppWindow.SetPresenter` 或 Win32 `SetWindowPos`/`WM_GETMINMAXINFO` 处理最小尺寸（建议 `MinWidth=720, MinHeight=480`），避免用户拖到过小导致布局崩溃

注意 WinUI 3 `AppWindow.Resize` 受 DPI 缩放影响，需用 `DisplayArea`/`Win32` 换算到逻辑像素，或使用 `OverlappedPresenter`。

### 4.3 窗口几何记忆

新建轻量 `WindowLayoutStore`：

- 存储位置：`%LOCALAPPDATA%\VibeOCR\winui-layout.json`
- 字段：`x, y, width, height, isMaximized`（可选 `navIndex` 记忆侧边栏选中项）
- 启动时恢复（若有效），关闭时保存
- 对齐 PySide6 `layout.json` 语义，但 **splitter 比例不记忆**（方案 A 下比例固定）

### 4.4 分栏统一约定

所有页面用 `Grid` + `ColumnDefinitions`/`RowDefinitions` 星号比例列：

- 单图识别：`400*,500*`
- 批量识别：`280*,450*,450*`
- 二维码：`500*,300*`
- PDF：`200*,600*`

各分栏组件按需加 `MinWidth`/`MaxWidth`（具体值见各页小节）。

## 5. 各页布局

> 约定：`★` = 已有控件（保留/移位），`○` = 本次新建的占位控件，统一为**灰色禁用态 + "功能开发中"提示**，不接后端。

### 5.1 单图识别页（两栏 `400*,500*`）

```
┌──────────────────────────┬──────────────────────────┐
│ [截图][选文件][粘贴]      │ ○ 预处理选项 (Expander)   │  Row 0 (Auto)
│ [复制图]      [开始识别]★ │ ○ 文本块处理 (Expander)   │
├──────────────────────────┼──────────────────────────┤
│                          │                          │
│    图片预览 (WebView2)★   │   识别结果 (WebView2)★    │  Row 1 (*)
│                          │                          │
└──────────────────────────┴──────────────────────────┘
列: 400* | 500*        右栏 MinWidth=300
```

变更点：

- 操作栏从"整页顶部横条"移到**左栏顶部**（PySide6 的 action_bar 在左侧）。
- 补 `截图`、`选择文件`、`粘贴`、`复制图片` 按钮位（现版是"打开图片/从剪贴板/截取桌面/取消"，按 PySide6 语义对齐命名/位置）。可交互的用 ★（接现有流程），暂无后端的用 ○。
- 右栏顶部加两个 `Expander`（预处理选项 / 文本块处理）占位，下方放现有结果 WebView2。
- 右栏 `MinWidth=300`。

### 5.2 批量识别页（三栏 `280*,450*,450*`）

```
┌────────┬────────────────┬────────────────┐
│文件列表 │   文件预览 ○    │   识别结果 ○    │
│ ListView│                │                │
│        │                │                │
│预处理○  │                │                │
│[开始]   │                │                │
│[取消]0/0│                │                │
│导出设置○ │                │                │
└────────┴────────────────┴────────────────┘
列: 280* | 450* | 450*   左栏 MinWidth=200
```

变更点：

- 从单列改为**三栏**（Grid 三列）。
- 左栏：现有 ListView（移入）+ 预处理 Expander 占位 + 操作/进度行 + 导出设置占位。
- 中栏：文件预览占位（空状态文字"选择文件以预览"）。
- 右栏：识别结果占位。
- 左栏 `MinWidth=200`。

### 5.3 二维码页（两栏 `500*,300*` + 子 Pivot）

```
┌──────────────────────┬─────────────────┐
│                      │ [生成] [识别]    │ ← Pivot 子标签
│   预览区 (共享) ○      │┌生成参数(Scroll)─┐│
│  Min 200×200         ││○格式/内容       ││
│                      ││○尺寸与纠错      ││
│ [保存][复制] /        ││○颜色设置        ││
│ [粘贴][选][识别][清空] ││○Logo嵌入        ││
│ (操作栏随子标签切换)   ││○文字说明        ││
│                      │└────────────────┘│
└──────────────────────┴─────────────────┘
列: 500* | 300*   右栏 MinWidth=260, MaxWidth=360
```

变更点：

- 左栏：大预览区（`MinWidth=200, MinHeight=200`）+ 操作栏。操作栏两套（生成/识别），随右侧子标签切换可见性。
- 右栏：`Pivot` 两个 tab。
  - 「生成」= `ScrollViewer` 包纵向参数面板，5 个参数段占位（输入内容/格式、尺寸与纠错、颜色设置、Logo 嵌入、文字说明）。
  - 「识别」= 现有结果 ListView。
- 右栏 `MinWidth=260, MaxWidth=360`（对齐 PySide6 生成面板约束）。
- 现版的上下两条工具栏（解码/生成）解散，重新组合到上述结构。

### 5.4 PDF 页（两栏 `200*,600*`）

```
┌─────────┬───────────────────────────────────┐
│文件下拉○│[打开][添加][移除][保存][另存][批量导出]│
│         │┌页面操作──────────────────────┐  │
│ 缩略图   ││[顺90][逆90][全顺][全逆][摆正]... │  │
│ ListView │└──────────────────────────────┘  │
│(Min 120)│┌文字层操作────────────────────┐  │
│         ││[添加][补加][删除][预览] 摘要     │  │
│         ││ ○方格状态网格 (ScrollViewer)    │  │
│         │└──────────────────────────────┘  │
│         │ 进度条+取消 / 状态                 │
└─────────┴───────────────────────────────────┘
列: 200* | 600*   左栏 MinWidth=120
```

变更点：

- 从单列改为**两栏**。左栏：文件下拉 + 缩略图 ListView（现有缩略图移入）。
- 右栏：文件操作栏 + 页面操作分组 + 文字层分组（含方格状态网格占位）+ 进度/状态。
- 左栏 `MinWidth=120`。

### 5.5 设置页（单页分组补全）

保留单页，外层 `ScrollViewer`，内层 `StackPanel` 纵向排列分组卡片（间距 12）。每个分组用带标题的卡片。

```
设置（ScrollViewer 内纵向堆叠）
┌─ 模型预加载 ──────────────────────────┐
│ ☑ 启动时自动预加载模型                  │
│   预加载管道: ○通用OCR ○表 ○文档P ○公式  │
│   [立即预加载]  ○状态标签  ○进度条        │
├─ 缓存管理 ────────────────────────────┤
│ [刷新缓存状态] [清除缓存]  ○缓存状态标签  │
├─ 应用设置 ────────────────────────────┤
│ ☑ 显示边缘工具栏                       │
│   ☑ 自动隐藏   隐藏延迟: [SpinBox] 毫秒  │
│ ☑ 最小化到系统托盘                      │
│ ☑ 开机自启动                          │
│ ○ 热键编辑（从关于页移入）              │
├─ 推理后端与依赖 ───────────────────────┤
│ 后端 [cpu/gpu] [切换] ☑需重启   ← 现有   │
│ ○ 依赖状态树 (Tree/List)               │
│ [重装选中][重装运行时][重装依赖][补充][更新] │
└──────────────────────────────────────┘
```

变更点：

- `★现有`：后端切换、预热管线（并入"模型预加载"分组）、需重启复选框。
- `○新建占位`：预加载管道勾选项、立即预加载/进度、缓存管理、边缘工具栏/自动隐藏/延迟、托盘/自启动、热键编辑、依赖树、重装按钮组。
- 把现版关于页里的**热键编辑、开机启动、隐藏到托盘、退出**选项移入"应用设置"分组。

### 5.6 关于页（双栏卡片）

居中、`MaxWidth=980`，两栏 Grid（`*,*`），外层 ScrollViewer。

```
              ┌──────────────┬──────────────┐
              │ 品牌卡片      │ 更新日志卡片   │
              │ Logo(96)     │ (CHANGELOG   │
              │ VibeOCR 24pt │  渲染)       │
              │ 版本药丸      │              │
              │ 简介          │ Min 280     │
              │──────────────│              │
              │ 详细信息卡片   │              │
              │ 作者/版权     │──────────────│
              │ 技术栈        │              │
              │ GitHub/Gitee │ [检查更新]    │
              │ 代码镜像      │ (右下)       │
              └──────────────┴──────────────┘
列: * | *     MaxWidth=980, 居中
```

变更点：

- 左栏：品牌卡片（Logo+名称+版本药丸+简介）+ 详细信息卡片（作者/版权/技术栈/GitHub/Gitee/镜像链接）。
- 右栏：更新日志卡片（渲染 CHANGELOG.md，`MinHeight=280`）+ 右下「检查更新」按钮。
- **移除**现版关于页里的：热键编辑、开机启动、隐藏到托盘、退出选项（移到设置页）。
- 现版的"版本/许可/项目主页链接"并入"详细信息卡片"。

### 5.7 主页（不改）

- 保持现状：居中 "VibeOCR" 文字。
- 作为默认选中项（NavigationView index 0）保留。
- 本次不涉及。

### 5.8 诊断与修复页（不改）

- 位置：NavigationView 最后一项（顺序不变）。
- 布局不改（WinUI 独有排障工具，无 PySide6 对照）。
- 本次不动。

## 6. 占位控件统一表现

所有 `○` 占位控件：

- **灰色禁用态**：`IsEnabled="False"` + 禁用样式。
- **提示文字**：在分组/控件附近加 `TextBlock` 提示"功能开发中"（或类似文案），避免用户误以为可交互。
- **不接任何后端逻辑**：本次不写 click handler / 数据绑定。

## 7. 验证策略

本次是纯 UI 布局变更，没有可写的功能单测。验证靠：

1. **编译通过**：`dotnet build`（VibeOCR.App + 测试项目）无错误。
2. **DPI 验证（关键）**：在 125%/150%/200% 缩放下启动应用，目视确认文字/图标清晰、不再位图拉伸发虚。
3. **窗口尺寸验证**：默认开 900×600；拖到更小有最小尺寸卡点；关闭重开记忆上次窗口几何。
4. **逐页布局对照**：启动后逐页切到单图/批量/二维码/PDF/设置/关于，与 PySide6 截图对照分栏数量、比例、控件位置。
5. **回归**：现有 WinUI 功能（导航切换、单图识别已有流程、批量列表、二维码识别、PDF 操作、设置后端切换）不因布局重排而坏。

验证时 PySide6 旧界面可作为视觉对照基准（截图并排比对），但不需要同时跑两套。

## 8. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| manifest 不生效 | 未打包 WinUI 应用 host 进程加载 manifest 的时机/路径偶有异常 | 回退到运行时 `SetProcessDpiAwarenessContext`（4.1 节回退方案） |
| 半成品控件观感 | `○` 占位控件搭壳后界面出现"看起来有功能但禁用"的项 | 统一灰色禁用 + "功能开发中"提示（第 6 节） |
| `AppWindow.Resize` DPI 换算 | WinUI 3 Resize 受缩放影响，可能不是逻辑 900×600 | 用 `OverlappedPresenter` 或 Win32 换算逻辑像素 |
| 最小尺寸实现 | WinUI 3 无原生 `MinWidth/MinHeight` 窗口属性 | 需 Win32 `WM_GETMINMAXINFO` 拦截 |
| 旧版 splitter 比例记忆缺失 | 方案 A 下 splitter 比例固定，不记忆 | 用户已确认接受；窗口几何仍记忆 |

## 9. 涉及文件

新增：

- `src/dotnet/VibeOCR.App/app.manifest`（DPI 声明）
- `src/dotnet/VibeOCR.App/Services/WindowLayoutStore.cs`（窗口几何持久化，存到 `%LOCALAPPDATA%\VibeOCR\winui-layout.json`）

修改：

- `src/dotnet/VibeOCR.App/VibeOCR.App.csproj`（引用 manifest）
- `src/dotnet/VibeOCR.App/MainWindow.xaml` / `MainWindow.xaml.cs`（窗口尺寸/几何/最小尺寸）
- `src/dotnet/VibeOCR.App/Views/RecognitionPage.xaml(.cs)`（两栏重排）
- `src/dotnet/VibeOCR.App/Views/BatchPage.xaml(.cs)`（三栏重排）
- `src/dotnet/VibeOCR.App/Views/QrCodePage.xaml(.cs)`（两栏 + Pivot 重排）
- `src/dotnet/VibeOCR.App/Views/PdfPage.xaml(.cs)`（两栏重排）
- `src/dotnet/VibeOCR.App/Views/SettingsPage.xaml(.cs)`（分组补全）
- `src/dotnet/VibeOCR.App/Views/AboutPage.xaml(.cs)`（双栏卡片重排）
