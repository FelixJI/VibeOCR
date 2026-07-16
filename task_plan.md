# 任务计划：稳定 NuGet 锁文件管理

## 目标

让 `packages.lock.json` 默认只读、只通过统一命令更新，并固定 WinUI 测试恢复为 `win-x64`，消除不同开发环境产生的 RID 漂移。

## 当前阶段

阶段 3：验证与交付

## 阶段

### Phase 1：确认现状与方案
- [x] 确认现有锁文件差异只增加 RID 图，没有包版本变化
- [x] 确认 SDK、中央包版本和 CI locked mode 已存在
- [x] 确定默认 locked mode + 显式更新开关方案
- **Status:** complete

### Phase 2：实施
- [x] 在 `Directory.Build.props` 中启用默认 locked mode
- [x] 固定 App 测试项目为 `win-x64`
- [x] 新增统一锁文件更新脚本
- [x] 撤销两份 RID 漂移锁文件修改
- **Status:** complete

### Phase 3：验证与交付
- [x] 静态检查 XML、JSON 和 PowerShell 脚本
- [x] 验证普通 restore 属性为 locked mode，更新脚本显式关闭 locked mode
- [x] 检查最终 diff 和工作区状态
- **Status:** complete

## 决策

| 决策 | 理由 |
|---|---|
| 锁文件继续纳入 Git | 应用和测试入口需要完整传递依赖锁定 |
| 默认 `RestoreLockedMode=true` | 普通 restore 不允许静默改写锁文件 |
| `UpdatePackageLocks=true` 作为唯一更新开关 | 依赖升级成为显式动作 |
| App 测试项目固定 `win-x64` | 与当前唯一发布架构一致 |
| 撤销当前两份锁文件修改 | 当前差异只有未支持的 RID 图，无版本更新 |

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 本机没有 global.json 要求的 .NET SDK 10.0.302 | 1 | 使用静态属性检查；运行级 restore 验证将明确标注环境限制 |
| 合并补丁更新计划阶段上下文不匹配，补丁整体未应用 | 1 | 拆分锁文件清理与计划状态更新补丁 |
| PowerShell 断言字符串中的 `$(UpdatePackageLocks)` 被当成子表达式执行 | 1 | 改用正则检查条件内容，避免字符串插值 |
| `apply_patch` 无法表达文件末尾无换行 | 1 | 对已批准撤销的单个锁文件使用精确 `git restore --source=HEAD` |
| 沙箱内 `git restore` 无法创建 `.git/index.lock` | 1 | 经授权在沙箱外执行精确单文件恢复，成功 |
| 完成检查脚本被 PowerShell 执行策略拦截 | 1 | 按技能约定使用 `-ExecutionPolicy Bypass` 重试 |
| 中文阶段标记未被完成检查脚本识别，报告 0/0 | 1 | 保留中文说明，改用机器可读的英文 Phase/Status 标记 |
