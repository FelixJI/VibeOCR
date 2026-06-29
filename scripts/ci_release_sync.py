#!/usr/bin/env python3
"""CI 发版辅助脚本：CNB Release 同步 + GitHub/CNB Release 历史清理。

子命令（供 .github/workflows/release.yml 调用）：
  sync_cnb   创建/复用 CNB Release，上传 zip+sha256，公告追加 GitHub 下载地址
  prune_github  删除 GitHub 上超出最近 10 个的 Release 记录（保留 tag）
  prune_cnb     删除 CNB 上超出最近 10 个的 Release 记录（保留 tag）

纯函数 rank_releases / select_for_prune / build_release_body 有单元测试。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 仓库信息（与 src/vibeocr/views/tabs/about_tab.py、update_service.py 对齐）
GITHUB_OWNER_REPO = "FelixJI/VibeOCR"
CNB_OWNER_REPO = "feljii/VibeOCR"
KEEP = 10

_GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER_REPO}"
_CNB_API = f"https://api.cnb.cool/{CNB_OWNER_REPO}"


# ---------------------------------------------------------------------------
# 纯函数（可测）
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def _semver_key(tag: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.match(tag or "")
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def rank_releases(releases: list[dict]) -> list[dict]:
    """过滤掉 draft/prerelease，按语义版本降序排序。

    兼容 gh 的 isDraft/isPreRelease 与 CNB 的 draft/prerelease 字段名。
    """
    stable = [
        r for r in releases
        if not r.get("isDraft", r.get("draft"))
        and not r.get("isPreRelease", r.get("prerelease"))
    ]
    return sorted(
        stable,
        key=lambda r: _semver_key(r.get("tagName", r.get("tag_name", ""))),
        reverse=True,
    )


def select_for_prune(ranked: list[dict], keep: int = KEEP) -> list[dict]:
    """返回应被删除的 release（排名在 keep 之后的）。"""
    return ranked[keep:]


def build_release_body(base: str, tag: str) -> str:
    """在 CHANGELOG 正文后追加 GitHub/CNB 下载地址。"""
    return (
        (base or f"VibeOCR {tag}")
        + "\n\n---\n\n## 下载地址\n"
        + f"- GitHub: https://github.com/{GITHUB_OWNER_REPO}/releases/{tag}\n"
        + f"- CNB（需登录）：https://cnb.cool/{CNB_OWNER_REPO}/-/releases/{tag}\n"
    )


# ---------------------------------------------------------------------------
# 子命令（后续 Task 填充）
# ---------------------------------------------------------------------------


def cmd_sync_cnb(args: argparse.Namespace) -> int:
    # Task 7 实现
    raise NotImplementedError


def cmd_prune_github(args: argparse.Namespace) -> int:
    # Task 8 实现
    raise NotImplementedError


def cmd_prune_cnb(args: argparse.Namespace) -> int:
    # Task 9 实现
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync_cnb", help="同步 Release 产物到 CNB")
    p_sync.add_argument("--version", required=True)
    p_sync.add_argument("--notes", default="release_notes.md")
    p_sync.set_defaults(func=cmd_sync_cnb)

    sub.add_parser("prune_github", help="清理 GitHub Release（保留 10）").set_defaults(func=cmd_prune_github)
    sub.add_parser("prune_cnb", help="清理 CNB Release（保留 10）").set_defaults(func=cmd_prune_cnb)

    ns = parser.parse_args(argv)
    try:
        return ns.func(ns)
    except SystemExit:
        raise
    except Exception as e:  # CI 内任何失败都不阻断主发版
        print(f"::warning::ci_release_sync {ns.cmd} 失败: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
