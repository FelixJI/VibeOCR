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

## 3. ⚠️ nvidia 库依赖声明问题(待决策)

### 现状(2026-06-16 核实)

`pyproject.toml` 的 nvidia 依赖声明**与 paddlepaddle-gpu 3.3.1 的实际要求不一致**:

| pyproject 声明 | paddlepaddle-gpu 3.3.1 实际 Requires-Dist |
|---------------|-------------------------------------------|
| `nvidia-cudnn-cu13>=9.23.1.3` | `nvidia-cudnn-cu12==9.9.0.52` |
| `nvidia-cublas-cu12>=12.9.2.10` | `nvidia-cublas-cu12==12.9.0.13` |
| `nvidia-cublas>=13.5.1.27` | (无对应;无后缀名指向 CUDA 13 系列包) |
| `nvidia-cufft>=12.3.0.29` | `nvidia-cufft-cu12==11.4.0.6` |
| `nvidia-curand>=10.4.3.29` | `nvidia-curand-cu12==10.3.10.19` |
| `nvidia-cusolver>=12.2.2.18` | `nvidia-cusolver-cu12==11.7.4.40` |
| `nvidia-cusparse>=12.8.1.7` | `nvidia-cusparse-cu12==12.5.9.5` |
| `nvidia-cuda-runtime>=13.3.29` | `nvidia-cuda-runtime-cu12==12.9.37` |

### 影响

- pyproject 同时拉入了 **cu12** (paddle 需要的) 和 **cu13/无后缀** (CUDA 13 系列新命名)
  两套 nvidia 库,`.venv` 中实际并存:
  - `nvidia-cublas 13.5.1.27` + `nvidia-cublas-cu12 12.9.2.10`
  - `nvidia-cudnn-cu13 9.23.1.3` + (paddle 自带的 cudnn-cu12)
  - `nvidia-cuda-runtime 13.3.29` + (paddle 自带的 cuda-runtime-cu12)
- 当前 GPU 推理能跑,说明 paddle 实际加载的是其自带 cu12 库;cu13 系列库处于"装了但
  可能没被使用"的状态,存在潜在的 DLL 冲突/体积膨胀风险。

### 待决策(改 pyproject 前必须验证 GPU 推理)

1. **方案 A(推荐)**:删除 pyproject 中所有 nvidia-* 显式依赖,完全依赖
   paddlepaddle-gpu 自带的 cu12 传递依赖。理由:paddle 已声明精确的 cu12 依赖,
   显式重复声明只会引入版本冲突。
2. **方案 B**:如果某些组件确实需要 cu13 库(需证据),则保留 cu13 声明,但应把
   包名统一为 cu13 系列(`nvidia-cudnn-cu13`, `nvidia-cublas-cu13`...),而非混用
   cu12/cu13/无后缀。
3. **验证方法**:改后在真实 GPU 机器上跑一次 OCR + PDF 文档解析,确认无 DLL 加载失败。

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
