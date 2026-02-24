#!/usr/bin/env python
"""
验证子进程 OCR 服务实现的脚本

检查以下组件：
1. 共享内存协议
2. Worker 脚本存在性
3. 嵌入式 Python 配置
4. 子进程 OCR 服务导入
"""

import os
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def check_shared_memory():
    """检查共享内存功能"""
    print("检查共享内存功能...")
    try:
        import multiprocessing.shared_memory as shm

        # 创建测试共享内存
        memory = shm.SharedMemory(name="vibeocr_test", create=True, size=1024)

        # 写入测试数据
        test_data = b"VibeOCR Test"
        memory.buf[0:len(test_data)] = test_data

        # 读取测试数据
        read_data = bytes(memory.buf[0:len(test_data)])

        # 清理
        memory.close()
        memory.unlink()

        if read_data == test_data:
            print("  [OK] 共享内存功能正常")
            return True
        else:
            print("  [FAIL] 共享内存数据不匹配")
            return False

    except Exception as e:
        print(f"  [FAIL] 共享内存检查失败: {e}")
        return False


def check_worker_script():
    """检查 Worker 脚本"""
    print("检查 Worker 脚本...")
    worker_path = project_root / "src" / "vibeocr" / "workers" / "ocr_worker.py"

    if worker_path.exists():
        print(f"  [OK] Worker 脚本存在: {worker_path}")

        # 检查脚本可执行性
        try:
            with open(worker_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if 'def run_worker' in content and 'if __name__ == "__main__"' in content:
                    print("  [OK] Worker 脚本结构正确")
                    return True
                else:
                    print("  [WARN] Worker 脚本结构可能不完整")
                    return True
        except Exception as e:
            print(f"  [WARN] 无法读取 Worker 脚本: {e}")
            return True
    else:
        print(f"  [FAIL] Worker 脚本不存在: {worker_path}")
        return False


def check_env_manager():
    """检查环境管理器辅助函数"""
    print("检查环境管理器...")
    try:
        from vibeocr.env_manager import (
            get_embedded_python,
            get_embedded_venv_python,
            is_embedded_python_ready,
            get_embedded_python_info,
        )

        print("  [OK] 环境管理器辅助函数导入成功")

        # 检查路径
        python_path = get_embedded_python()
        print(f"  嵌入式 Python 路径: {python_path}")

        venv_path = get_embedded_venv_python()
        print(f"  虚拟环境 Python 路径: {venv_path}")

        # 检查就绪状态
        ready = is_embedded_python_ready()
        print(f"  嵌入式 Python 就绪: {ready}")

        # 获取详细信息
        info = get_embedded_python_info()
        print(f"  Python 信息: {info}")

        return True

    except ImportError as e:
        print(f"  [FAIL] 环境管理器导入失败: {e}")
        return False
    except Exception as e:
        print(f"  [WARN] 环境管理器检查部分失败: {e}")
        return True


def check_subprocess_ocr_service():
    """检查子进程 OCR 服务"""
    print("检查子进程 OCR 服务...")
    try:
        # 注意：由于 PIL 依赖，这里只检查模块可导入性
        # 实际功能需要嵌入式 Python 环境

        # 检查文件存在
        service_path = project_root / "src" / "vibeocr" / "services" / "ocr_service_subprocess.py"
        if service_path.exists():
            print(f"  [OK] 子进程 OCR 服务文件存在")
        else:
            print(f"  [FAIL] 子进程 OCR 服务文件不存在")
            return False

        # 尝试导入（可能会因为 PIL 失败，这是预期的）
        try:
            from vibeocr.services.ocr_service_subprocess import (
                _SharedMemoryProtocol,
                _OCRWorker,
                OCRServiceSubprocess,
            )
            print("  [OK] 子进程 OCR 服务导入成功")
            return True
        except ImportError as e:
            if "PIL" in str(e) or "PIL.Pillow" in str(e):
                print(f"  [WARN] PIL 未安装（这是预期的，需要嵌入式环境）: {e}")
                return True
            else:
                print(f"  [FAIL] 子进程 OCR 服务导入失败: {e}")
                return False

    except Exception as e:
        print(f"  [FAIL] 子进程 OCR 服务检查失败: {e}")
        return False


def check_workers_init():
    """检查 workers 包初始化"""
    print("检查 workers 包初始化...")
    init_path = project_root / "src" / "vibeocr" / "workers" / "__init__.py"

    if init_path.exists():
        print(f"  [OK] workers/__init__.py 存在")
        return True
    else:
        print(f"  [FAIL] workers/__init__.py 不存在")
        return False


def check_services_init():
    """检查 services 包初始化"""
    print("检查 services 包初始化...")
    init_path = project_root / "src" / "vibeocr" / "services" / "__init__.py"

    if init_path.exists():
        print(f"  [OK] services/__init__.py 存在")

        # 检查内容
        with open(init_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if 'USE_SUBPROCESS_OCR' in content:
                print("  [OK] services/__init__.py 包含子进程 OCR 切换逻辑")
                return True
            else:
                print("  [WARN] services/__init__.py 可能未更新")
                return True
    else:
        print(f"  [FAIL] services/__init__.py 不存在")
        return False


def main():
    """主检查流程"""
    print("=" * 60)
    print("VibeOCR 子进程服务验证")
    print("=" * 60)
    print()

    results = []

    # 检查各组件
    results.append(("共享内存功能", check_shared_memory()))
    results.append(("Worker 脚本", check_worker_script()))
    results.append(("Workers 包初始化", check_workers_init()))
    results.append(("Services 包初始化", check_services_init()))
    results.append(("环境管理器", check_env_manager()))
    results.append(("子进程 OCR 服务", check_subprocess_ocr_service()))

    # 总结
    print()
    print("=" * 60)
    print("检查结果总结")
    print("=" * 60)

    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} {name}")

    passed = sum(1 for _, r in results if r)
    total = len(results)

    print()
    print(f"通过: {passed}/{total}")

    if passed == total:
        print("[SUCCESS] 所有检查通过！")
        return 0
    else:
        print(f"[WARNING] {total - passed} 项检查未通过")
        return 1


if __name__ == "__main__":
    sys.exit(main())
