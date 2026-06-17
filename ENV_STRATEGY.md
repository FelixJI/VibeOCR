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

## 3. ✅ nvidia cu13 运行时依赖(2026-06-17 重新核实并修正)

### 关键事实

- `paddlepaddle-gpu 3.3.1` 是用 **CUDA 13.0** 编译的(`paddle.version.cuda() == "13.0"`,
  `paddle.version.cudnn() == "9.13.0"`),运行时需要 **cu13 系列** DLL:
  `cublas64_13.dll`、`cudnn64_9.dll` 等。
- paddle 的 wheel **不含**这些 CUDA 运行时库(`paddle/libs/` 下只有 `common.dll`、
  `phi.dll`、`mkldnn.dll` 等,无 cublas/cudnn)。
- 系统 NVIDIA 驱动**只提供 driver API**,不提供 cuBLAS/cuDNN 这类 user-mode
  运行时 DLL。GPU 推理必须额外获得这些库。
- paddle 通过 `Requires-Dist` 声明了精确的 cu13 传递依赖(见下表),但 **uv 不会
  自动安装它们**(`uv.lock` 不收录、`uv sync` 不下载),必须在 `pyproject.toml`
  显式声明。

paddlepaddle-gpu 3.3.1 的真实 `Requires-Dist`(权威来源):

| 包名 | 版本 | 提供 DLL |
|------|------|----------|
| `nvidia-cuda-runtime` | `==13.0.88` | `cudart64_13.dll` |
| `nvidia-cudnn-cu13` | `==9.13.0.50` | `cudnn64_9.dll` 等 |
| `nvidia-cublas` | `==13.0.2.14` | `cublas64_13.dll` |
| `nvidia-cufft` | `==12.0.0.61` | `cufft64_11.dll` |
| `nvidia-curand` | `==10.4.0.35` | `curand64_10.dll` |
| `nvidia-cusolver` | `==12.0.4.66` | `cusolver64_11.dll` |
| `nvidia-cusparse` | `==12.6.3.3` | `cusparse64_12.dll` |

(`nvidia-cublas` 还会传递拉入 `nvidia-nvjitlink`。)

### 最终方案:pyproject 显式声明 cu13 依赖(精确版本)

`pyproject.toml` 必须以 `==` 精确版本声明上述 7 个包,与 paddle 的 Requires-Dist
完全一致。**不能用 `>=`**:paddle 对这些是精确 `==` 要求,放宽版本会让 uv 装到不匹配
的版本导致 DLL 加载失败(error 126)。

### 2026-06-17 命令行验证(本机,RTX 4090 / 驱动 610.47)

```
paddle.version.cuda() = 13.0
cuda.device_count() = 1
GPU 验证通过(matmul 成功,说明 cublas64_13.dll 已正确加载)
PaddleOCR(PP-OCRv6_medium) init OK
OCR predict OK(GPU 推理成功)
```

### 历史教训(避免重蹈覆辙)

之前一次"删除全部 nvidia-* 依赖"的改动(曾基于"paddle 自带 cu12 库 / 系统驱动足够"
的假设)是**错误**的:删除后 GPU 因 `cublas64_13.dll` 缺失回退 CPU,而 CPU 路径又命中
paddle 3.3 的 PIR+oneDNN bug(见第 5 节),导致 OCR 完全不可用。当时文档里"OCR predict OK"
的验证记录不成立(很可能只验证了 paddle import,没跑完整 PaddleOCR predict)。

### 便携版安装逻辑修复(2026-06-17,第一批)

调研发现便携版 GPU 安装**从未真正可用过**,存在三个叠加 bug,现已全部修复:

1. **CUDA 13 误映射 cu129** → 改为 `cu130`。cu129 的 paddlepaddle-gpu wheel 不声明
   nvidia 依赖、也不内嵌 DLL;cu130 wheel 的 METADATA 声明 7 个 cu13 nvidia 依赖
   (nvidia-cublas==13.0.2.14 等),与开发环境一致。

2. **cu-tag 二次查表 bug**:`detect_gpu()` 返回的 `cuda_version` 已是 cu-tag
   (如 "cu130"),但 `_install_paddle_stack` 曾再用 `CUDA_VERSION_MAP.get(cuda_version)`
   把 cu-tag 当原始版本查表 → 返回 None → 有 GPU 也回退装 CPU。现已直接用 cu-tag
   构造 index URL。

3. **7 个 cu13 nvidia 包从不安装**:便携安装路径(`_install_paddle_stack`)此前只装
   paddlepaddle-gpu + paddleocr + mineru (+torch),从不装 nvidia 包。paddle GPU wheel
   又不内嵌 DLL,导致 `cublas64_13.dll` 缺失 → 运行时回退 CPU。现已从 specs 读取 7 个
   cu13 包并显式安装(单条 pip 命令,从 PyPI)。

附带修复:`subprocess.run` 的 argv 传参——多包规格(如 "torch torchvision" 或 7 个
nvidia 包)现在正确拆成独立 argv 元素(此前整个字符串被当成单个非法 requirement)。

### GPU/CPU 后端切换(第一批底层,UI 待第二批)

- `install_embedded_dependencies` / `install_dependencies` 新增 `force_backend` 参数
  (`"gpu"`/`"cpu"`/`None`),用于首启让用户选择或设置页切换。
- `switch_paddle_backend(project_root, target)`:卸载当前 paddle(两包名都卸防冲突,
  GPU→CPU 时额外卸 7 个 nvidia 包回收 ~1GB)→ 安装目标后端 → 写 `pending_backend`
  到缓存。
- `machine_cache` 新增 `pending_backend` 字段(可选,向后兼容)+ `update_cache_field`
  辅助函数原地更新单字段。
- `resolve_use_gpu` 优先读 `pending_backend`(用户已选且待生效),其次 `hardware_info.has_gpu`。

### GPU/CPU 后端选择 UI(第二批,已交付)

三处 UI 触点,复用第一批底层原语:

1. **首启合并对话框**(`widgets/backend_choice_dialog.py` `BackendChoiceDialog`):
   依赖缺失时弹出,GPU/CPU 单选 + 体积/速度提示(GPU 约 1.5GB / CPU 约 150MB),
   无 NVIDIA GPU 时 GPU 选项禁用。点"开始安装"后用 `InstallWorker(force_backend=选择)`
   跑安装,进度区实时显示。
2. **设置页"推理后端"**(`widgets/backend_options_widget.py` `BackendOptionsWidget` +
   `settings_page_controller._init_backend_options`):显示当前后端 + 待切换状态,
   单选切换后点"应用"只写 `pending_backend` 标记(不跑 pip),提示"下次重启自动下载并切换"。
3. **重启消费**(`main_window._check_pending_backend` + `widgets/switch_dialog.py`
   `SwitchDialog`):启动时检测 `pending_backend`,若与当前后端不一致则弹 `SwitchDialog`
   跑 `switch_paddle_backend`(进度对话框),成功后清除标记并启动 worker;失败则保留标记、
   不启动 worker、状态栏提示重试。pending 与当前一致时静默清除标记。

`InstallWorker`(`widgets/install_dialog.py`)加 `force_backend` 参数:`None` 时保持
自动检测(向后兼容),指定时跳过检测直接透传到 `install_embedded_dependencies`。



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
