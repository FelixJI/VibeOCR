#!/usr/bin/env python3
"""更新 KaTeX 离线资源

从 CDN 下载最新 KaTeX release 并替换本地资源文件。

用法:
    python qa/update_katex.py
"""

import sys
import urllib.request
from pathlib import Path

KATEX_VERSION = "0.16.11"
KATEX_CDN_BASE = f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist"

RESOURCES_DIR = Path(__file__).parent.parent / "resources" / "katex"

FILES = {
    "katex.min.js": f"{KATEX_CDN_BASE}/katex.min.js",
    "katex.min.css": f"{KATEX_CDN_BASE}/katex.min.css",
}


def main():
    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)

    for filename, url in FILES.items():
        target = RESOURCES_DIR / filename
        print(f"下载 {url} -> {target}")
        try:
            urllib.request.urlretrieve(url, target)
            size = target.stat().st_size
            print(f"  完成: {size:,} bytes")
        except Exception as e:
            print(f"  失败: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"\nKaTeX v{KATEX_VERSION} 资源已更新到 {RESOURCES_DIR}")


if __name__ == "__main__":
    main()
