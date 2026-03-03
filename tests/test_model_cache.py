"""模型缓存管理器测试"""

import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vibeocr.model_cache_manager import (
    check_models_cached,
    get_model_cache_path,
    get_paddlex_home,
    get_pipeline_model_info,
    is_pipeline_cached,
    load_model_cache,
    quick_check_all_models,
    update_cache,
)


def test_model_cache():
    """测试模型缓存功能"""
    print("=" * 60)
    print("模型缓存管理器测试")
    print("=" * 60)

    # 1. 显示 PaddleX 主目录
    paddlex_home = get_paddlex_home()
    print(f"\n1. PaddleX 主目录: {paddlex_home}")

    # 2. 显示缓存文件路径
    cache_path = get_model_cache_path()
    print(f"2. 模型缓存文件路径: {cache_path}")
    print(f"   缓存文件存在: {cache_path.exists()}")

    # 3. 检查模型缓存状态
    print("\n3. 检查各管道模型缓存状态:")
    pipeline_status = check_models_cached()
    for pipeline, ready in pipeline_status.items():
        status = "[OK] 已就绪" if ready else "[X] 未就绪"
        print(f"   - {pipeline}: {status}")

    # 4. 测试快速检查
    print("\n4. 测试快速检查所有模型:")
    quick_status = quick_check_all_models()
    for pipeline, ready in quick_status.items():
        status = "[OK] 已就绪" if ready else "[X] 未就绪"
        print(f"   - {pipeline}: {status}")

    # 5. 测试单个管道检查
    print("\n5. 测试单个管道检查 (OCR):")
    ocr_cached = is_pipeline_cached("OCR")
    print(f"   OCR 模型已缓存: {ocr_cached}")

    # 6. 获取管道模型信息
    print("\n6. 获取 OCR 管道模型信息:")
    info = get_pipeline_model_info("OCR")
    print(f"   管道: {info['pipeline']}")
    print(f"   就绪: {info['ready']}")
    print(f"   缺失模型: {info['missing_models']}")
    print(f"   PaddleX 主目录: {info['paddlex_home']}")

    # 7. 加载缓存
    print("\n7. 加载缓存:")
    cache = load_model_cache()
    if cache:
        print(f"   缓存版本: {cache.get('version')}")
        print(f"   最后更新: {cache.get('last_update')}")
        print(f"   缓存的管道数: {len(cache.get('pipelines', {}))}")
    else:
        print("   无缓存或缓存已过期")

    # 8. 强制更新缓存
    print("\n8. 强制更新缓存:")
    updated_status = update_cache()
    print(f"   已更新 {len(updated_status)} 个管道的缓存")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(test_model_cache())
