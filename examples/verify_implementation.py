#!/usr/bin/env python
"""
子进程 OCR 实现验证脚本

验证各组件是否正确实现和配置。
"""

import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def print_section(title: str):
    """打印分节标题"""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def check_imports():
    """检查关键模块导入"""
    print_section("1. 检查模块导入")

    # 检查 workers 模块
    try:
        from vibeocr.workers import (
            OCRWorkerError,  # noqa: F401  # type: ignore[unused-import]
        )

        print("[OK] vibeocr.workers 模块可导入")
    except ImportError as e:
        print(f"[FAIL] vibeocr.workers 导入失败: {e}")
        return False

    # 检查 env_manager 新增函数
    try:
        from vibeocr.env_manager import (  # noqa: F401
            get_embedded_python,  # type: ignore[unused-import]
            get_embedded_python_info,  # type: ignore[unused-import]
            get_embedded_venv_python,  # type: ignore[unused-import]
            is_embedded_python_ready,  # type: ignore[unused-import]
        )

        print("[OK] env_manager 子进程辅助函数可导入")
    except ImportError as e:
        print(f"[FAIL] env_manager 函数导入失败: {e}")
        return False

    return True


def check_worker_script():
    """检查 worker 脚本"""
    print_section("2. 检查 Worker 脚本")

    worker_script = project_root / "src" / "vibeocr" / "workers" / "ocr_worker.py"

    if not worker_script.exists():
        print(f"[FAIL] Worker 脚本不存在: {worker_script}")
        return False

    print(f"[OK] Worker 脚本存在: {worker_script}")

    # 检查脚本内容
    with open(worker_script, encoding="utf-8") as f:
        content = f.read()

    required_elements = [
        "def run_worker",
        "class OCRPipeline",
        "class SharedMemoryProtocol",
        'if __name__ == "__main__"',
    ]

    for element in required_elements:
        if element in content:
            print(f"[OK] 包含 {element}")
        else:
            print(f"[FAIL] 缺少 {element}")
            return False

    return True


def check_subprocess_service():
    """检查子进程服务模块"""
    print_section("3. 检查子进程服务模块")

    service_file = (
        project_root / "src" / "vibeocr" / "services" / "ocr_service_subprocess.py"
    )

    if not service_file.exists():
        print(f"[FAIL] 子进程服务文件不存在: {service_file}")
        return False

    print(f"[OK] 子进程服务文件存在: {service_file}")

    # 检查关键类和函数
    with open(service_file, encoding="utf-8") as f:
        content = f.read()

    required_elements = [
        "class _SharedMemoryProtocol",
        "class _OCRWorker",
        "class OCRServiceSubprocess",
        "class OCRService",
        "def get_ocr_service",
    ]

    for element in required_elements:
        if element in content:
            print(f"[OK] 包含 {element}")
        else:
            print(f"[FAIL] 缺少 {element}")
            return False

    return True


def check_services_init():
    """检查 services 包初始化"""
    print_section("4. 检查 services 包初始化")

    init_file = project_root / "src" / "vibeocr" / "services" / "__init__.py"

    if not init_file.exists():
        print("[FAIL] services/__init__.py 不存在")
        return False

    with open(init_file, encoding="utf-8") as f:
        content = f.read()

    if "USE_SUBPROCESS_OCR" in content:
        print("[OK] services/__init__.py 包含子进程切换逻辑")
    else:
        print("[WARN] services/__init__.py 可能未更新")
        return False

    if "ocr_service_subprocess" in content:
        print("[OK] services/__init__.py 导入子进程服务")
    else:
        print("[FAIL] services/__init__.py 未导入子进程服务")
        return False

    return True


def check_env_manager():
    """检查环境管理器增强"""
    print_section("5. 检查环境管理器增强")

    try:
        from vibeocr.env_manager import (
            get_embedded_python,
            get_embedded_python_info,
            get_embedded_venv_python,
            is_embedded_python_ready,
        )
    except ImportError as e:
        print(f"[FAIL] 无法导入新增函数: {e}")
        return False

    print("[OK] 新增函数可导入")

    # 测试函数调用
    try:
        python_path = get_embedded_python()
        print(f"[OK] get_embedded_python() -> {python_path}")
    except Exception as e:
        print(f"[FAIL] get_embedded_python() 失败: {e}")
        return False

    try:
        venv_path = get_embedded_venv_python()
        print(f"[OK] get_embedded_venv_python() -> {venv_path}")
    except Exception as e:
        print(f"[FAIL] get_embedded_venv_python() 失败: {e}")
        return False

    try:
        ready = is_embedded_python_ready()
        print(f"[OK] is_embedded_python_ready() -> {ready}")
    except Exception as e:
        print(f"[FAIL] is_embedded_python_ready() 失败: {e}")
        return False

    try:
        info = get_embedded_python_info()
        print(f"[OK] get_embedded_python_info() -> {info}")
    except Exception as e:
        print(f"[FAIL] get_embedded_python_info() 失败: {e}")
        return False

    return True


def check_file_structure():
    """检查文件结构"""
    print_section("6. 检查文件结构")

    required_files = [
        "src/vibeocr/workers/__init__.py",
        "src/vibeocr/workers/ocr_worker.py",
        "src/vibeocr/services/ocr_service_subprocess.py",
        "tests/test_ocr_service_subprocess.py",
        "docs/subprocess_ocr_architecture.md",
    ]

    all_exist = True
    for file_path in required_files:
        full_path = project_root / file_path
        if full_path.exists():
            print(f"[OK] {file_path}")
        else:
            print(f"[FAIL] {file_path} 不存在")
            all_exist = False

    return all_exist


def check_documentation():
    """检查文档"""
    print_section("7. 检查文档")

    doc_file = project_root / "docs" / "subprocess_ocr_architecture.md"

    if not doc_file.exists():
        print(f"[WARN] 架构文档不存在: {doc_file}")
        return True  # 不影响核心功能

    print(f"[OK] 架构文档存在: {doc_file}")

    with open(doc_file, encoding="utf-8") as f:
        content = f.read()

    required_sections = [
        "问题背景",
        "架构设计",
        "核心实现",
        "使用方式",
        "打包配置",
    ]

    for section in required_sections:
        if section in content:
            print(f"[OK] 文档包含 '{section}' 章节")
        else:
            print(f"[WARN] 文档缺少 '{section}' 章节")

    return True


def main():
    """主检查流程"""
    print("\n" + "=" * 60)
    print("VibeOCR 子进程实现验证")
    print("=" * 60)

    checks = [
        ("模块导入", check_imports),
        ("Worker 脚本", check_worker_script),
        ("子进程服务模块", check_subprocess_service),
        ("Services 包初始化", check_services_init),
        ("环境管理器增强", check_env_manager),
        ("文件结构", check_file_structure),
        ("文档", check_documentation),
    ]

    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n[ERROR] {name} 检查时发生异常: {e}")
            import traceback

            traceback.print_exc()
            results.append((name, False))

    # 总结
    print_section("验证结果总结")

    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status} {name}")

    passed = sum(1 for _, r in results if r)
    total = len(results)

    print(f"\n通过: {passed}/{total}")

    if passed == total:
        print("\n[SUCCESS] 所有检查通过！子进程 OCR 实现已完成。")
        return 0
    print(f"\n[WARNING] {total - passed} 项检查未通过")
    return 1


if __name__ == "__main__":
    sys.exit(main())
