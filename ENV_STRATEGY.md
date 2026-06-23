# VibeOCR 环境 / CUDA / 依赖策略

> 记录开发环境 (uv) 与便携部署环境 (pip) 在 Python 运行时、CUDA、torch、nvidia 库上的
> 策略差异与决策理由。供维护者升级依赖或排查 GPU 问题时参考。
>
> 最后核实日期: 2026-06-23 ｜ paddlepaddle-gpu 3.3.1（cu126）

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

## 2. CUDA 版本选择（cu126 同源策略）

### 2.0 实际可用的 wheel（已从官方 index 核实，2026-06-23）

直接拉取 `paddlepaddle.org.cn/packages/stable/{tag}/` 与 `download.pytorch.org/whl/{tag}/torch/`
确认 cp313 / win_amd64 下**实际存在**的 wheel：

- **paddlepaddle-gpu 3.3.1**：仅 `cu118` / `cu126` / `cu129` 三个变体。
  ❌ cu121 / cu123 / cu130 **从未发布** win wheel。
- **torch (cp313/win)**：`cu118`(2.6.0–2.7.1) / `cu124`(仅 2.6.0) / `cu126`(2.6.0–2.12.1)。
  ❌ cu121 / cu128 / cu129 / cu130 **无** win cp313 wheel。

由此推导出唯一自洽的组合：CUDA 12.x 一律走 **cu126（paddle + torch 同源）**，
CUDA 11.8 走 **cu118**。`cu129` 虽为 RTX 50 系适配，但 torch 无对应 win wheel，
启用它会让 torch/lib 的 CUDA 12 DLL 与 cu129 paddle 不匹配，**本项目不启用 cu129**。

### 2.1 PaddlePaddle (主 GPU 后端)

`env_manager.CUDA_VERSION_MAP` 把 `nvidia-smi` 检测到的驱动 CUDA 版本映射到 paddle tag。
**所有 CUDA 12.x 与 13.x 都归并到 cu126**（CUDA 12 同大版本共享 `cublas64_12.dll`，
cu126 runtime 向下兼容 12.0+；CUDA 13.x 驱动向下兼容 CUDA 12 运行时）：

| 驱动 CUDA | paddle tag |
|----------|-----------|
| 11.8 | cu118 |
| 12.0 – 13.x | cu126 |

- **包来源**：`https://www.paddlepaddle.org.cn/packages/stable/{cuda_tag}/`
- **关键事实**：cu126 paddle wheel **不内嵌** cublas/cudnn（`paddle/libs/` 只有
  `common.dll` / `phi.dll` / `mkldnn.dll` 等），其 `Requires-Dist` 也不声明 nvidia
  传递依赖（`uv.lock` 不收录任何 `nvidia-*` 包）。运行时所需的 `cublas64_12.dll` /
  `cudnn64_9.dll` 等由 **torch wheel 的 `torch/lib`** 提供。

### 2.2 PyTorch (MinerU 后端)

`env_manager.TORCH_CUDA_MAP` 把 paddle tag 映射到 torch tag：

| Paddle tag | torch tag | 原因 |
|-----------|-----------|------|
| cu118 | cu118 | 同大族（CUDA 11） |
| cu126 | cu126 | 同大族（CUDA 12），同源 DLL |

不在表里的 paddle tag（理论上不可达）回落到 `cu126`（torch/lib 提供 CUDA 12 DLL，
兼容同为 CUDA 12 族的 cu129 paddle，但本项目不启用 cu129，仅作兜底）。

### 2.3 开发 vs 便携（已统一到 cu126）

| 环境 | paddle | torch | CUDA 运行时来源 |
|------|--------|-------|----------------|
| 开发 (uv) | cu126 (paddlepaddle.org.cn) | `torch 2.12.0+cu126` (pytorch-cu126 index) | `torch/lib` |
| 便携 (pip) | cu126 (CUDA 11.8 驱动例外走 cu118) | cu126 或 cu118（`TORCH_CUDA_MAP`） | `torch/lib` |

两条路径的 paddle/torch CUDA tag 现已对齐，避免历史上"开发用 cu126、便携动态选 cu124/cu128"
导致 DLL 不一致的问题。

## 3. ✅ CUDA 运行时来源：torch/lib（cu126 同源，2026-06-23 重新核实）

### 关键事实

- `paddlepaddle-gpu 3.3.1`（cu126 wheel）运行时报告 `paddle.version.cuda() == "12.9"`、
  `paddle.version.cudnn() == "9.9.0"`，需要 **CUDA 12 族** 运行时 DLL：
  `cublas64_12.dll`、`cudnn64_9.dll`、`cufft64_11.dll`、`curand64_10.dll`、
  `cusolver64_11.dll`、`cusparse64_12.dll`、`cudart64_12.dll` 等。
- paddle 的 wheel **不含**这些库（见 §2.1）。系统 NVIDIA 驱动**只提供 driver API**，
  不提供 cuBLAS/cuDNN 这类 user-mode 运行时 DLL。
- **torch cu126 wheel 自带完整的 CUDA 12.6 + cuDNN 9 运行时**（`torch/lib` 目录），
  恰好提供 paddle 所需的全部 `_12` 系列 DLL。因此**无需额外声明/安装任何
  `nvidia-*-cu12` / `nvidia-*-cu13` 包**——`pyproject.toml` 不含 nvidia 依赖，
  `uv.lock` 也不收录。

### 实现

`OCRService._setup_cuda_dll_path` / `_register_dll_directories`（`services/ocr_service.py`）
扫描 `nvidia/*` 包目录与 `torch/lib`，用 `os.add_dll_directory()` + PATH 注册，
使 paddle 能找到 cuBLAS/cuDNN。`_install_paddle_stack`（`env_manager.py`）在 GPU
安装时额外装 `torch torchvision`（从对应 index），即把 torch/lib 作为运行时来源安装到位。

### 2026-06-23 命令行验证（本机 RTX 4090 / 驱动 610.47，cu126 环境）

```
torch 2.12.0+cu126  cuda 12.6  cudnn 9.10.02
paddle 3.3.1        cuda 12.9  cudnn 9.9.0
torch/lib 含: cublas64_12.dll, cublasLt64_12.dll, cudnn64_9.dll(+7 子库),
              cufft64_11.dll, curand64_10.dll, cusolver64_11.dll,
              cusparse64_12.dll, cudart64_12.dll, nvrtc64_120_0.dll 等（共 37 DLL）
paddle.device.cuda.device_count() = 1
paddle.matmul OK（cublas64_12.dll 正确加载）→ GPU 推理可用，未回退 CPU
```

### 历史教训（避免重蹈覆辙）

早期文档（§3 旧版）曾误称"paddle 3.3.1 是 CUDA 13.0 编译、需 cu13 DLL、必须显式声明
7 个 nvidia-*-cu13 包"。这是基于 cu130 wheel 的旧策略，已被 `dc82935` 推翻：
项目实际用 **cu126 wheel**（CUDA 12 族），运行时由 torch/lib 提供，**无需任何
nvidia 包**。旧文档里的 `cublas64_13.dll` / `nvidia-cublas==13.0.2.14` 等记录
仅适用于已废弃的 cu130 路径，**不再适用**。

### GPU/CPU 后端切换

- `install_embedded_dependencies` / `install_dependencies` 有 `force_backend` 参数
  （`"gpu"`/`"cpu"`/`None`），用于首启让用户选择或设置页切换。
- `switch_paddle_backend(project_root, target)`：卸载当前 paddle（两包名都卸防冲突）
  → 安装目标后端 → 写 `pending_backend` 到缓存。CUDA 运行时由 torch wheel 的
  torch/lib 提供，切换后端**无需单独装卸 nvidia 包**。
- `machine_cache` 有 `pending_backend` 字段（可选，向后兼容）+ `update_cache_field`
  辅助函数原地更新单字段。
- `resolve_use_gpu` 优先读 `pending_backend`（用户已选且待生效），其次 `hardware_info.has_gpu`。

### GPU/CPU 后端选择 UI

三处 UI 触点，复用底层原语：

1. **首启合并对话框**（`widgets/backend_choice_dialog.py` `BackendChoiceDialog`）：
   依赖缺失时弹出，GPU/CPU 单选 + 体积/速度提示（GPU 约 1.5GB / CPU 约 150MB），
   无 NVIDIA GPU 时 GPU 选项禁用。点"开始安装"后用 `InstallWorker(force_backend=选择)`
   跑安装，进度区实时显示。
2. **设置页"推理后端"**（`widgets/backend_options_widget.py` `BackendOptionsWidget` +
   `settings_page_controller._init_backend_options`）：显示当前后端 + 待切换状态，
   单选切换后点"应用"只写 `pending_backend` 标记（不跑 pip），提示"下次重启自动下载并切换"。
3. **重启消费**（`main_window._check_pending_backend` + `widgets/switch_dialog.py`
   `SwitchDialog`）：启动时检测 `pending_backend`，若与当前后端不一致则弹 `SwitchDialog`
   跑 `switch_paddle_backend`（进度对话框），成功后清除标记并启动 worker；失败则保留标记、
   不启动 worker、状态栏提示重试。pending 与当前一致时静默清除标记。

`InstallWorker`（`widgets/install_dialog.py`）有 `force_backend` 参数：`None` 时保持
自动检测（向后兼容），指定时跳过检测直接透传到 `install_embedded_dependencies`。



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

## 4.1 CPU 推理禁用 mkldnn(paddle 3.3 PIR+oneDNN bug 绕过)

### 现象

CPU 推理(predict)抛出:
```
NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
not support [pir::ArrayAttribute<pir::DoubleAttribute>]
  (at ..\paddle\fluid\framework\new_executor\instruction\onednn\onednn_instruction.cc:118)
```

### 根因

paddle 3.3 默认启用 PIR(新中间表示)executor + oneDNN(MKL-DNN),但 PIR 到 oneDNN
instruction 的属性转换器对 `ArrayAttribute<pir::DoubleAttribute>` **未实现**,
PP-OCRv6 模型推理触发该路径即崩溃。上游已知问题:
- https://github.com/PaddlePaddle/PaddleOCR/issues/17539
- https://github.com/PaddlePaddle/Paddle/issues/77340

### 绕过

`OCRService._create_pipeline` 在 `device == "cpu"` 时向 `PaddleOCR` / `PPStructureV3` /
`PaddleOCRVL` 传 `enable_mkldnn=False`,避开有 bug 的 oneDNN PIR 转换路径。
代价:CPU 推理略慢(GPU 不受影响)。上游修复后可移除。

### 命令行验证(2026-06-17)

```
PaddleOCR(device="cpu", enable_mkldnn=False)
  -> init OK
  -> predict OK(结果数: 1)   # NotImplementedError 消失
```

## 5. pip / PyTorch 镜像源

| 用途 | SSOT | 说明 |
|------|------|------|
| pip 镜像 | `network_detector._PIP_MIRRORS` | 按 network_type (domestic→清华 / international→pypi) |
| PyTorch 镜像 | `env_config.PYTORCH_MIRROR_SOURCES` + `get_pytorch_mirror` | nju/sjtu/official |
| 网络检测 | `NetworkDetector` | 7 天缓存,探测 bcebos vs huggingface 速度 |

`env_manager.detect_network_source` / `get_pip_source` 委托 `NetworkDetector`,
不再有独立的旧网络检测逻辑。
