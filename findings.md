# 调研记录：NuGet 锁文件管理

## 需求

- 保留 `packages.lock.json` 对传递依赖版本的锁定作用。
- 避免 Visual Studio、不同 RID 或不同恢复入口在普通开发中改写锁文件。
- 提供明确、可重复的依赖锁更新入口。

## 已确认

- 当前两份未提交锁文件仅新增 `win-arm64`、`win-x86` 等 RID 图，没有包版本变化。
- `global.json` 已固定 SDK 10.0.302。
- `Directory.Packages.props` 已使用中央包版本管理。
- CI 和 Release 已使用 `dotnet restore ... --locked-mode`。
- `VibeOCR.App` 只声明 `win-x64`，但 `VibeOCR.App.Tests` 没有显式 RuntimeIdentifier。
- 本机命令行没有可用的 .NET SDK，无法执行真实 restore。
- App 测试项目固定 RID 后，其锁文件必须保留对应的 `win-x64` 图；只清理未支持的 `win-arm64/win-x86` 图。

## 技术方案

| 方案 | 原因 |
|---|---|
| 默认 locked mode | 让普通 restore 失败而非改写锁文件 |
| 更新开关 `UpdatePackageLocks=true` | 仅显式维护命令允许重新计算依赖图 |
| PowerShell 更新脚本 | Windows/WinUI 仓库的统一开发入口 |
| 测试项目显式 `win-x64` | 统一项目和测试的 RID 输入 |

## 参考

- https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files
- https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-restore
