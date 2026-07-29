# ADR-0001：VibeOCR 四仓拆分边界

- 状态：Accepted
- 日期：2026-07-29
- 决策基线：`eb8b6a6715599332ccf331dabf02468d7b1df40c`
- 详细方案：`docs/plans/2026-07-29-repository-split-plan.md`

## 背景

当前单仓中的 contracts、client、backend、Classic 与 Next 已有物理目录，但仍共享版本、Python namespace、测试根和 Release。尤其 `vibeocr-client-py` 同时拥有 wire client、Backend domain、运行时安装和 Classic 辅助逻辑，现有目录不能直接成为独立仓库。

## 决策

建立四个公开仓库，并固定唯一依赖方向：

```text
vibeocr-protocol
        ↓
vibeocr-backend
        ↓ immutable release assets
vibeocr-classic / vibeocr-next
```

四仓分别独立版本、CI 和 Release，不建立中央制品仓、common 仓或 client 仓。跨仓只消费带版本、manifest、SHA-256 和 GitHub artifact attestation 的 Release 资产；禁止 Git URL、submodule、分支源码、editable install 或本地路径进入 Release。

Python 使用 PEP 420 namespace，并由单个发行包独占子树：

- Protocol：`vibeocr.runtime_contracts`、`vibeocr.runtime_client`
- Backend：`vibeocr.backend`
- Classic：`vibeocr.classic`

.NET 使用 `VibeOCR.Runtime.Contracts`、`VibeOCR.Runtime.Client`、`VibeOCR.Next`。

Protocol 拥有跨进程 wire contract、Bootstrap Protocol、生成模型和薄传输客户端；Backend 拥有 domain、provider、runtime 与无 UI installer；Classic/Next 只拥有各自产品、平台 adapter、便携打包和更新。

## 强制约束

1. Protocol 不得依赖 Backend、Classic 或 Next。
2. Backend 不得依赖 Runtime Client transport、Classic 或 Next。
3. Classic/Next 不得 import Backend 源码，只能通过 Runtime Client 和不可变 Backend Release 资产交互。
4. 每个当前源模块必须在 `config/repository-split/ownership/` 中恰有一个目标 owner。
5. 每个 wheel archive path 只能有一个 owner。
6. Release 元数据不得包含本地路径、editable、Git URL 或 branch source dependency。
7. 旧 `openapi.snapshot.json` 仅是历史漂移证据，不是 Protocol v2 的权威规范。
8. GitHub 新仓只在单仓 Phase 0-3 退出门全部通过后创建。

## 迁移策略

迁移期间保留当前 import 和构建行为，所有目录移动、协议语义修改和远端操作分开提交。`config/repository-split/module-map.json` 是当前模块到目标仓/namespace 的机器可读迁移账本；四个 ownership manifest 是边界门的输入。

新仓采用干净根提交。旧单仓永久保留完整历史，并在四仓首版通过最终 smoke 后发布无资产迁移说明 Release、更新入口并归档。

## 后果

组件可以独立发布和回滚，但跨仓联调必须通过本地 Release staging/dev override；不能再依赖单仓 `PYTHONPATH` 掩盖缺失依赖。正式 Protocol v2 需要从真实 Backend 与两个客户端共同重建，而不是复制旧 snapshot。
