# VibeOCR 环境 / CUDA / 依赖策略

> 记录开发环境 (uv) 与便携部署环境 (pip) 在 Python 运行时、CUDA、torch、nvidia 库上的
> 策略差异与决策理由。供维护者升级依赖或排查 GPU 问题时参考。
>
> 最后核实日期: 2026-06-16 ｜ paddlepaddle-gpu 3.3.1

---

## 1. Python 运行时

| 环境 | 来源 | 版本 |
|------|------|------|
| 开发 (uv) | 系统/uv 安装的 CPython | 由 `.python-version` (3.13) 约束 |
| 便携部署 | python-build-standalone | `env_config.PYTHON_VERSION_SHORT` (3.13) + `PATCH` (12) → 3.13.12 |

- **单一源**:`.python-version` 文件是 Python 版本的权威。`bump_version.py` 生成的
  `version.json` 的 `python_version` 字段读自该文件(不再硬编码)。
- **升级方法**:改 `.python-version` + `env_config.PYTHON_BUILD_STANDALONE_TAG`/`PATCH` +
  `pyproject.toml` 的 `requires-python`/`pyright.pythonVersion`/`mypy.python_version`。

## 2. CUDA 版本选择

### 2.1 PaddlePaddle (主 GPU 后端)

`env_manager.CUDA_VERSION_MAP` 将检测到的驱动 CUDA 版本映射到 PaddlePaddle 支持的 tag
(cu118/cu121/cu123/cu126/cu129)。CUDA 13.x 映射到 cu129(Paddle 最新兼容)。

- **包来源**:`https://www.paddlepaddle.org.cn/packages/stable/{cuda_tag}/`
- **关键事实**:paddlepaddle-gpu 3.3.1 的 nvidia 依赖**全部是 cu12 系列**
  (nvidia-cuda-runtime-cu12==12.9.37, nvidia-cudnn-cu12==9.9.0.52 等)。

### 2.2 PyTorch (MinerU 后端)

`env_manager.TORCH_CUDA_MAP` 将 Paddle 的 CUDA tag 映射到 PyTorch 的 CUDA tag
(PyTorch wheel 的 tag 粒度更粗):

| Paddle tag | PyTorch tag | 原因 |
|-----------|-------------|------|
| cu129 | cu128 | PyTorch 无 cu129 wheel,cu128 向下兼容 |
| cu123 | cu124 | PyTorch 跳过 cu123 |

### 2.3 ⚠️ 开发 vs 便携的 torch 版本策略不一致(已知,有意保留)

| 环境 | torch 来源 | CUDA tag |
|------|-----------|----------|
| 开发 (uv) | `pyproject.toml [tool.uv.sources]` → `pytorch-cu126` index | **恒定 cu126** |
| 便携 (pip) | `TORCH_CUDA_MAP` + `get_pytorch_mirror` | **按 GPU 动态** (cu118/cu121/cu124/cu126/cu128) |

**理由**:开发机 CUDA 驱动版本相对固定,uv 锁定 cu126 保证可复现;便携部署面向
不同用户硬件,需按实际 GPU 动态选 tag。两条路径的 torch 版本可能不同,但都通过
各自的 index 安装,功能上 MinerU 对 torch 小版本差异不敏感。

## 3. ✅ nvidia 库依赖问题已解决(方案 A,2026-06-17 验证)

### 历史问题(2026-06-16 发现)

`pyproject.toml` 曾显式声明 8 个 nvidia-* 依赖,包名后缀混乱(cu12 / cu13 / 无后缀混用),
与 `paddlepaddle-gpu 3.3.1` 通过 `Requires-Dist` 要求的 cu12 系列包不匹配。导致 `.venv`
里同时装了两套 nvidia 库(cu12 + cu13),体积膨胀且存在潜在 DLL 冲突风险。

### 根因(方案 A 验证时发现)

删除全部 8 个 nvidia-* 显式依赖并 `uv sync` 后,发现:
- paddlepaddle-gpu 声明的 `nvidia-*-cu12==x.y` 传递依赖**被 uv 解析但未锁定安装**
  (uv 在 Windows 上不强制安装这些 optional 依赖)。
- **paddle GPU 推理实际不依赖 pip 包带的 CUDA 运行时库**,而是直接使用
  **系统级 NVIDIA 驱动**提供的 CUDA 运行时。

### 最终方案:删除全部 nvidia-* 显式依赖

验证环境:NVIDIA 驱动 610.47 / CUDA UMD 13.3,GPU Compute Capability 8.9。

```
# uv sync 后 nvidia 包数量
installed nvidia packages: 0

# paddle GPU 初始化(无任何 nvidia pip 包)
paddle import OK
CUDAPlace(0) OK

# PaddleOCR 完整推理(PP-OCRv6_medium,GPU)
PaddleOCR init OK          # 加载 PP-OCRv6_medium_det / PP-OCRv6_medium_rec
OCR predict OK             # GPU 推理成功
GPU Compute Capability: 8.9, Driver API Version: 13.3, Runtime API Version: 12.9
```

### 结论与前提

- **本机(开发环境)无需任何 nvidia-* pip 包**,系统 NVIDIA 驱动足够。
- **便携部署环境**:`env_manager.install_dependencies` 不再安装 nvidia 包;
  若目标机器无 NVIDIA 驱动,会回退 CPU 模式(原有逻辑不变)。
- **风险**:若某机器驱动过旧(< paddle 编译时的 CUDA 版本),可能需要手动装
  cu12 运行时库。届时按需在便携环境补充,不在 pyproject 全局声明。

## 4. 依赖版本单一源 (SSOT)

经过 B1 整改,依赖规格的加载链如下:

```
开发环境:  pyproject.toml [project.dependencies]  ← 权威源
                    ↓
          env_manager._load_dep_specs()  (读 pyproject,读不到则 raise)
                    ↓
          install_dependencies / install_embedded_dependencies

打包环境:  bump_version.py 从 pyproject 生成 version.json
                    ↓ (dep_versions 键名归一为纯包名,与 OCR_CHECK_MODULES 一致)
          env_manager._load_dep_specs()  (读 version.json)
```

- **OCR 检测清单**:`env_config.OCR_CHECK_MODULES` ({import 模块名: pip 包名})
  是"检测哪些依赖"的唯一来源,`env_manager._check_imports` 遍历它。
- **无陈旧 fallback**:`_load_dep_specs` 读不到源时 raise(含修复提示),不再悄悄
  回退到硬编码旧版本。

## 5. pip / PyTorch 镜像源

| 用途 | SSOT | 说明 |
|------|------|------|
| pip 镜像 | `network_detector._PIP_MIRRORS` | 按 network_type (domestic→清华 / international→pypi) |
| PyTorch 镜像 | `env_config.PYTORCH_MIRROR_SOURCES` + `get_pytorch_mirror` | nju/sjtu/official |
| 网络检测 | `NetworkDetector` | 7 天缓存,探测 bcebos vs huggingface 速度 |

`env_manager.detect_network_source` / `get_pip_source` 委托 `NetworkDetector`,
不再有独立的旧网络检测逻辑。
