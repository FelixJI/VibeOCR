# WinUI 真实环境签核 Runbook

本文件给出 Phase 5.5 剩余真实环境签核项的可执行步骤。每项标注：负责角色、所需环境、执行命令、通过标准、产物。

> 原迁移分支已合并到 `main`。自动化门禁由 `scripts/run_phase0_gate.ps1` 统一执行；以下项目仍必须在真实桌面/物理机由人工完成，不能用风险接受替代验证。

---

## 1. Win10 1809 x64 验证

**负责**：测试负责人。**环境**：Win10 1809 x64（1809, OS Build 17763）物理机或 VM。

步骤：
1. 在 Win10 1809 机器上 clone 仓库，checkout 最新 `main`。
2. 安装 .NET Desktop Runtime 10.x、Windows App Runtime 2.2、WebView2 Evergreen、Python runtime。
3. `scripts/build_winui_release.ps1` 构建。
4. 解压产物到目标目录；启动 `VibeOCR.Bootstrapper.exe`。
5. 验证：runtime 缺失时 bootstrapper 引导修复；首窗可见；导航 5 个页（识别/批量/二维码/PDF/设置）。
6. 模拟旧版升级：先装一个旧 PySide6 版，再跑升级包，确认 `cutover_sequence` 全步骤通过。

**通过标准**：安装/解压/升级无致命错；runtime 缺失走修复；无旧 UI 启动入口。

**产物**：在 `docs/releases/winui-cutover-checklist.md` 第 2 节对应行打 `[x]` + 附截图/日志。

---

## 2. 真实 OCR 模型 GPU 预热/取消

**负责**：测试负责人。**环境**：受支持 GPU + 已安装 OCR 模型权重。

步骤：
1. 启动 WinUI app，进入"设置"页。
2. `DetectGpu` 应显示 GPU 可用；选 `gpu`，点"切换后端"。
3. 确认状态变 "已切换到 gpu，需重启生效"；重启 app。
4. 在单图/批量页跑一次真实 OCR，确认 GPU 后端加载、预热完成、识别成功。
5. 跑批量时点"取消全部"，确认协作取消（无残留绿格/无冻结）。

**通过标准**：GPU 切换持久化（已由 `test_settings_backend_switch_integration.py` 验证协议层）；真实模型预热成功；批量取消及时。

**产物**：checklist 第 4 节对应行打 `[x]`。

---

## 3. 8 小时稳定性 soak

**负责**：测试负责人。**环境**：当前桌面（任意受支持 OS）。

执行（已提供的 harness）：
```powershell
.\.venv\Scripts\python.exe scripts\soak_winui.py `
  --winui-exe src\dotnet\VibeOCR.App\bin\Release\net10.0-windows10.0.17763.0\win-x64\VibeOCR.WinUI.exe `
  --duration-hours 8 `
  --report reports\local\soak-report.json
```

harness 每 10 次迭代注入一次 worker crash，并要求应用写出显式恢复结果；进程/句柄采样失败本身即为门禁失败。通过标准：`failures==0`、至少一次有效迭代、`process_drift<=2` 且句柄漂移不超阈值。rc=0 即通过。

**产物**：`reports/local/soak-report.json` 附入 checklist 第 6 节。

---

## 4. 人工可达性签核

**负责**：测试/UX 负责人。**环境**：真实桌面 + 多显示器 + Office。

逐项确认（交互式，无脚本）：
- 多显示器混合 DPI（125%/150%/200%）：截图清晰，坐标正确。
- 真实托盘：最小化到托盘、右键菜单、恢复。
- Office 剪贴板：复制富文本到 Word/WPS，格式正确。
- 键盘可达性：Tab 顺序、Enter/Esc、快捷键。
- 高对比度：系统高对比度模式下文字可读。
- 屏幕阅读器：Narrator/NVDA 读出主要控件。

**产物**：checklist 第 7 节逐项打 `[x]` + 截图。

---

## 5. 三方签字

**负责**：开发/测试/发布负责人。

在 checklist 第 8 节填入签字（姓名 + 日期）。三方均签后方可执行合入。

---

## 6. 发布审批

迁移代码已在 `main`；完成 1–5 后再创建发布 tag。更新流程必须校验 WinUI payload 与 SHA-256，通过 Bootstrapper 启动 `production`，等待 `startup.healthy`；失败只进入可见修复流程，不启动旧 UI。
