#!/usr/bin/env python3
"""检查离线资源文件是否存在"""

from pathlib import Path

RESOURCES_DIR = Path(__file__).parent.parent / "resources" / "katex"
REQUIRED_FILES = ["katex.min.js", "katex.min.css"]


def check() -> bool:
    missing = [f for f in REQUIRED_FILES if not (RESOURCES_DIR / f).exists()]
    if missing:
        print(f"缺少 KaTeX 资源: {', '.join(missing)}")
        print("运行 python qa/update_katex.py 来下载")
        return False
    print("KaTeX 资源检查通过")
    return True


if __name__ == "__main__":
    import sys

    sys.exit(0 if check() else 1)
