"""验证脚本：排查 bbox 界面颜色异常（黄色变蓝色）。

目的：
  1. 关闭「文档方向分类」和「文档扭曲矫正」后，PaddleOCR 的
     doc_preprocessor_res['output_img'] 是否仍然非空？
     （若非空，预览图会被替换 → 这就是 bbox 界面图片的来源）
  2. output_img 的真实通道顺序是 RGB 还是 BGR？
     用一张含明确颜色块的测试图，对比翻转/不翻转哪种与原图一致。
  3. 据此判断当前代码 `out_arr[:, :, ::-1]`（假定 BGR→RGB）是否正确。

只读调查脚本，不修改业务代码。运行：
    .venv/Scripts/python.exe scripts/verify_preproc_channels.py
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image


def build_test_image() -> np.ndarray:
    """构造一张含明确颜色块的测试图（RGB）。

    布局（每块 100x100，总 400x300）：
        红 (255,0,0) | 绿 (0,255,0) | 蓝 (0,0,255) | 黄 (255,255,0)
        青 (0,255,255)| 品红(255,0,255)| 白(255,255,255)| 黑(0,0,0)
        下半部放一些黑色文字（让 OCR 有内容识别）
    """
    block = 100
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 255),
        (0, 0, 0),
    ]
    img = np.zeros((3 * block, 4 * block, 3), dtype=np.uint8)
    for i, c in enumerate(colors):
        r, cc = divmod(i, 4)
        img[r * block : (r + 1) * block, cc * block : (cc + 1) * block] = c
    return img


def pick_block_pixel(arr: np.ndarray, block_index: int, block: int = 100) -> tuple:
    """取某色块中心像素（block_index 0..7）。返回 (R,G,B) 三元组（按数组内存顺序）。"""
    r, cc = divmod(block_index, 4)
    y = r * block + block // 2
    x = cc * block + block // 2
    return tuple(int(v) for v in arr[y, x][:3])


def main() -> int:
    test_rgb = build_test_image()
    print("=== 原始测试图（RGB）各色块中心像素（按内存 [c0,c1,c2]）===")
    labels = ["红", "绿", "蓝", "黄", "青", "品红", "白", "黑"]
    expected_rgb = {
        "红": (255, 0, 0),
        "绿": (0, 255, 0),
        "蓝": (0, 0, 255),
        "黄": (255, 255, 0),
        "青": (0, 255, 255),
        "品红": (255, 0, 255),
        "白": (255, 255, 255),
        "黑": (0, 0, 0),
    }
    for i, name in enumerate(labels):
        print(f"  {name}: 内存={pick_block_pixel(test_rgb, i)}  期望RGB={expected_rgb[name]}")

    # 保存一份原图 PNG（RGB），供肉眼对照
    Image.fromarray(test_rgb).save("_verify_orig.png")
    print("已保存原图: _verify_orig.png（RGB）\n")

    # ---- 调 PaddleOCR ----
    print("=== 导入 PaddleOCR OCR pipeline ===")
    try:
        from paddleocr import PaddleOCR
    except Exception as e:
        print(f"无法导入 PaddleOCR: {e}")
        return 1

    # 与业务代码一致：关闭两个文档预处理
    print("创建 PaddleOCR（use_doc_orientation_classify=False, use_doc_unwarping=False）...")
    pipe = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False)

    print("执行 predict ...")
    output = pipe.predict(input=test_rgb)
    output_list = list(output)
    if not output_list:
        print("predict 无输出！")
        return 1

    res = output_list[0]
    print(f"\n结果项类型: {type(res)}")

    # ---- 关键验证 1: output_img 是否非空 ----
    dp_res = None
    if hasattr(res, "get"):
        dp_res = res.get("doc_preprocessor_res")
    print(f"doc_preprocessor_res: {'存在' if dp_res is not None else 'None'}")

    out_arr = None
    if dp_res is not None:
        out_arr = dp_res.get("output_img")
        print(f"output_img: {'非空' if out_arr is not None else 'None'}")
        print(f"  angle={dp_res.get('angle', 'N/A')}")

    if out_arr is None:
        print(
            "\n[结论A] 关闭预处理后 output_img 为空 → preprocessed_image 不会被赋值，"
            "预览图不会被替换。颜色异常与预处理图无关，需查别处。"
        )
        return 0

    print(f"output_img shape={out_arr.shape} dtype={out_arr.dtype}")

    # ---- 关键验证 2: 通道顺序 ----
    # output_img 可能是原图、也可能被旋转/裁剪过。先看尺寸是否一致。
    h, w = out_arr.shape[:2]
    print(f"output_img 尺寸: {w}x{h}（原图 400x300）")
    same_size = (w == 400 and h == 300)
    print(f"尺寸是否与原图一致: {same_size}")

    if same_size:
        print("\n=== 各色块在 output_img 中的实际内存值 vs 两种解读 ===")
        print(f"{'色块':<6}{'期望RGB':<18}{'内存[c0,c1,c2]':<20}{'解读为RGB':<18}{'解读为BGR(翻转)':<18}")
        for i, name in enumerate(labels):
            mem = pick_block_pixel(out_arr, i)
            as_rgb = mem  # 直接当 RGB
            as_bgr = (mem[2], mem[1], mem[0])  # 翻转后当 RGB
            exp = expected_rgb[name]
            print(f"{name:<6}{exp!s:<18}{mem!s:<20}{as_rgb!s:<18}{as_bgr!s:<18}")

        # 统计哪种解读更接近原图
        rgb_match = 0
        bgr_match = 0
        for i, name in enumerate(labels):
            mem = pick_block_pixel(out_arr, i)
            exp = expected_rgb[name]
            if tuple(mem) == exp:
                rgb_match += 1
            if (mem[2], mem[1], mem[0]) == exp:
                bgr_match += 1
        print(f"\n按 RGB 直接读，匹配色块数: {rgb_match}/8")
        print(f"按 BGR 翻转读，匹配色块数: {bgr_match}/8")

        print("\n=== 判定 ===")
        if rgb_match > bgr_match:
            print(
                "[结论B] output_img 实际是 **RGB**。\n"
                "    当前代码 `out_arr[:, :, ::-1]` 是错误的（多余翻转），\n"
                "    会把 R/B 对调 → 黄色(255,255,0)→(0,255,255)青蓝色调，\n"
                "    与「黄色文件夹变蓝色」现象吻合。\n"
                "    修复：移除 [::-1] 翻转，直接 fromarray(out_arr)。"
            )
        elif bgr_match > rgb_match:
            print(
                "[结论C] output_img 实际是 **BGR**。\n"
                "    当前代码 `out_arr[:, :, ::-1]` 是正确的，颜色异常另有原因。"
            )
        else:
            print("[结论D] 两种解读都不完全匹配，output_img 可能被旋转/裁剪，需肉眼对照图片。")
    else:
        print(
            "\n尺寸不一致，output_img 被几何变换过。保存两种解读的 PNG 供肉眼对照："
        )

    # 保存两种解读的 PNG 供肉眼对照
    Image.fromarray(out_arr).save("_verify_out_asRGB.png")
    Image.fromarray(out_arr[:, :, ::-1] if out_arr.ndim == 3 else out_arr).save(
        "_verify_out_asBGRflipped.png"
    )
    print(
        "\n已保存：\n"
        "  _verify_out_asRGB.png       —— 直接当 RGB（不翻转，若颜色对则代码应去掉翻转）\n"
        "  _verify_out_asBGRflipped.png—— 当前代码的做法（翻转，若颜色对则代码正确）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
