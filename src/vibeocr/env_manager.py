"""环境管理模块：负责自动部署嵌入式Python和管理项目依赖"""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen
from typing import Literal, Optional


# 嵌入式Python版本
PYTHON_VERSION = "3.12.8"
PYTHON_VERSION_SHORT = "3.12"

# 定义pip下载源
MIRROR_SOURCES = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "ustc": "https://mirrors.ustc.edu.cn/pypi/web/simple",
    "official": "https://pypi.org/simple",
}

# 测试用的URL
TEST_URLS = {
    "google": "https://www.google.com",
    "github": "https://www.github.com",
    "baidu": "https://www.baidu.com",
}

# 嵌入式Python下载URL
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# 国内镜像源（如果官方源无法访问）
PYTHON_MIRROR_URLS = [
    "https://mirrors.huaweicloud.com/python/",
    "https://repo.huaweicloud.com/python/",
]

# PaddlePaddle 版本
PADDLE_VERSION = "3.3.0"

# CUDA 版本映射到 PaddlePaddle 支持的版本
# PaddlePaddle GPU 版本支持: cu118, cu121, cu123, cu126, cu129 等
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
}

# cuDNN 版本映射（基于 CUDA 版本）
# paddlepaddle-gpu 需要系统级 cuDNN 运行时库
# CUDA 11.x -> cuDNN 8.x, CUDA 12.x -> cuDNN 9.x
# nvidia-cudnn-cu11 是 CUDA 11.x 的元包
# nvidia-cudnn-cu12 是 CUDA 12.x 的元包
CUDNN_PACKAGE_MAP = {
    "cu118": "nvidia-cudnn-cu11",  # CUDA 11.8 -> cuDNN 8.x (元包会安装最新兼容版本)
    "cu121": "nvidia-cudnn-cu12",  # CUDA 12.1 -> cuDNN 9.x (元包会安装最新兼容版本)
    "cu123": "nvidia-cudnn-cu12",  # CUDA 12.3 -> cuDNN 9.x
    "cu126": "nvidia-cudnn-cu12",  # CUDA 12.6 -> cuDNN 9.x
    "cu129": "nvidia-cudnn-cu12",  # CUDA 12.9 -> cuDNN 9.x
}

# nvidia-cudnn-cu11 和 nvidia-cudnn-cu12 会自动安装以下依赖：
# - nvidia-cudnn-cu11/nvidia-cudnn-cu12: 主包
# - nvidia-cufft-cu11/nvidia-cufft-cu12: FFT 库
# - nvidia-cublas-cu11/nvidia-cublas-cu12: BLAS 库
# - nvidia-curand-cu11/nvidia-curand-cu12: 随机数库
# - nvidia-cusolver-cu11/nvidia-cusolver-cu12: 求解器库
# - nvidia-cusparse-cu11/nvidia-cusparse-cu12: 稀疏矩阵库


def ping_url(url: str, timeout: int = 3) -> bool:
    """测试URL是否可访问"""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def detect_network_source() -> Literal["domestic", "international"]:
    """检测网络环境，选择合适的下载源"""
    print("[环境检测] 正在检测网络环境...")

    # 先测试国内网站
    if ping_url(TEST_URLS["baidu"], timeout=2):
        print("[环境检测] 国内网络可访问")
        # 如果能访问Google，说明是国际网络
        if ping_url(TEST_URLS["google"], timeout=3):
            print("[环境检测] 国际网络也可访问，使用官方源")
            return "international"
        else:
            print("[环境检测] 使用国内镜像源")
            return "domestic"

    # 如果国内网站都访问不了，尝试国际网站
    if ping_url(TEST_URLS["github"], timeout=5):
        print("[环境检测] 国际网络可访问，使用官方源")
        return "international"

    print("[环境检测] 网络环境未知，默认使用国内镜像源")
    return "domestic"


def get_pip_source(network_type: Literal["domestic", "international"] = "domestic") -> str:
    """根据网络类型获取pip下载源"""
    if network_type == "domestic":
        # 按优先级返回国内源
        for source_name, source_url in MIRROR_SOURCES.items():
            if source_name != "official":
                if ping_url(source_url.replace("/simple", ""), timeout=3):
                    print(f"[环境检测] 选择镜像源: {source_name}")
                    return source_url
        # 如果都测试失败，默认使用清华源
        print("[环境检测] 使用默认清华镜像源")
        return MIRROR_SOURCES["tsinghua"]
    else:
        print("[环境检测] 使用官方PyPI源")
        return MIRROR_SOURCES["official"]


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
    venv_python = project_root / ".venv" / "Scripts" / "python.exe" if os.name == "nt" else project_root / ".venv" / "bin" / "python"
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


def is_embedded_python_installed(project_root: Path) -> bool:
    """检查嵌入式Python是否已安装(支持虚拟环境和便携式两种模式)"""
    mode = get_environment_mode(project_root)
    if mode == "none":
        return False
    python_exe = get_embedded_python_executable(project_root)
    return python_exe.exists()


def download_file_with_progress(url: str, dest_path: Path, description: str = "下载") -> bool:
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
                        print(f"\r[{description}] 进度: {progress}% ({downloaded // 1024 // 1024}MB / {total_size // 1024 // 1024}MB)", end="")

        print(f"\r[{description}] 下载完成")
        return True

    except Exception as e:
        print(f"\r[{description}] 下载失败: {e}")
        return False


def install_embedded_python(project_root: Path, network_type: Literal["domestic", "international"] = "domestic") -> tuple[bool, str]:
    """
    下载并安装嵌入式Python

    Args:
        project_root: 项目根目录
        network_type: 网络类型

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
        else:
            return False, "虚拟环境不完整,请重新创建"

    python_dir = project_root / "python"

    if python_dir.exists():
        return True, f"嵌入式Python已安装: {python_dir}"

    print("\n" + "=" * 50)
    print("[环境安装] 安装嵌入式Python")
    print("=" * 50)

    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        zip_path = temp_path / "python_embed.zip"

        # 尝试下载嵌入式Python
        python_url = PYTHON_EMBED_URL
        download_ok = download_file_with_progress(python_url, zip_path, "Python")

        # 如果官方源下载失败，尝试国内镜像
        if not download_ok and network_type == "domestic":
            print("[环境安装] 官方源下载失败，尝试国内镜像...")
            for mirror_url in PYTHON_MIRROR_URLS:
                mirror_python_url = f"{mirror_url}{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
                if download_file_with_progress(mirror_python_url, zip_path, "Python(镜像)"):
                    download_ok = True
                    break

        if not download_ok:
            return False, f"无法下载嵌入式Python，请手动下载:\n{python_url}\n并解压到: {python_dir}"

        # 解压文件
        print("[环境安装] 正在解压Python文件...")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(python_dir)
            print("[环境安装] 解压完成")
        except Exception as e:
            return False, f"解压失败: {e}"

    # 配置嵌入式Python
    pth_file = python_dir / f"python{PYTHON_VERSION_SHORT.replace('.', '')}._pth"
    if pth_file.exists():
        print("[环境安装] 配置Python路径...")
        with open(pth_file, "w", encoding="utf-8") as f:
            f.write(f"python{PYTHON_VERSION_SHORT}.zip\n")
            f.write(".\n")
            f.write("Lib\n")
            f.write("Lib\\site-packages\n")
            f.write("import site\n")
        print("[环境安装] 路径配置完成")

    # 创建Lib目录
    lib_dir = python_dir / "Lib" / "site-packages"
    lib_dir.mkdir(parents=True, exist_ok=True)

    # 下载并安装pip
    print("[环境安装] 正在安装pip...")
    get_pip_path = temp_path / "get-pip.py"

    if download_file_with_progress(GET_PIP_URL, get_pip_path, "get-pip.py"):
        python_exe = get_embedded_python_executable(project_root)
        try:
            result = subprocess.run(
                [str(python_exe), str(get_pip_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                print("[环境安装] pip安装成功")
            else:
                print(f"[环境安装] pip安装警告: {result.stderr[-200:] if result.stderr else ''}")
        except subprocess.TimeoutExpired:
            return False, "pip安装超时"
        except Exception as e:
            return False, f"pip安装失败: {e}"
    else:
        return False, "无法下载get-pip.py"

    return True, f"嵌入式Python安装成功: {python_exe}"


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

    for pkg in dependencies.keys():
        try:
            __import__(pkg)
            dependencies[pkg] = True
        except ImportError:
            dependencies[pkg] = False

    return dependencies


def check_embedded_environment_dependencies(project_root: Path) -> dict[str, bool]:
    """检查嵌入式Python环境的OCR依赖是否已安装

    用于检测 PaddlePaddle, PaddleX 等OCR功能依赖

    Args:
        project_root: 项目根目录

    Returns:
        依赖状态字典
    """
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return {}

    dependencies = {
        "paddlepaddle": False,
        "paddlex": False,
    }

    for pkg in dependencies.keys():
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


def check_dependencies(project_root: Path) -> dict[str, bool]:
    """检查嵌入式Python的依赖是否已安装

    注意：此函数检测的是嵌入式环境的依赖，包括生产依赖和OCR依赖。
    对于生产环境依赖检测，请使用 check_current_environment_dependencies()
    对于仅检测OCR依赖，请使用 check_embedded_environment_dependencies()
    """
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return {}

    dependencies = {
        "PySide6": False,
        "paddlepaddle": False,
        "paddlex": False,
        "PIL": False,
    }

    for pkg in dependencies.keys():
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


def is_embedded_environment_ready(project_root: Path) -> tuple[bool, list[str]]:
    """检查嵌入式OCR环境是否就绪

    Args:
        project_root: 项目根目录

    Returns:
        (是否就绪, 缺失的依赖列表)
    """
    # 首先检查嵌入式Python是否存在
    python_exe = get_embedded_python_executable(project_root)
    if not python_exe.exists():
        return False, ["嵌入式Python未安装"]

    deps = check_embedded_environment_dependencies(project_root)
    missing = [pkg for pkg, installed in deps.items() if not installed]
    return len(missing) == 0, missing


def install_embedded_dependencies(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    use_gpu: bool = True,
    cuda_version: str | None = None,
    progress_callback=None,
) -> tuple[bool, str]:
    """
    仅安装嵌入式OCR依赖（PaddlePaddle, PaddleX）

    不安装生产依赖（PySide6, Pillow）

    Args:
        project_root: 项目根目录
        network_type: 网络类型
        use_gpu: 是否安装GPU版本
        cuda_version: CUDA版本标识（如 "cu129"）
        progress_callback: 进度回调函数，接收 (stage, message) 参数

    Returns:
        (是否成功, 消息)
    """
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return False, "嵌入式Python未安装"

    pip_source = get_pip_source(network_type)

    def report(stage: str, msg: str):
        print(f"[{stage}] {msg}")
        if progress_callback:
            progress_callback(stage, msg)

    report("依赖安装", "开始安装OCR依赖...")
    report("依赖安装", f"pip源: {pip_source}")
    report("依赖安装", f"GPU版本: {'是' if use_gpu else '否'}")
    if cuda_version:
        report("依赖安装", f"CUDA版本: {cuda_version}")

    try:
        # 升级pip
        report("依赖安装", "正在升级pip...")
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", "--upgrade", "pip", "-i", pip_source],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            report("依赖安装", f"pip升级警告: {result.stderr[-100:] if result.stderr else ''}")

        # 选择PaddlePaddle版本和安装源
        requirements = []
        paddle_version = "3.3.0"
        cu_dnn_package = None

        if use_gpu and cuda_version:
            paddle_package = f"paddlepaddle-gpu=={paddle_version}"
            paddle_index = f"https://www.paddlepaddle.org.cn/packages/stable/{cuda_version}/"
            requirements.append(("PaddlePaddle GPU", paddle_package, paddle_index))
            report("依赖安装", f"PaddlePaddle GPU版本将使用专用源: {paddle_index}")

            # 添加 cuDNN 依赖
            if cuda_version in CUDNN_PACKAGE_MAP:
                cu_dnn_package = CUDNN_PACKAGE_MAP[cuda_version]
                # cuDNN 包优先使用国内镜像（如果网络环境是国内）
                # 如果国内镜像不可用，安装代码会自动回退到官方 PyPI 源
                requirements.append(("cuDNN", cu_dnn_package, pip_source))
                report("依赖安装", f"将安装 cuDNN 运行时库: {cu_dnn_package}")
        elif use_gpu:
            paddle_package = f"paddlepaddle-gpu=={paddle_version}"
            paddle_index = "https://www.paddlepaddle.org.cn/packages/stable/cu129/"
            requirements.append(("PaddlePaddle GPU", paddle_package, paddle_index))
            report("依赖安装", "使用默认CUDA 12.9版本的PaddlePaddle GPU")

            # 添加 cuDNN 依赖（CUDA 12.9 使用 cu12）
            cu_dnn_package = CUDNN_PACKAGE_MAP["cu129"]
            requirements.append(("cuDNN", cu_dnn_package, pip_source))
            report("依赖安装", f"将安装 cuDNN 运行时库: {cu_dnn_package}")
        else:
            paddle_package = f"paddlepaddle=={paddle_version}"
            paddle_index = "https://www.paddlepaddle.org.cn/packages/stable/cpu/"
            requirements.append(("PaddlePaddle CPU", paddle_package, paddle_index))
            report("依赖安装", "使用PaddlePaddle CPU版本")

        # 安装PaddleX (使用普通pip源)
        requirements.append(("PaddleX", '"paddlex[ocr]>=3.4.2"', pip_source))

        for name, package_spec, index_url in requirements:
            report("依赖安装", f"正在安装 {name}...")
            report("依赖安装", f"包规格: {package_spec}")
            report("依赖安装", f"使用源: {index_url}")

            result = subprocess.run(
                [str(python_exe), "-m", "pip", "install", package_spec, "-i", index_url],
                capture_output=True,
                text=True,
                timeout=600,  # PaddlePaddle安装可能需要较长时间
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "未知错误"
                if "Could not find a version" in str(error_msg) or "No matching distribution" in str(error_msg):
                    report("依赖安装", f"{name} 安装失败，尝试使用官方PyPI源...")
                    result = subprocess.run(
                        [str(python_exe), "-m", "pip", "install", package_spec],
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )

                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout or "未知错误"
                    return False, f"{name} 安装失败:\n{error_msg[:500]}"

            report("依赖安装", f"{name} 安装成功")

        report("依赖安装", "所有OCR依赖安装完成")
        return True, "OCR依赖安装成功"

    except subprocess.TimeoutExpired:
        return False, "依赖安装超时（10分钟）"
    except Exception as e:
        return False, f"依赖安装异常: {e}"


def detect_cuda_version() -> str | None:
    """
    检测系统CUDA版本

    Returns:
        CUDA版本字符串（如 "12.0"），如果未检测到则返回 None
    """
    # CUDA版本映射到PaddlePaddle支持的版本
    # PaddlePaddle GPU版本支持: cu118, cu121, cu123, cu126, cu129 等
    cuda_version_map = {
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
    }

    try:
        # 方法1: 使用nvidia-smi检测CUDA版本
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version,cuda_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            # 输出格式: "560.94, 12.6"
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                cuda_version = parts[1].strip()
                print(f"[硬件检测] CUDA版本 (nvidia-smi): {cuda_version}")

                # 解析主版本号
                major_minor = ".".join(cuda_version.split(".")[:2])
                if major_minor in cuda_version_map:
                    paddle_cuda = cuda_version_map[major_minor]
                    print(f"[硬件检测] 对应PaddlePaddle CUDA版本: {paddle_cuda}")
                    return paddle_cuda
                else:
                    # 尝试找到最接近的版本
                    version_float = float(major_minor)
                    best_match = None
                    for supported_ver, paddle_tag in cuda_version_map.items():
                        if float(supported_ver) <= version_float:
                            best_match = paddle_tag
                    if best_match:
                        print(f"[硬件检测] 使用兼容的PaddlePaddle CUDA版本: {best_match}")
                        return best_match

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

                major_minor = cuda_version
                if major_minor in cuda_version_map:
                    return cuda_version_map[major_minor]
                else:
                    version_float = float(major_minor)
                    best_match = None
                    for supported_ver, paddle_tag in cuda_version_map.items():
                        if float(supported_ver) <= version_float:
                            best_match = paddle_tag
                    if best_match:
                        print(f"[硬件检测] 使用兼容的PaddlePaddle CUDA版本: {best_match}")
                        return best_match

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


def install_dependencies(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    use_gpu: bool = True,
    cuda_version: str | None = None
) -> tuple[bool, str]:
    """
    安装项目依赖到嵌入式Python

    Args:
        project_root: 项目根目录
        network_type: 网络类型
        use_gpu: 是否安装GPU版本
        cuda_version: CUDA版本标识（如 "cu129"）

    Returns:
        (是否成功, 消息)
    """
    python_exe = get_embedded_python_executable(project_root)

    if not python_exe.exists():
        return False, "嵌入式Python未安装"

    pip_source = get_pip_source(network_type)

    print("\n" + "=" * 50)
    print("[依赖安装] 安装项目依赖")
    print("=" * 50)
    print(f"[依赖安装] pip源: {pip_source}")
    print(f"[依赖安装] GPU版本: {'是' if use_gpu else '否'}")
    if cuda_version:
        print(f"[依赖安装] CUDA版本: {cuda_version}")

    try:
        # 升级pip
        print("[依赖安装] 正在升级pip...")
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", "--upgrade", "pip", "-i", pip_source],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"[依赖安装] pip升级警告: {result.stderr[-100:] if result.stderr else ''}")

        # 安装基础依赖
        requirements = [
            ("PySide6", "pyside6>=6.8.0", pip_source),
            ("Pillow", "pillow>=11.0.0", pip_source),
        ]

        # 选择PaddlePaddle版本和安装源
        # PaddlePaddle需要使用官方专用源
        paddle_version = "3.3.0"
        if use_gpu and cuda_version:
            # GPU版本使用官方专用源
            paddle_package = f"paddlepaddle-gpu=={paddle_version}"
            paddle_index = f"https://www.paddlepaddle.org.cn/packages/stable/{cuda_version}/"
            requirements.append(("PaddlePaddle GPU", paddle_package, paddle_index))
            print(f"[依赖安装] PaddlePaddle GPU版本将使用专用源: {paddle_index}")

            # 添加 cuDNN 依赖
            if cuda_version in CUDNN_PACKAGE_MAP:
                cu_dnn_package = CUDNN_PACKAGE_MAP[cuda_version]
                requirements.append(("cuDNN", cu_dnn_package, pip_source))
                print(f"[依赖安装] 将安装 cuDNN 运行时库: {cu_dnn_package}")
        elif use_gpu:
            # 有GPU但无法检测CUDA版本，使用默认GPU版本
            paddle_package = f"paddlepaddle-gpu=={paddle_version}"
            paddle_index = "https://www.paddlepaddle.org.cn/packages/stable/cu129/"
            requirements.append(("PaddlePaddle GPU", paddle_package, paddle_index))
            print(f"[依赖安装] 使用默认CUDA 12.9版本的PaddlePaddle GPU")

            # 添加 cuDNN 依赖（CUDA 12.9 使用 cu12）
            cu_dnn_package = CUDNN_PACKAGE_MAP["cu129"]
            requirements.append(("cuDNN", cu_dnn_package, pip_source))
            print(f"[依赖安装] 将安装 cuDNN 运行时库: {cu_dnn_package}")
        else:
            # CPU版本
            paddle_package = f"paddlepaddle=={paddle_version}"
            paddle_index = "https://www.paddlepaddle.org.cn/packages/stable/cpu/"
            requirements.append(("PaddlePaddle CPU", paddle_package, paddle_index))
            print("[依赖安装] 使用PaddlePaddle CPU版本")

        # 安装PaddleX (使用普通pip源)
        requirements.append(("PaddleX", '"paddlex[ocr]>=3.4.2"', pip_source))

        for name, package_spec, index_url in requirements:
            print(f"[依赖安装] 正在安装 {name}...")
            print(f"[依赖安装] 包规格: {package_spec}")
            print(f"[依赖安装] 使用源: {index_url}")

            result = subprocess.run(
                [str(python_exe), "-m", "pip", "install", package_spec, "-i", index_url],
                capture_output=True,
                text=True,
                timeout=600,  # PaddlePaddle安装可能需要较长时间
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "未知错误"
                # 检查是否是网络问题，尝试备用方案
                if "Could not find a version" in str(error_msg) or "No matching distribution" in str(error_msg):
                    print(f"[依赖安装] {name} 安装失败，尝试使用官方PyPI源...")
                    result = subprocess.run(
                        [str(python_exe), "-m", "pip", "install", package_spec],
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )

                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout or "未知错误"
                    return False, f"{name} 安装失败:\n{error_msg[:500]}"

            print(f"[依赖安装] {name} 安装成功")

        print("[依赖安装] 所有依赖安装完成")
        return True, "依赖安装成功"

    except subprocess.TimeoutExpired:
        return False, "依赖安装超时（10分钟）"
    except Exception as e:
        return False, f"依赖安装异常: {e}"


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
        print("1. 下载并安装嵌入式Python 3.12")
        print("2. 检测GPU并选择合适的PaddlePaddle版本")
        print("3. 安装所有项目依赖")

    print("\n整个过程可能需要几分钟，请耐心等待...")

    # 1. 检测网络环境
    network_type = detect_network_source()

    # 2. 安装/检查嵌入式Python
    success, msg = install_embedded_python(project_root, network_type)
    if not success:
        return False, f"安装嵌入式Python失败:\n{msg}"

    # 3. 检测GPU和CUDA版本
    has_gpu, cuda_version = detect_gpu()

    # 4. 安装依赖
    success, msg = install_dependencies(project_root, network_type, has_gpu, cuda_version)
    if not success:
        return False, f"安装依赖失败:\n{msg}"

    return True, "环境配置完成！"


def ensure_environment(project_root: Path, ask_user: bool = False) -> tuple[bool, str, bool]:
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
        - 如果需要重启,调用方应使用嵌入式Python重启
    """
    embedded_python = get_embedded_python_executable(project_root)

    # 情况1: 使用嵌入式Python运行
    current_python = Path(sys.executable).resolve()
    is_embedded = embedded_python.exists() and current_python == embedded_python.resolve()

    if is_embedded:
        print("[VibeOCR] 使用嵌入式Python环境")

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

        success, msg = install_dependencies(project_root, network_type, has_gpu, cuda_version)
        if not success:
            return False, f"依赖安装失败: {msg}", False

        print("[VibeOCR] 依赖安装完成")
        return True, "依赖安装完成，可以启动应用", False

    # 情况2: 嵌入式Python存在但使用其他Python运行(开发模式)
    if embedded_python.exists():
        print("[VibeOCR] 检测到嵌入式Python环境")

        # 检查依赖
        deps_status = check_dependencies(project_root)
        missing_deps = [pkg for pkg, installed in deps_status.items() if not installed]

        if not missing_deps:
            print("[VibeOCR] 依赖完整，建议使用嵌入式Python运行")
            return True, "依赖完整，但建议使用嵌入式Python运行", True

        # 依赖缺失
        msg = f"嵌入式Python缺少依赖: {', '.join(missing_deps)}"

        if ask_user:
            # 返回信息让调用方处理用户交互
            return False, msg, False
        else:
            # 自动安装
            print(f"[VibeOCR] {msg}")
            print("[VibeOCR] 正在自动安装依赖...")

            network_type = detect_network_source()
            has_gpu, cuda_version = detect_gpu()

            success, msg = install_dependencies(project_root, network_type, has_gpu, cuda_version)
            if not success:
                return False, f"依赖安装失败: {msg}", False

            print("[VibeOCR] 依赖安装完成")
            return True, "依赖安装完成，请使用嵌入式Python运行", True

    # 情况3: 首次运行,无嵌入式Python
    print("[VibeOCR] 未检测到嵌入式Python环境")

    if ask_user:
        # 返回信息让调用方处理用户交互
        return False, "首次运行，需要安装嵌入式Python和依赖", False
    else:
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


# ============================================================
# 便携式 Python 支持辅助函数
# ============================================================

def get_embedded_python(project_root: Optional[Path] = None) -> Path:
    """获取嵌入式 Python 可执行文件路径（便捷函数）

    优先使用虚拟环境 (.venv)，如果不存在则使用便携式 python/ 目录

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        嵌入式 Python 可执行文件路径
    """
    if project_root is None:
        project_root = get_project_root()
    return get_embedded_python_executable(project_root)


def get_embedded_venv_python(project_root: Optional[Path] = None) -> Path:
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
    else:
        return project_root / ".venv" / "bin" / "python"


def get_embedded_portable_python(project_root: Optional[Path] = None) -> Path:
    """获取便携式 Python 可执行文件路径

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        便携式 Python 可执行文件路径
    """
    if project_root is None:
        project_root = get_project_root()

    if os.name == "nt":  # Windows
        return project_root / "python" / "python.exe"
    else:
        return project_root / "python" / "bin" / "python"


def is_embedded_python_ready(project_root: Optional[Path] = None) -> bool:
    """检查嵌入式 Python 是否已准备好

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        是否准备好
    """
    if project_root is None:
        project_root = get_project_root()

    # 检查 Python 可执行文件
    python_exe = get_embedded_python(project_root)
    if not python_exe.exists():
        return False

    # 检查 PaddleX 依赖
    try:
        result = subprocess.run(
            [str(python_exe), "-c", "import paddlex"],
            capture_output=True,
            timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def get_embedded_python_info(project_root: Optional[Path] = None) -> dict:
    """获取嵌入式 Python 的详细信息

    Args:
        project_root: 项目根目录，如果为 None 则自动检测

    Returns:
        包含以下键的字典：
        - exists: 是否存在
        - path: Python 路径
        - version: Python 版本
        - has_paddle: 是否安装了 PaddlePaddle
        - has_paddlex: 是否安装了 PaddleX
        - mode: 环境模式（venv 或 portable）
    """
    if project_root is None:
        project_root = get_project_root()

    python_exe = get_embedded_python(project_root)
    info = {
        'exists': python_exe.exists(),
        'path': str(python_exe),
        'version': None,
        'has_paddle': False,
        'has_paddlex': False,
        'mode': 'unknown'
    }

    if not info['exists']:
        return info

    # 检测模式
    if '.venv' in str(python_exe):
        info['mode'] = 'venv'
    elif 'python' in str(python_exe).split(os.sep)[-2]:
        info['mode'] = 'portable'

    # 获取版本
    try:
        result = subprocess.run(
            [str(python_exe), "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            info['version'] = result.stdout.strip() or result.stderr.strip()
    except Exception:
        pass

    # 检查依赖
    for pkg in ['paddlepaddle', 'paddlex']:
        try:
            result = subprocess.run(
                [str(python_exe), "-c", f"import {pkg}"],
                capture_output=True,
                timeout=10
            )
            if pkg == 'paddlepaddle':
                info['has_paddle'] = result.returncode == 0
            else:
                info['has_paddlex'] = result.returncode == 0
        except Exception:
            pass

    return info
