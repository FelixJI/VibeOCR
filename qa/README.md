# 代码质量控制工具

本目录包含代码格式化、静态检查和测试覆盖率的脚本。

## 使用的工具

项目使用以下代码质量工具（已在 `pyproject.toml` 中配置）：

| 工具 | 用途 | 配置位置 |
|------|------|----------|
| **ruff** | 代码格式化 + linter + import 排序 | `[tool.ruff]` |
| **pyright** | 静态类型检查 | `[tool.pyright]` |
| **pytest** | 测试框架 | `[tool.pytest.ini_options]` |
| **pytest-cov** | 测试覆盖率 | `[tool.coverage]` |

> 注：format.py 也支持可选的 black/isort（通过 `--ruff` 切换），type_check.py 也支持可选的 mypy

## 快速开始

### 交互式运行（推荐）

```bash
# 交互式选择检查项
python qa/run.py

# 自动修复问题
python qa/run.py --fix

# 生成报告文件
python qa/run.py --report
```

### 运行所有检查

```bash
# 运行所有检查
python qa/run.py --all

# 自动修复问题
python qa/run.py --all --fix

# 快速检查（跳过测试）
python qa/run.py --all --quick

# CI 模式（严格检查，生成报告）
python qa/run.py --all --ci
```

### 选择性运行

```bash
# 只运行格式化和代码检查
python qa/run.py format lint

# 只运行类型检查
python qa/run.py type_check

# 运行多个检查项
python qa/run.py format lint type_check
```

### 单独运行各检查

```bash
# 格式化代码
python qa/format.py           # 格式化
python qa/format.py --check   # 只检查

# 代码检查
python qa/lint.py             # 检查问题
python qa/lint.py --fix       # 自动修复
python qa/lint.py --stats     # 显示统计

# 类型检查
python qa/type_check.py           # 运行所有类型检查
python qa/type_check.py --pyright # 只运行 pyright
python qa/type_check.py --mypy    # 只运行 mypy

# 测试覆盖率
python qa/coverage.py           # 运行测试并生成报告
python qa/coverage.py --html    # 生成 HTML 报告
python qa/coverage.py --min 80  # 设置最低覆盖率阈值

# 依赖升级
python qa/upgrade_deps.py           # 升级依赖并更新 pyproject.toml
python qa/upgrade_deps.py --dry-run # 预览变更
python qa/upgrade_deps.py --sync    # 升级后同步环境
python qa/upgrade_deps.py --dotnet-locks # 同时通过统一入口重建 .NET 锁文件
```

## 命令行选项

| 选项 | 说明 |
|------|------|
| `--all` | 运行所有检查 |
| `--fix` | 自动修复可修复的问题 |
| `--quick` | 快速检查（跳过测试） |
| `--ci` | CI 模式（严格检查，生成报告） |
| `--report` | 生成报告文件 |
| `--report-format` | 报告格式：text/json/all |
| `--no-interactive` | 禁用交互模式 |

## 报告输出

使用 `--report` 选项会在 `reports/` 目录生成报告文件：

```bash
python qa/run.py --all --report
```

生成的文件：
- `reports/report_YYYYMMDD_HHMMSS.txt` - 文本报告
- `reports/report_YYYYMMDD_HHMMSS.json` - JSON 报告

## 脚本说明

| 脚本 | 功能 |
|------|------|
| `run.py` | 主入口脚本，支持交互式选择和报告生成 |
| `format.py` | 代码格式化（ruff format，可选 black + isort） |
| `lint.py` | 代码问题检查（ruff） |
| `type_check.py` | 静态类型检查（pyright，可选 mypy） |
| `coverage.py` | 测试覆盖率（pytest-cov） |
| `upgrade_deps.py` | 依赖升级（uv lock + pyproject，可选委托统一脚本更新 .NET locks） |

## 推荐工作流

1. **开发时**: `python qa/run.py --fix` - 自动修复问题
2. **提交前**: `python qa/run.py --all --quick` - 快速验证
3. **CI 中**: `python qa/run.py --all --ci` - 严格检查并生成报告
