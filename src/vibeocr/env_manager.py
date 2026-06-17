"""环境管理模块：负责自动部署 Python 运行时（python-build-standalone）和管理项目依赖"""

import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.request import Request, urlopen

from vibeocr.machine_cache import (
    create_cache_entry,
    is_cache_valid,
    update_cache_field,
)
from vibeocr.services.env_config import (
    PYTHON_BUILD_STANDALONE_ASSET,
    PYTHON_BUILD_STANDALONE_BASE,
    PYTHON_BUILD_STANDALONE_MIRRORS,
    PYTHON_BUILD_STANDALONE_TAG,
    PYTHON_VERSION_SHORT,
    get_pytorch_mirror,
)

# Python 运行时下载地址（python-build-standalone）
# GitHub 直链 + 国内镜像（NJU/ghproxy）已在 env_config.PYTHON_BUILD_STANDALONE_MIRRORS 定义
PYTHON_STANDALONE_URLS = [PYTHON_BUILD_STANDALONE_BASE, *PYTHON_BUILD_STANDALONE_MIRRORS]

# CUDA 版本映射到 PaddlePaddle 支持的版本
# PaddlePaddle GPU 版本支持: cu118, cu121, cu123, cu126, cu129, cu130 等
# CUDA 13.x → cu130：cu130 wheel 的 METADATA 声明 7 个 cu13 nvidia 运行时依赖
# (nvidia-cublas==13.0.2.14 等)，与本机开发环境一致（已验证 GPU 推理可跑）。
# cu129 wheel 不声明 nvidia 依赖、也不内嵌 DLL，无法提供 cublas64_13.dll。
# 注意：_install_paddle_stack 接收的 cuda_version 已是 cu-tag（detect_cuda_version 输出），
# 此映射仅用于 detect_cuda_version 把原始版本（nvidia-smi 输出）转成 cu-tag。
CUDA_VERSION_MAP = {
    "11.8": "cu118",
    "12.0": "cu121",
    "12.1": "cu121",
    "12.2": "cu123",
    "12.3": "cu123",
    "12.4": "cu126",
    "12.5": "cu126",
    "12.6": "cu126",
    "12.7": "cu129",
    "12.8": "cu129",
    "12.9": "cu129",
    # CUDA 13.x → cu130 (wheel 声明 cu13 nvidia 依赖)
    "13.0": "cu130",
    "13.1": "cu130",
    "13.2": "cu130",
}

# PyTorch CUDA 版本映射（PaddlePaddle CUDA tag → PyTorch CUDA tag）
# PyTorch 官方 wheel 的 CUDA tag 粒度比 PaddlePaddle 粗：
# - cu129 → cu128: PyTorch 无 cu129 wheel，cu128 是向下兼容的最近版本
# - cu123 → cu124: 同理，PyTorch 跳过 cu123
# 开发环境 (uv) 的 torch 来源见 pyproject.toml [tool.uv.sources]，恒定 cu126；
# 便携环境 (pip) 的 torch 来源由此映射 + get_pytorch_mirror 决定。
TORCH_CUDA_MAP = {
    "cu118": "cu118",
    "cu121": "cu121",
    "cu123": "cu124",
    "cu126": "cu126",
    "cu129": "cu128",
}

_dep_specs_cache: dict[str, str] | None = None


def _load_dep_specs() -> dict[str, str]:
    """从 pyproject.toml 或 version.json 加载依赖版本规格

    开发环境读 pyproject.toml（权威源）；打包环境读 version.json（由 bump_version 生成）。
    两者皆缺失时抛 RuntimeError，不再悄悄回退到陈旧 fallback ——
    陈旧 fallback 会装到旧版本（paddleocr 3.6.0 / mineru 3.2.0 已落后于 pyproject）。

    Returns:
        {base_name: full_spec}，如 {"paddleocr": "paddleocr[doc-parser]>=3.7.0"}

    Raises:
        RuntimeError: pyproject.toml 与 version.json 均不存在
    """
    global _dep_specs_cache
    if _dep_specs_cache is not None:
        return _dep_specs_cache

    import re

    project_root = get_project_root()

    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        import tomllib

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        deps = data.get("project", {}).get("dependencies", [])
        specs: dict[str, str] = {}
        for dep in deps:
            dep = dep.strip()
            if dep.startswith("#"):
                continue
            m = re.match(r"^([a-zA-Z0-9_.-]+)", dep)
            if m:
                specs[m.group(1).lower()] = dep
        _dep_specs_cache = specs
        return specs

    version_json = project_root / "version.json"
    if version_json.exists():
        import json

        data = json.loads(version_json.read_text(encoding="utf-8"))
        specs = {k: f"{k}>={v}" for k, v in data.get("dep_versions", {}).items()}
        _dep_specs_cache = specs
        return specs

    raise RuntimeError(
        "无法加载依赖规格：pyproject.toml 与 version.json 均不存在。\n"
        "开发环境请确认在项目根目录运行，或执行 `uv sync` 生成环境；\n"
        "打包环境请确认 version.json 已由 bump_version.py 生成。"
    )


def detect_network_source() -> Literal["domestic", "international"]:
    """检测网络类型（委托 NetworkDetector）

    注意：返回值用于选择 pip 镜像、PaddleX/MinerU 模型源。
    """
    from vibeocr.network_detector import NetworkDetector as _ND

    detector = _ND(get_project_root())
    return detector.network_type


def get_pip_source(
    network_type: Literal["domestic", "international"] = "domestic",
) -> str:
    """获取 pip 镜像源 URL（委托 NetworkDetector）

    注意：参数 network_type 目前未直接使用 —— NetworkDetector 基于自身检测结果返回。
    """
    from vibeocr.network_detector import NetworkDetector as _ND

    detector = _ND(get_project_root())
    return detector.pip_mirror_url


def get_environment_mode(project_root: Path) -> Literal["venv", "portable", "none"]:
    """检测当前环境模式

    Returns:
        "venv": 使用 .venv 虚拟环境
        "portable": 使用便携式 python/ 目录
        "none": 无任何环境
    """
    if (project_root / ".venv").exists():
        return "venv"
    if (project_root / "python").exists():
        return "portable"
    return "none"


def get_embedded_python_path(project_root: Path) -> Path:
    """获取嵌入式Python目录路径

    优先使用 .venv 虚拟环境(开发模式),如果不存在则使用 python/ 目录(便携式部署)
    """
    venv_python = (
        project_root / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else project_root / ".venv" / "bin" / "python"
    )
    portable_python = project_root / "python"

    # 优先使用虚拟环境
    if venv_python.exists():
        return venv_python.parent
    return portable_python


def get_embedded_python_executable(project_root: Path) -> Path:
    """获取嵌入式Python可执行文件路径

    优先使用 .venv 虚拟环境(开发模式),如果不存在则使用 python/ 目录(便携式部署)
    """
    if os.name == "nt":  # Windows
        venv_python = project_root / ".venv" / "Scripts" / "python.exe"
        portable_python = project_root / "python" / "python.exe"
    else:
        venv_python = project_root / ".venv" / "bin" / "python"
        portable_python = project_root / "python" / "bin" / "python"

    # 优先使用虚拟环境
    if venv_python.exists():
        return venv_python
    return portable_python


def get_embedded_python(project_root: Path | None = None) -> Path:
    """获取嵌入式Python可执行文件路径（兼容别名）

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        Python 可执行文件路径
    """
    if project_root is None:
        project_root = get_project_root()
    return get_embedded_python_executable(project_root)


def get_embedded_venv_python(project_root: Path | None = None) -> Path:
    """获取虚拟环境 Python 可执行文件路径

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        虚拟环境 Python 可执行文件路径
    """
    if project_root is None:
        project_root = get_project_root()

    if os.name == "nt":  # Windows
        return project_root / ".venv" / "Scripts" / "python.exe"
    return project_root / ".venv" / "bin" / "python"


def is_embedded_python_ready(project_root: Path | None = None) -> bool:
    """检查嵌入式 Python 是否准备好

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        是否准备好
    """
    if project_root is None:
        project_root = get_project_root()
    return get_embedded_python_executable(project_root).exists()


def get_embedded_python_info(project_root: Path | None = None) -> dict[str, str | bool]:
    """获取嵌入式 Python 信息

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        包含 Python 信息的字典:
        - path: Python 可执行文件路径
        - mode: 环境模式 (venv/portable/none)
        - ready: 是否准备好
    """
    if project_root is None:
        project_root = get_project_root()

    python_path = get_embedded_python_executable(project_root)
    mode = get_environment_mode(project_root)
    ready = python_path.exists()

    return {
        "path": str(python_path),
        "mode": mode,
        "ready": ready,
    }


def is_embedded_python_installed(project_root: Path) -> bool:
    """检查嵌入式Python是否已安装(支持虚拟环境和便携式两种模式)"""
    mode = get_environment_mode(project_root)
    if mode == "none":
        return False
    python_exe = get_embedded_python_executable(project_root)
    return python_exe.exists()


def download_file_with_progress(
    url: str, dest_path: Path, description: str = "下载"
) -> bool:
    """下载文件并显示进度"""
    try:
        print(f"[{description}] 正在下载: {url}")
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})

        with urlopen(req, timeout=30) as response:
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            chunk_size = 8192

            with open(dest_path, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        progress = int(downloaded / total_size * 100)
                        print(
                            f"\r[{description}] 进度: {progress}% ({downloaded // 1024 // 1024}MB / {total_size // 1024 // 1024}MB)",
                            end="",
                        )

        print(f"\r[{description}] 下载完成")
        return True

    except Exception as e:
        print(f"\r[{description}] 下载失败: {e}")
        return False


def install_embedded_python(
    project_root: Path, network_type: Literal["domestic", "international"] = "domestic"
) -> tuple[bool, str]:
    """
    下载并安装 Python 运行时（python-build-standalone）

    相比旧 embeddable 方案的改进：
    - 完整标准库 + 自带 pip，无需 get-pip.py / 手写 ._pth / 手建 site-packages
    - 标准 site 机制，paddle/torch 的 .pth、entry_points、DLL 查找按设计行为工作
    - 上游仅发布 .tar.gz，用标准库 tarfile 解压

    Args:
        project_root: 项目根目录
        network_type: 网络类型（domestic 时优先国内镜像，international 时优先 GitHub 直链）

    Returns:
        (是否成功, 消息)
    """
    # 检查环境模式
    mode = get_environment_mode(project_root)

    # 如果已存在虚拟环境,跳过安装
    if mode == "venv":
        venv_python = get_embedded_python_executable(project_root)
        if venv_python.exists():
            return True, f"检测到虚拟环境: {venv_python}"
        return False, "虚拟环境不完整,请重新创建"

    python_dir = project_root / "python"

    if python_dir.exists():
        return True, f"Python 运行时已安装: {python_dir}"

    print("\n" + "=" * 50)
    print("[环境安装] 安装 Python 运行时（python-build-standalone）")
    print("=" * 50)

    # 根据网络类型排序下载源：international 优先 GitHub 直链，domestic 优先国内镜像
    urls = list(PYTHON_STANDALONE_URLS)
    if network_type == "domestic":
        # 国内镜像在前
        mirrors = PYTHON_BUILD_STANDALONE_MIRRORS
        urls = [*mirrors, PYTHON_BUILD_STANDALONE_BASE]
    else:
        urls = [PYTHON_BUILD_STANDALONE_BASE, *PYTHON_BUILD_STANDALONE_MIRRORS]

    # 去重保序
    seen: set[str] = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        tar_path = temp_path / PYTHON_BUILD_STANDALONE_ASSET.replace(".tar.gz", ".tgz")

        download_ok = False
        used_url = ""
        for i, url in enumerate(urls, 1):
            label = "Python(GitHub)" if "github.com" in url else "Python(镜像)"
            print(f"[环境安装] 尝试下载源 {i}/{len(urls)}: {label}")
            if download_file_with_progress(url, tar_path, label):
                download_ok = True
                used_url = url
                break

        if not download_ok:
            return (
                False,
                f"无法下载 Python 运行时，请手动下载:\n"
                f"{PYTHON_BUILD_STANDALONE_BASE}\n"
                f"（国内镜像可访问 https://mirror.nju.edu.cn/github-release/"
                f"astral-sh/python-build-standalone/{PYTHON_BUILD_STANDALONE_TAG}/ ）\n"
                f"解压 {PYTHON_BUILD_STANDALONE_ASSET} 内的 python/ 到: {python_dir}",
            )

        print(f"[环境安装] 下载完成，正在解压 {PYTHON_BUILD_STANDALONE_ASSET}...")
        try:
            python_dir.mkdir(parents=True, exist_ok=True)
            # tar.gz 内顶层为 install_only/python/，需 flatten 到 project_root/python/
            # （python_dir 本身即 python/，故去掉 install_only/ 和内层 python/ 两层前缀）
            with tarfile.open(tar_path, "r:gz") as tar:
                members = tar.getmembers()
                for member in members:
                    rel = member.name
                    # 去掉 install_only/ 首层前缀（若存在）
                    if rel.startswith("install_only/"):
                        rel = rel[len("install_only/") :]
                    # 去掉 python/ 首层前缀（python_dir 本身即 python/，避免 python/python/ 嵌套）
                    if rel.startswith("python/"):
                        rel = rel[len("python/") :]
                    if not rel:
                        continue
                    # 防御 path traversal
                    if rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
                        continue
                    target = python_dir / rel
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        src = tar.extractfile(member)
                        if src is not None:
                            with src, open(target, "wb") as dst:
                                dst.write(src.read())
                    elif member.issym() or member.islnk():
                        # standalone 偶尔含符号链接（如 python3 → python3.13），跳过以保 Windows 兼容
                        continue
            print("[环境安装] 解压完成")
        except Exception as e:
            # 解压失败时清理半成品目录，避免误判为已安装
            import shutil

            shutil.rmtree(python_dir, ignore_errors=True)
            return False, f"解压失败: {e}"

    python_exe = get_embedded_python_executable(project_root)
    if not python_exe.exists():
        return False, (
            f"解压后未找到 {python_exe}，下载源可能损坏: {used_url}\n"
            f"请手动下载并解压: {PYTHON_BUILD_STANDALONE_BASE}"
        )

    # build-standalone 自带 pip，做一次健康检查（失败不阻断，pip install 阶段会再报错）
    try:
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            print(f"[环境安装] pip 可用: {result.stdout.strip()}")
        else:
            print(f"[环境安装] 警告: pip 自检失败: {result.stderr[-200:] if result.stderr else ''}")
    except Exception as e:
        print(f"[环境安装] 警告: pip 自检异常: {e}")

    return True, f"Python 运行时安装成功: {python_exe}"


def check_current_environment_dependencies() -> dict[str, bool]:
    """检查当前运行Python环境的生产依赖是否已安装

    用于检测 PySide6, Pillow 等生产环境依赖

    Returns:
        依赖状态字典
    """
    dependencies = {
        "PySide6": False,
        "PIL": False,
    }

    for pkg in dependencies:
        try:
            __import__(pkg)
            dependencies[pkg] = True
        except ImportError:
            dependencies[pkg] = False

    return dependencies


def check_embedded_environment_dependencies(
    project_root: Path, use_cache: bool = True
) -> dict[str, bool]:
    """检查嵌入式Python环境的OCR依赖是否已安装

    用于检测 PaddlePaddle, PaddleX 等OCR功能依赖
    注意：paddlepaddle-gpu 和 paddlepaddle 是二选一关系，
    检测 paddle 模块即可（GPU或CPU版本都会导入为 paddle）

    Args:
        project_root: 项目根目录
        use_cache: 是否使用缓存（默认True）

    Returns:
        依赖状态字典，包含:
        - paddlepaddle: 是否安装了PaddlePaddle（GPU或CPU版本）
        - paddleocr: 是否安装了PaddleOCR
        - is_gpu: 是否是GPU版本（可选字段）
    """
    # 1. 尝试使用缓存
    if use_cache:
        is_valid, cached_data = is_cache_valid(project_root)
        if is_valid and cached_data:
            print("[依赖检测] 使用缓存结果")
            return cached_data.get("dependencies", {})

    # 2. 执行实际检测（委托给 _check_imports 原语，模块清单由 OCR_CHECK_MODULES 统一）
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return {}

    dependencies = _check_imports(python_exe)
    if dependencies.get("paddlepaddle"):
        print("[依赖检测] PaddlePaddle已安装")

    # 3. 更新缓存
    has_gpu, cuda_version = detect_gpu()
    hardware_info = {
        "has_gpu": has_gpu,
        "cuda_version": cuda_version,
    }
    create_cache_entry(project_root, dependencies, hardware_info)

    return dependencies


def check_embedded_environment_dependencies_fresh(
    project_root: Path,
) -> dict[str, bool]:
    """强制重新检测依赖（忽略缓存）"""
    return check_embedded_environment_dependencies(project_root, use_cache=False)


def check_dependencies(project_root: Path) -> dict[str, bool]:
    """检查嵌入式Python的依赖是否已安装

    注意：此函数检测的是嵌入式环境的依赖，包括生产依赖和OCR依赖。
    对于生产环境依赖检测，请使用 check_current_environment_dependencies()
    对于仅检测OCR依赖，请使用 check_embedded_environment_dependencies()

    注意：paddlepaddle-gpu 和 paddlepaddle 是二选一关系，
    检测 paddle 模块即可（GPU或CPU版本都会导入为 paddle）
    """
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return {}

    # OCR 依赖委托给 _check_imports 原语
    dependencies: dict[str, bool] = _check_imports(python_exe)

    # 生产依赖（PySide6/PIL）单独检测，timeout 较短
    for pkg in ["PySide6", "PIL"]:
        try:
            result = subprocess.run(
                [str(python_exe), "-c", f"import {pkg}"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            dependencies[pkg] = result.returncode == 0
        except Exception:
            dependencies[pkg] = False

    return dependencies


def is_production_environment_ready() -> tuple[bool, list[str]]:
    """检查生产环境是否就绪

    Returns:
        (是否就绪, 缺失的依赖列表)
    """
    deps = check_current_environment_dependencies()
    missing = [pkg for pkg, installed in deps.items() if not installed]
    return len(missing) == 0, missing


def _check_imports(python_exe: Path) -> dict[str, bool]:
    """检测嵌入式 Python 可导入哪些 OCR 模块（单一实现，消除重复）

    遍历 env_config.OCR_CHECK_MODULES，对每个 import 模块名执行
    `python -c "import <module>"`，结果以包名为 key 返回。

    Args:
        python_exe: 目标 Python 可执行文件

    Returns:
        {包名: 是否可导入}，如 {"paddlepaddle": True, "torch": False}
    """
    from vibeocr.services.env_config import OCR_CHECK_MODULES, OCR_CHECK_TIMEOUTS

    deps: dict[str, bool] = {}
    for module, pkg in OCR_CHECK_MODULES.items():
        try:
            result = subprocess.run(
                [str(python_exe), "-c", f"import {module}"],
                capture_output=True,
                text=True,
                timeout=OCR_CHECK_TIMEOUTS.get(module, 15),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deps[pkg] = result.returncode == 0
        except Exception:
            deps[pkg] = False
    return deps


def _quick_verify_deps(python_exe: Path) -> dict[str, bool]:
    """轻量验证依赖是否实际已安装（用于校验缓存）

    只做简单的 import 检测，不获取 GPU 信息，速度较快。
    委托给 _check_imports 原语，模块清单由 OCR_CHECK_MODULES 统一管理。
    """
    return _check_imports(python_exe)


def is_embedded_environment_ready(project_root: Path) -> tuple[bool, list[str]]:
    """检查嵌入式OCR环境是否就绪

    Args:
        project_root: 项目根目录

    Returns:
        (是否就绪, 缺失的依赖列表)
    """
    # 首先检查 Python 运行时是否存在
    python_exe = get_embedded_python_executable(project_root)
    if not python_exe.exists():
        return False, ["Python 运行时未安装"]

    deps = check_embedded_environment_dependencies(project_root)
    # 只检查 paddlepaddle 和 paddleocr，排除 is_gpu 等元数据字段
    required_deps = ["paddlepaddle", "paddleocr", "mineru"]
    missing = [pkg for pkg in required_deps if pkg not in deps or not deps[pkg]]

    # 缓存显示缺失时，做一次轻量验证排除过期缓存
    if missing:
        verified = _quick_verify_deps(python_exe)
        still_missing = [pkg for pkg in missing if not verified.get(pkg, False)]
        if still_missing != missing:
            # 缓存已过期，用验证结果更新
            print("[依赖检查] 缓存已过期，使用实时检测结果")
            missing = still_missing
            # 重新写入缓存
            for pkg, installed in verified.items():
                deps[pkg] = installed
            has_gpu, cuda_version = detect_gpu()
            hardware_info = {
                "has_gpu": has_gpu,
                "cuda_version": cuda_version,
            }
            create_cache_entry(project_root, deps, hardware_info)

    return len(missing) == 0, missing


def _install_paddle_stack(
    python_exe: Path,
    specs: dict[str, str],
    pip_source: str,
    network_type: Literal["domestic", "international"],
    use_gpu: bool,
    cuda_version: str | None,
    report_fn: Callable[[str, str], None],
    extra_requirements: list[tuple[str, str, str]],
    success_msg: str,
) -> tuple[bool, str]:
    """安装 PaddlePaddle + PaddleOCR + MinerU (+可选 torch) 依赖栈

    install_dependencies 与 install_embedded_dependencies 的共享实现，
    消除 pip 升级、GPU/CPU 分支、torch index 计算、PyPI 回退等重复逻辑。

    Args:
        python_exe: 目标 Python 可执行文件
        specs: _load_dep_specs() 返回的依赖规格
        pip_source: pip 镜像源 URL
        network_type: 网络类型（决定 torch 镜像）
        use_gpu: 是否安装 GPU 版本
        cuda_version: CUDA 版本字符串
        report_fn: 日志回调 (stage, msg)
        extra_requirements: 前置额外包 [(name, spec, index), ...]（如 PySide6/Pillow）
        success_msg: 全部成功时的返回消息

    Returns:
        (是否成功, 消息)
    """
    paddle_gpu_spec = specs["paddlepaddle-gpu"]
    paddle_cpu_spec = paddle_gpu_spec.replace("paddlepaddle-gpu", "paddlepaddle")

    # 决定 PaddlePaddle 版本（GPU 优先）
    # 注意：cuda_version 已是 cu-tag（detect_cuda_version 输出，如 "cu130"），
    # 直接用于构造 paddle index URL，不要再查 CUDA_VERSION_MAP（那是原始版本→cu-tag 的映射）。
    default_gpu_tag = "cu130"
    if use_gpu and cuda_version:
        paddle_package = paddle_gpu_spec
        paddle_index = f"https://www.paddlepaddle.org.cn/packages/stable/{cuda_version}/"
        paddle_name = f"PaddlePaddle GPU ({cuda_version})"
        report_fn("依赖安装", f"检测到 CUDA {cuda_version}，安装 GPU 版本")
    elif use_gpu:
        paddle_package = paddle_gpu_spec
        paddle_index = f"https://www.paddlepaddle.org.cn/packages/stable/{default_gpu_tag}/"
        paddle_name = f"PaddlePaddle GPU ({default_gpu_tag})"
        report_fn("依赖安装", f"安装 GPU 版本（默认 {default_gpu_tag}）")
    else:
        paddle_package = paddle_cpu_spec
        paddle_index = "https://www.paddlepaddle.org.cn/packages/stable/cpu/"
        paddle_name = "PaddlePaddle CPU"
        report_fn("依赖安装", "使用CPU版本")

    try:
        # 升级pip
        report_fn("依赖安装", "正在升级pip...")
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", "--upgrade", "pip", "-i", pip_source],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            report_fn(
                "依赖安装",
                f"pip升级警告: {result.stderr[-100:] if result.stderr else ''}",
            )

        requirements = list(extra_requirements)
        requirements.extend([
            (paddle_name, paddle_package, paddle_index),
            ("PaddleOCR", f'"{specs["paddleocr"]}"', pip_source),
            ("MinerU", f'"{specs["mineru"]}"', pip_source),
        ])

        # GPU 环境下安装 torch+CUDA 覆盖 mineru 附带的 CPU 版本
        if use_gpu:
            paddle_cuda_tag = cuda_version or default_gpu_tag
            torch_cuda_tag = TORCH_CUDA_MAP.get(paddle_cuda_tag, "cu128")
            pytorch_mirror_name = "nju" if network_type == "domestic" else "official"
            torch_index = get_pytorch_mirror(pytorch_mirror_name, torch_cuda_tag)
            requirements.append(
                (f"PyTorch CUDA ({torch_cuda_tag})", "torch torchvision", torch_index)
            )
            report_fn("依赖安装", f"将安装 PyTorch CUDA ({torch_cuda_tag})")

            # cu13 nvidia 运行时库：paddle GPU wheel 不内嵌 CUDA DLL（cublas/cudnn 等），
            # 全靠外部 nvidia 包提供；而 pip/uv 无法从 paddle wheel 的依赖声明自动解析出
            # cu13 系列（总匹配到 -cu12 后缀包）。必须显式安装，版本与 pyproject 声明一致。
            # 从 specs 字典读取（_load_dep_specs 已解析自 pyproject），避免硬编码版本。
            nvidia_keys = [
                "nvidia-cuda-runtime",
                "nvidia-cudnn-cu13",
                "nvidia-cublas",
                "nvidia-cufft",
                "nvidia-curand",
                "nvidia-cusolver",
                "nvidia-cusparse",
            ]
            nvidia_specs = [specs[k] for k in nvidia_keys if k in specs]
            if nvidia_specs:
                nvidia_pkg = " ".join(nvidia_specs)
                requirements.append(
                    ("NVIDIA cu13 运行时库", nvidia_pkg, pip_source)
                )
                report_fn("依赖安装", f"将安装 {len(nvidia_specs)} 个 NVIDIA cu13 运行时库")

        for name, package_spec, index_url in requirements:
            report_fn("依赖安装", f"正在安装 {name}...")
            report_fn("依赖安装", f"包规格: {package_spec}")
            report_fn("依赖安装", f"使用源: {index_url}")

            # package_spec 可能含多个包（空格分隔，如 "torch torchvision" 或 7 个 nvidia 包），
            # 必须拆成独立的 argv 元素传给 pip，否则 pip 把整个字符串当成一个非法 requirement。
            # 同时剥离冗余的引号（subprocess 传 list 不经过 shell，引号会变成参数的一部分）。
            raw_args = package_spec.split() if isinstance(package_spec, str) else list(package_spec)
            pkg_args = [a.strip('"').strip("'") for a in raw_args]

            result = subprocess.run(
                [str(python_exe), "-m", "pip", "install", *pkg_args, "-i", index_url],
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "未知错误"
                if "Could not find a version" in str(
                    error_msg
                ) or "No matching distribution" in str(error_msg):
                    report_fn("依赖安装", f"{name} 安装失败，尝试使用官方PyPI源...")
                    result = subprocess.run(
                        [str(python_exe), "-m", "pip", "install", *pkg_args],
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )

                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout or "未知错误"
                    return False, f"{name} 安装失败:\n{error_msg[:500]}"

            report_fn("依赖安装", f"{name} 安装成功")

        report_fn("依赖安装", "所有OCR依赖安装完成")
        return True, success_msg

    except subprocess.TimeoutExpired:
        return False, "依赖安装超时（10分钟）"
    except Exception as e:
        return False, f"依赖安装异常: {e}"


def install_embedded_dependencies(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    use_gpu: bool = False,
    cuda_version: str | None = None,
    progress_callback=None,
    force_backend: str | None = None,
) -> tuple[bool, str]:
    """
    仅安装嵌入式OCR依赖（PaddlePaddle GPU/CPU, PaddleX, MinerU）

    不安装生产依赖（PySide6, Pillow）

    Args:
        project_root: 项目根目录
        network_type: 网络类型
        use_gpu: 是否安装 GPU 版本（优先），False 则安装 CPU 版
        cuda_version: CUDA 版本 cu-tag（如 "cu130"），用于选择对应的 GPU 包
        progress_callback: 进度回调函数，接收 (stage, message) 参数
        force_backend: 强制后端 "gpu" / "cpu" / None。指定时覆盖 use_gpu/cuda_version，
            用于首启让用户选择或设置页切换。None 时走自动检测逻辑。

    Returns:
        (是否成功, 消息)
    """
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return False, "Python 运行时未安装"

    # force_backend 覆盖自动检测结果
    if force_backend == "gpu":
        use_gpu = True
        if not cuda_version:
            _has_gpu, cuda_version = detect_gpu()
    elif force_backend == "cpu":
        use_gpu = False
        cuda_version = None

    pip_source = get_pip_source(network_type)

    def report(stage: str, msg: str):
        print(f"[{stage}] {msg}")
        if progress_callback:
            progress_callback(stage, msg)

    report("依赖安装", "开始安装OCR依赖...")
    report("依赖安装", f"pip源: {pip_source}")

    specs = _load_dep_specs()
    # 嵌入式模式不装 PySide6/Pillow，extra_requirements 为空
    return _install_paddle_stack(
        python_exe=python_exe,
        specs=specs,
        pip_source=pip_source,
        network_type=network_type,
        use_gpu=use_gpu,
        cuda_version=cuda_version,
        report_fn=report,
        extra_requirements=[],
        success_msg="OCR依赖安装成功",
    )


def detect_cuda_version() -> str | None:
    """
    检测系统CUDA版本

    Returns:
        CUDA版本字符串（如 "cu129"），如果未检测到则返回 None
    """
    # CUDA版本映射到PaddlePaddle支持的版本
    # PaddlePaddle GPU版本支持: cu118, cu121, cu123, cu126, cu129 等
    cuda_version_map = CUDA_VERSION_MAP

    def find_best_match(major_minor: str) -> str | None:
        """查找最匹配的 PaddlePaddle CUDA 版本"""
        if major_minor in cuda_version_map:
            return cuda_version_map[major_minor]
        # 尝试找到最接近的版本（向下兼容）
        try:
            version_float = float(major_minor)
            best_match = None
            for supported_ver, paddle_tag in cuda_version_map.items():
                if float(supported_ver) <= version_float:
                    best_match = paddle_tag
            return best_match
        except ValueError:
            return None

    try:
        # 方法1: 解析 nvidia-smi 输出获取 CUDA 版本
        # 注意: nvidia-smi 的 --query-gpu 不支持 cuda_version 字段
        # 需要解析 nvidia-smi 的表头输出
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0 and result.stdout:
            # 在输出中查找 "CUDA Version: X.Y"
            import re

            match = re.search(r"CUDA Version:\s*(\d+\.\d+)", result.stdout)
            if match:
                cuda_version = match.group(1)
                print(f"[硬件检测] CUDA版本 (nvidia-smi): {cuda_version}")

                major_minor = ".".join(cuda_version.split(".")[:2])
                paddle_cuda = find_best_match(major_minor)
                if paddle_cuda:
                    print(f"[硬件检测] 对应PaddlePaddle CUDA版本: {paddle_cuda}")
                    return paddle_cuda

    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        print(f"[硬件检测] nvidia-smi检测失败: {e}")

    try:
        # 方法2: 检查nvcc版本
        result = subprocess.run(
            ["nvcc", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            # 输出包含: "release 12.6"
            import re

            match = re.search(r"release\s+(\d+\.\d+)", result.stdout)
            if match:
                cuda_version = match.group(1)
                print(f"[硬件检测] CUDA版本 (nvcc): {cuda_version}")

                paddle_cuda = find_best_match(cuda_version)
                if paddle_cuda:
                    print(f"[硬件检测] 对应PaddlePaddle CUDA版本: {paddle_cuda}")
                    return paddle_cuda

    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    print("[硬件检测] 无法检测CUDA版本")
    return None


def detect_gpu() -> tuple[bool, str | None]:
    """
    检测系统是否有可用的NVIDIA GPU及CUDA版本

    Returns:
        (是否有GPU, CUDA版本标识如"cu129"或None)
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"[硬件检测] 检测到GPU: {result.stdout.strip()}")
            cuda_version = detect_cuda_version()
            return True, cuda_version
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    print("[硬件检测] 未检测到NVIDIA GPU，将使用CPU版本")
    return False, None


def resolve_use_gpu(project_root: Path) -> bool:
    """决定运行时是否使用 GPU（缓存优先 + 探测回退）

    避免在无 GPU 的机器上硬编码 use_gpu=True，让 OCR worker 以 CPU 模式启动。

    策略：
    1. 读 machine_cache 的 hardware_info.has_gpu（依赖检测时由
       check_embedded_environment_dependencies 写入）
    2. 缓存有效且含 has_gpu → 直接返回
    3. 缓存缺失/失效/无 hardware_info → 回退 detect_gpu() 实时探测

    Args:
        project_root: 项目根目录

    Returns:
        是否使用 GPU
    """
    is_valid, cached_data = is_cache_valid(project_root)
    if is_valid and cached_data:
        # 优先级 1：用户在设置页选择的待生效后端
        pending = cached_data.get("pending_backend")
        if pending == "gpu":
            return True
        if pending == "cpu":
            return False
        # 优先级 2：依赖检测时写入的硬件信息
        hardware_info = cached_data.get("hardware_info") or {}
        if "has_gpu" in hardware_info:
            return bool(hardware_info["has_gpu"])

    # 缓存不可用，实时探测
    has_gpu, _cuda_version = detect_gpu()
    return has_gpu


# NVIDIA cu13 运行时包名（与 _install_paddle_stack 的 nvidia_keys 一致）
_NVIDIA_CU13_PACKAGES = [
    "nvidia-cuda-runtime",
    "nvidia-cudnn-cu13",
    "nvidia-cublas",
    "nvidia-cufft",
    "nvidia-curand",
    "nvidia-cusolver",
    "nvidia-cusparse",
]


def switch_paddle_backend(
    project_root: Path,
    target: str,
    network_type: Literal["domestic", "international"] = "domestic",
    progress_callback=None,
) -> tuple[bool, str]:
    """切换 PaddlePaddle 后端（GPU ↔ CPU）

    供设置页调用：卸载当前 paddle（两包名都卸防冲突）→ 安装目标后端 →
    写入 pending_backend 到缓存（下次启动 worker 时生效）。

    paddlepaddle 和 paddlepaddle-gpu 不能共存（都装 paddle 模块），必须先卸两者。
    GPU→CPU 时额外卸 7 个 nvidia cu13 包（回收 ~1GB）。

    Args:
        project_root: 项目根目录
        target: "gpu" 或 "cpu"
        network_type: 网络类型
        progress_callback: 进度回调 (stage, message)

    Returns:
        (是否成功, 消息)
    """
    if target not in ("gpu", "cpu"):
        return False, f"无效的后端目标: {target}（应为 'gpu' 或 'cpu'）"

    python_exe = get_embedded_python_executable(project_root)
    if not python_exe.exists():
        return False, "Python 运行时未安装"

    def report(stage: str, msg: str):
        print(f"[{stage}] {msg}")
        if progress_callback:
            progress_callback(stage, msg)

    report("后端切换", f"开始切换到 {target.upper()} 后端...")

    try:
        # 1. 卸载 paddle（两包名都卸，防冲突）
        report("后端切换", "卸载现有 PaddlePaddle...")
        uninstall_cmd = [
            str(python_exe),
            "-m",
            "pip",
            "uninstall",
            "-y",
            "paddlepaddle",
            "paddlepaddle-gpu",
        ]
        subprocess.run(
            uninstall_cmd,
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # uninstall 即使包不存在也返回 0，无需检查 returncode

        # 2. GPU→CPU 时额外卸载 nvidia cu13 包
        if target == "cpu":
            report("后端切换", "卸载 NVIDIA cu13 运行时库...")
            nv_uninstall_cmd = [
                str(python_exe),
                "-m",
                "pip",
                "uninstall",
                "-y",
                *_NVIDIA_CU13_PACKAGES,
            ]
            subprocess.run(
                nv_uninstall_cmd,
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        # 3. 安装目标后端（复用 install_embedded_dependencies 的 force_backend）
        report("后端切换", f"安装 {target.upper()} 版 PaddlePaddle...")
        success, msg = install_embedded_dependencies(
            project_root,
            network_type=network_type,
            progress_callback=progress_callback,
            force_backend=target,
        )
        if not success:
            return False, f"{target.upper()} 安装失败: {msg}"

        # 4. 写入 pending_backend（下次启动 worker 时 resolve_use_gpu 读取）
        if not update_cache_field(project_root, "pending_backend", target):
            report("后端切换", "警告: 缓存更新失败，切换可能不会在重启后生效")

        report("后端切换", f"已切换到 {target.upper()}，重启后生效")
        return True, f"已切换到 {target.upper()} 后端，重启应用后生效"

    except subprocess.TimeoutExpired:
        return False, "后端切换超时"
    except Exception as e:
        return False, f"后端切换异常: {e}"


def install_dependencies(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    use_gpu: bool = False,
    cuda_version: str | None = None,
    force_backend: str | None = None,
) -> tuple[bool, str]:
    """
    安装项目依赖到 Python 运行时

    Args:
        project_root: 项目根目录
        network_type: 网络类型
        use_gpu: 是否安装 GPU 版本（优先），False 则安装 CPU 版
        cuda_version: CUDA 版本 cu-tag（如 "cu130"），用于选择对应的 GPU 包
        force_backend: 强制后端 "gpu" / "cpu" / None。指定时覆盖 use_gpu/cuda_version。

    Returns:
        (是否成功, 消息)
    """
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return False, "Python 运行时未安装"

    # force_backend 覆盖自动检测结果
    if force_backend == "gpu":
        use_gpu = True
        if not cuda_version:
            _has_gpu, cuda_version = detect_gpu()
    elif force_backend == "cpu":
        use_gpu = False
        cuda_version = None

    pip_source = get_pip_source(network_type)

    print("\n" + "=" * 50)
    print("[依赖安装] 安装项目依赖")
    print("=" * 50)
    print(f"[依赖安装] pip源: {pip_source}")

    specs = _load_dep_specs()

    def report(stage: str, msg: str):
        print(f"[{stage}] {msg}")

    # 完整安装含生产依赖 PySide6/Pillow
    extra_requirements = [
        ("PySide6", specs["pyside6"], pip_source),
        ("Pillow", specs["pillow"], pip_source),
    ]
    return _install_paddle_stack(
        python_exe=python_exe,
        specs=specs,
        pip_source=pip_source,
        network_type=network_type,
        use_gpu=use_gpu,
        cuda_version=cuda_version,
        report_fn=report,
        extra_requirements=extra_requirements,
        success_msg="依赖安装成功",
    )


def setup_environment(project_root: Path) -> tuple[bool, str]:
    """
    完整的环境设置流程

    支持两种模式:
    1. 虚拟环境模式 (.venv): 开发调试使用,需要预先创建
    2. 便携式模式 (python/): 便携部署使用,自动下载安装

    Args:
        project_root: 项目根目录

    Returns:
        (是否成功, 消息)
    """
    print("\n" + "=" * 60)
    print("VibeOCR - 首次运行，正在配置环境")
    print("=" * 60)

    mode = get_environment_mode(project_root)
    if mode == "venv":
        print("[环境设置] 检测到虚拟环境模式")
        print("\n这将自动完成以下步骤:")
        print("1. 检测GPU并选择合适的PaddlePaddle版本")
        print("2. 安装所有项目依赖到虚拟环境")
    else:
        print("[环境设置] 使用便携式部署模式")
        print("\n这将自动完成以下步骤:")
        print(f"1. 下载并安装 Python {PYTHON_VERSION_SHORT}（python-build-standalone）")
        print("2. 检测GPU并选择合适的PaddlePaddle版本")
        print("3. 安装所有项目依赖")

    print("\n整个过程可能需要几分钟，请耐心等待...")

    # 1. 检测网络环境
    network_type = detect_network_source()

    # 2. 安装/检查 Python 运行时
    success, msg = install_embedded_python(project_root, network_type)
    if not success:
        return False, f"安装 Python 运行时失败:\n{msg}"

    # 3. 检测GPU和CUDA版本
    has_gpu, cuda_version = detect_gpu()

    # 4. 安装依赖
    success, msg = install_dependencies(
        project_root, network_type, has_gpu, cuda_version
    )
    if not success:
        return False, f"安装依赖失败:\n{msg}"

    return True, "环境配置完成！"


def ensure_environment(
    project_root: Path, ask_user: bool = False
) -> tuple[bool, str, bool]:
    """
    确保环境就绪 - 统一的环境检查和安装入口

    这个函数封装了所有环境相关的逻辑:
    1. 检测当前运行环境
    2. 检查依赖是否完整
    3. 如需安装,自动检测网络/GPU并安装

    Args:
        project_root: 项目根目录
        ask_user: 是否需要用户确认(GUI场景)

    Returns:
        (是否就绪, 消息, 是否需要重启)
        - 如果需要重启,调用方应使用 Python 运行时重启
    """
    embedded_python = get_embedded_python_executable(project_root)

    # 情况1: 使用嵌入式Python运行
    current_python = Path(sys.executable).resolve()
    is_embedded = (
        embedded_python.exists() and current_python == embedded_python.resolve()
    )

    if is_embedded:
        print("[VibeOCR] 使用 Python 运行时环境")

        # 检查依赖
        deps_status = check_dependencies(project_root)
        missing_deps = [pkg for pkg, installed in deps_status.items() if not installed]

        if not missing_deps:
            return True, "依赖完整，可以启动应用", False

        # 依赖缺失,自动安装
        print(f"[VibeOCR] 检测到缺失依赖: {', '.join(missing_deps)}")
        print("[VibeOCR] 正在自动安装依赖...")

        network_type = detect_network_source()
        has_gpu, cuda_version = detect_gpu()

        success, msg = install_dependencies(
            project_root, network_type, has_gpu, cuda_version
        )
        if not success:
            return False, f"依赖安装失败: {msg}", False

        print("[VibeOCR] 依赖安装完成")
        return True, "依赖安装完成，可以启动应用", False

    # 情况2: Python 运行时存在但使用其他Python运行(开发模式)
    if embedded_python.exists():
        print("[VibeOCR] 检测到 Python 运行时环境")

        # 检查依赖
        deps_status = check_dependencies(project_root)
        missing_deps = [pkg for pkg, installed in deps_status.items() if not installed]

        if not missing_deps:
            print("[VibeOCR] 依赖完整，建议使用 Python 运行时运行")
            return True, "依赖完整，但建议使用 Python 运行时运行", True

        # 依赖缺失
        msg = f"Python 运行时缺少依赖: {', '.join(missing_deps)}"

        if ask_user:
            # 返回信息让调用方处理用户交互
            return False, msg, False
        # 自动安装
        print(f"[VibeOCR] {msg}")
        print("[VibeOCR] 正在自动安装依赖...")

        network_type = detect_network_source()
        has_gpu, cuda_version = detect_gpu()

        success, msg = install_dependencies(
            project_root, network_type, has_gpu, cuda_version
        )
        if not success:
            return False, f"依赖安装失败: {msg}", False

        print("[VibeOCR] 依赖安装完成")
        return True, "依赖安装完成，请使用 Python 运行时运行", True

    # 情况3: 首次运行,无 Python 运行时
    print("[VibeOCR] 未检测到 Python 运行时环境")

    if ask_user:
        # 返回信息让调用方处理用户交互
        return False, "首次运行，需要安装 Python 运行时和依赖", False
    # 自动安装
    success, msg = setup_environment(project_root)
    if not success:
        return False, f"环境设置失败: {msg}", False

    return True, "环境设置完成，请使用嵌入式Python运行", True


def get_project_root() -> Path:
    """获取项目根目录"""
    # 从当前文件向上查找项目根目录（包含src目录）
    current = Path(__file__).resolve()
    while current.parent != current:
        if (current / "src" / "vibeocr").exists():
            return current
        current = current.parent
    # 默认返回main.py的父目录的父目录
    return Path(__file__).parent.parent.parent


def ensure_mineru_models(
    project_root: Path,
    timeout: int = 600,
) -> tuple[bool, str]:
    """下载 MinerU 所需模型（首次运行时调用）

    Args:
        project_root: 项目根目录
        timeout: 超时时间（秒）

    Returns:
        (是否成功, 消息)
    """
    python_exe = get_embedded_python_executable(project_root)
    if not python_exe.exists():
        return False, "Python 未安装"

    print("[模型下载] 正在下载 MinerU 模型...")
    try:
        network = detect_network_source()
        source = "modelscope" if network == "domestic" else "huggingface"
        print(f"[模型下载] 使用模型源: {source}")
        result = subprocess.run(
            [str(python_exe), "-m", "mineru.cli.models_download", "-s", source],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            return True, "MinerU 模型下载完成"
        return False, f"模型下载失败: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "模型下载超时"
    except Exception as e:
        return False, f"模型下载异常: {e}"
