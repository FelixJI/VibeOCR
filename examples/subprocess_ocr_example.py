#!/usr/bin/env python
"""
子进程 OCR 服务使用示例

演示如何使用子进程版本的 OCR 服务进行文本识别。
"""

import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from PIL import Image, ImageDraw, ImageFont


def create_sample_image(text: str, width: int = 400, height: int = 200) -> Image.Image:
    """创建包含指定文本的示例图像"""
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # 尝试使用系统字体
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except IOError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 32)
        except IOError:
            font = ImageFont.load_default()

    # 绘制文本
    draw.text((20, 20), text, fill="black", font=font)

    return img


def example_basic_usage():
    """示例 1: 基本使用"""
    print("=" * 60)
    print("示例 1: 基本使用")
    print("=" * 60)

    from vibeocr.services import OCRService

    # 创建服务实例（单例）
    service = OCRService()

    # 创建测试图像
    img = create_sample_image("Hello VibeOCR!")

    # 执行识别
    try:
        text = service.recognize(img)
        print(f"识别结果: {repr(text)}")
    except Exception as e:
        print(f"识别失败: {e}")

    print()


def example_custom_configuration():
    """示例 2: 自定义配置"""
    print("=" * 60)
    print("示例 2: 自定义配置（直接使用子进程实现）")
    print("=" * 60)

    try:
        from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

        # 创建自定义配置的服务
        service = OCRServiceSubprocess(
            max_workers=2,      # 2 个并行 worker
            use_gpu=False,      # 使用 CPU（便于测试）
            shm_size=5*1024*1024  # 5MB 共享内存
        )

        # 创建测试图像
        img = create_sample_image("Custom Config Test")

        # 执行识别
        try:
            text = service.recognize(img)
            print(f"识别结果: {repr(text)}")
        except Exception as e:
            print(f"识别失败: {e}")

        # 清理资源
        service.shutdown()

    except ImportError as e:
        print(f"子进程 OCR 服务不可用: {e}")

    print()


def example_batch_processing():
    """示例 3: 批量处理"""
    print("=" * 60)
    print("示例 3: 批量处理多个图像")
    print("=" * 60)

    from vibeocr.services import OCRService

    service = OCRService()

    # 创建多个测试图像
    texts = ["First Image", "Second Image", "Third Image"]
    images = [create_sample_image(text) for text in texts]

    # 批量识别
    results = []
    for i, img in enumerate(images):
        try:
            text = service.recognize(img)
            results.append(text)
            print(f"图像 {i+1}: {repr(text)}")
        except Exception as e:
            print(f"图像 {i+1} 识别失败: {e}")

    print()


def example_environment_info():
    """示例 4: 环境信息检查"""
    print("=" * 60)
    print("示例 4: 嵌入式 Python 环境信息")
    print("=" * 60)

    try:
        from vibeocr.env_manager import (
            get_embedded_python_info,
            is_embedded_python_ready,
            get_embedded_python,
        )

        # 获取环境信息
        info = get_embedded_python_info()
        print(f"Python 路径: {info['path']}")
        print(f"Python 版本: {info['version']}")
        print(f"环境模式: {info['mode']}")
        print(f"存在 PaddlePaddle: {info['has_paddle']}")
        print(f"存在 PaddleX: {info['has_paddlex']}")

        # 检查是否准备好
        ready = is_embedded_python_ready()
        print(f"环境就绪: {ready}")

        # 获取 Python 路径
        python_path = get_embedded_python()
        print(f"嵌入式 Python: {python_path}")

    except Exception as e:
        print(f"获取环境信息失败: {e}")
        import traceback
        traceback.print_exc()

    print()


def example_error_handling():
    """示例 5: 错误处理"""
    print("=" * 60)
    print("示例 5: 错误处理和降级")
    print("=" * 60)

    from vibeocr.services import OCRService

    service = OCRService()

    # 创建空白图像
    img = Image.new("RGB", (100, 50), color="white")

    # 尝试识别
    try:
        text = service.recognize(img)
        print(f"空白图像识别结果: {repr(text)}")
    except RuntimeError as e:
        print(f"捕获运行时错误: {e}")
    except Exception as e:
        print(f"捕获其他错误: {type(e).__name__}: {e}")

    print()


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("VibeOCR 子进程服务使用示例")
    print("=" * 60 + "\n")

    # 运行各个示例
    example_environment_info()
    example_basic_usage()
    example_custom_configuration()
    example_batch_processing()
    example_error_handling()

    print("=" * 60)
    print("示例运行完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
