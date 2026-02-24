"""
检查 PaddleX 直接导入打包的可行性

测试：
1. PaddleX 依赖大小
2. PyInstaller 打包测试
3. Nuitka 编译测试
"""

import os
import sys
from pathlib import Path


def check_paddlex_size():
    """检查 PaddleX 及其依赖的大小"""
    print("=" * 60)
    print("1. 检查 PaddleX 依赖大小")
    print("=" * 60)

    try:
        import pip
        import pkg_resources

        # 获取 PaddleX 及其依赖
        distribution = pkg_resources.get_distribution('paddlex')
        print(f"\nPaddleX 版本: {distribution.version}")

        # 获取依赖
        dependencies = distribution.requires()

        print("\n主要依赖:")
        total_size = 0

        for dep in dependencies[:10]:  # 只显示前10个
            try:
                dist = pkg_resources.get_distribution(str(dep).split('>=')[0].split('==')[0])
                if dist.egg_info and os.path.exists(dist.egg_info):
                    # 估算大小
                    location = dist.egg_info
                    if os.path.exists(location):
                        size = sum(f.stat().st_size for f in Path(location).rglob('*') if f.is_file())
                        total_size += size
                        print(f"  {dist.project_name}: {size / 1024 / 1024:.1f} MB")
            except:
                pass

        print(f"\n估算总大小: {total_size / 1024 / 1024:.1f} MB")
        print("注意：这只是部分依赖，实际打包后会更大")

    except ImportError as e:
        print(f"无法检查: {e}")


def check_paddlex_import():
    """检查 PaddleX 导入"""
    print("\n" + "=" * 60)
    print("2. 检查 PaddleX 导入")
    print("=" * 60)

    try:
        import paddlex
        print(f"✓ PaddleX 可以导入")

        # 尝试创建流水线
        try:
            from paddlex import create_pipeline
            print(f"✓ create_pipeline 可以导入")

            # 注意：不实际创建流水线，因为这需要 GPU/CUDA
            print("  （跳过实际创建流水线，避免资源占用）")

        except ImportError as e:
            print(f"✗ create_pipeline 导入失败: {e}")

    except ImportError as e:
        print(f"✗ PaddleX 导入失败: {e}")
        print("\n请先安装 PaddleX:")
        print("  pip install paddlex")


def check_pyinstaller_available():
    """检查 PyInstaller 可用性"""
    print("\n" + "=" * 60)
    print("3. 检查 PyInstaller")
    print("=" * 60)

    try:
        import PyInstaller
        print(f"✓ PyInstaller 已安装")
        print(f"  版本: {PyInstaller.__version__}")

        # 检查是否可以运行
        import subprocess
        result = subprocess.run(
            ["pyinstaller", "--version"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"  CLI 可用: {result.stdout.strip()}")
        else:
            print(f"  CLI 不可用")

    except ImportError:
        print(f"✗ PyInstaller 未安装")
        print("\n安装命令:")
        print("  pip install pyinstaller")


def check_nuitka_available():
    """检查 Nuitka 可用性"""
    print("\n" + "=" * 60)
    print("4. 检查 Nuitka")
    print("=" * 60)

    try:
        import nuitka
        print(f"✓ Nuitka 已安装")

        # 检查版本
        import subprocess
        result = subprocess.run(
            ["nuitka", "--version"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"  版本: {result.stdout.strip()}")
        else:
            print(f"  版本: 未知")

    except ImportError:
        print(f"✗ Nuitka 未安装")
        print("\n安装命令:")
        print("  pip install nuitka")


def create_test_script():
    """创建测试脚本"""
    print("\n" + "=" * 60)
    print("5. 创建测试脚本")
    print("=" * 60)

    test_script = Path("test_ocr_import.py")

    content = '''#!/usr/bin/env python
"""测试 OCR 导入"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_direct_import():
    """测试直接导入模式"""
    print("测试直接导入 PaddleX...")

    try:
        from paddlex import create_pipeline
        print("✓ PaddleX 导入成功")

        # 尝试创建流水线（CPU 模式）
        try:
            pipeline = create_pipeline("OCR", device="cpu")
            print("✓ OCR 流水线创建成功")
            return True
        except Exception as e:
            print(f"✗ 流水线创建失败: {e}")
            return False

    except ImportError as e:
        print(f"✗ PaddleX 导入失败: {e}")
        return False


def test_subprocess_import():
    """测试子进程模式"""
    print("\\n测试子进程模式...")

    try:
        from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess
        print("✓ 子进程服务导入成功")

        # 创建服务实例（不实际启动 worker）
        print("  （跳过实际创建服务，避免启动子进程）")
        return True

    except ImportError as e:
        print(f"✗ 子进程服务导入失败: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("VibeOCR 导入测试")
    print("=" * 60)
    print()

    direct_ok = test_direct_import()
    subprocess_ok = test_subprocess_import()

    print()
    print("=" * 60)
    print("结果总结:")
    print(f"  直接导入: {'✓' if direct_ok else '✗'}")
    print(f"  子进程模式: {'✓' if subprocess_ok else '✗'}")
    print("=" * 60)
'''

    test_script.write_text(content, encoding='utf-8')
    print(f"✓ 测试脚本已创建: {test_script}")
    print(f"\n运行测试:")
    print(f"  python test_ocr_import.py")


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("VibeOCR 打包方案检查")
    print("=" * 60)

    # 检查各项
    check_paddlex_size()
    check_paddlex_import()
    check_pyinstaller_available()
    check_nuitka_available()
    create_test_script()

    print("\n" + "=" * 60)
    print("检查完成")
    print("=" * 60)
    print("\n建议:")
    print("1. 如果 PaddleX 已安装，可以测试直接导入打包")
    print("2. 考虑使用 PyInstaller 或 Nuitka 进行测试打包")
    print("3. 对比打包后的体积和启动速度")
    print("4. 如果体积太大，建议使用子进程方案")


if __name__ == "__main__":
    main()
