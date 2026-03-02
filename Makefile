# VibeOCR 代码质量工具命令
# 使用 make <target> 运行

# 默认目标：运行所有检查
.PHONY: all
all: format lint type-check

# 安装开发依赖
.PHONY: install-dev
install-dev:
	uv sync --extra dev

# 格式化代码 (black + isort)
.PHONY: format
format:
	black src tests
	isort src tests
	ruff check src tests --fix

# 检查格式（不修改）
.PHONY: check-format
check-format:
	black --check --diff src tests
	isort --check-only --diff src tests

# 运行 Ruff linter
.PHONY: lint
lint:
	ruff check src tests

# 修复 lint 问题
.PHONY: lint-fix
lint-fix:
	ruff check src tests --fix

# 类型检查 (pyright + mypy)
.PHONY: type-check
type-check:
	pyright src
	mypy src

# 只运行 pyright
.PHONY: pyright
pyright:
	pyright src

# 只运行 mypy
.PHONY: mypy
mypy:
	mypy src

# 运行所有检查（CI 模式）
.PHONY: ci
ci: check-format lint type-check

# 清理缓存
.PHONY: clean
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage

# 使用 Python 脚本运行
.PHONY: check
check:
	python scripts/lint.py

.PHONY: fix
fix:
	python scripts/lint.py --fix
