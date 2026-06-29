#!/usr/bin/env python3
"""CI 发版辅助脚本：Gitee Release 同步 + GitHub/Gitee Release 历史清理。

子命令（供 .github/workflows/release.yml 调用）：
  sync_gitee   创建/复用 Gitee Release，上传 主包+WebEngine 资源包 + sha256，
               公告追加 GitHub/Gitee 下载地址
  prune_github  删除 GitHub 上超出最近 10 个的 Release 记录（保留 tag）
  prune_gitee   删除 Gitee 上超出最近 3 个的 Release 记录（保留 tag，防 1GB 附件超限）

发布渠道（方案 C）：CNB 仅镜像代码；产物主源 GitHub，镜像源 Gitee（国内客户端优先）。

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
import urllib.parse
import urllib.request
from pathlib import Path

# 仓库信息（与 src/vibeocr/views/tabs/about_tab.py、update_service.py 对齐）
GITHUB_OWNER_REPO = "FelixJI/VibeOCR"
GITEE_OWNER_REPO = "felixjii/vibeocr"
KEEP = 10  # GitHub 保留数
KEEP_GITEE = 3  # Gitee 保留数（单仓库附件 1GB 上限，每版 ~150MB）

_GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER_REPO}"
_GITEE_API = f"https://gitee.com/api/v5/repos/{GITEE_OWNER_REPO}"


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
    """在 CHANGELOG 正文后追加 GitHub/Gitee 下载地址。"""
    return (
        (base or f"VibeOCR {tag}")
        + "\n\n---\n\n## 下载地址\n"
        + f"- GitHub: https://github.com/{GITHUB_OWNER_REPO}/releases/{tag}\n"
        + f"- Gitee（国内推荐）：https://gitee.com/{GITEE_OWNER_REPO}/releases/{tag}\n"
    )


# ---------------------------------------------------------------------------
# 子命令（后续 Task 填充）
# ---------------------------------------------------------------------------


def cmd_sync_gitee(args: argparse.Namespace) -> int:
    token = os.environ.get("GITEE_TOKEN", "")
    if not token:
        print("::warning::未配置 GITEE_TOKEN，跳过 Gitee Release 同步")
        return 0

    version = args.version
    tag = f"v{version}"

    # 4 个产物文件：主包 + WebEngine 资源包 + 各自 sha256
    assets = [
        Path(f"dist/VibeOCR-v{version}-win64.zip"),
        Path(f"dist/VibeOCR-v{version}-win64.zip.sha256"),
        Path(f"dist/VibeOCR-v{version}-webengine-win64.zip"),
        Path(f"dist/VibeOCR-v{version}-webengine-win64.zip.sha256"),
    ]
    notes_path = Path(args.notes)
    base_body = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
    body = build_release_body(base_body, tag)

    def request_with_retry(req, *, attempts=3, base_delay=10):
        last_err = None
        for i in range(1, attempts + 1):
            try:
                timeout = req.timeout if hasattr(req, "timeout") else 60
                return urllib.request.urlopen(req, timeout=timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                if i == attempts:
                    raise
                delay = base_delay * i
                print(f"  请求失败 ({e})，{delay}s 后第 {i + 1} 次尝试...")
                time.sleep(delay)
        raise last_err  # 不可达

    def _gitee_url(path: str) -> str:
        # Gitee v5 用 access_token query 参数鉴权
        sep = "&" if "?" in path else "?"
        return f"{_GITEE_API}{path}{sep}access_token={token}"

    # 1) 创建 Release；已存在则按 tag 复用并 PATCH 更新 body（补下载地址）
    release_id = None
    create_data = urllib.parse.urlencode({
        "tag_name": tag,
        "name": tag,
        "body": body,
        "target_commitish": "main",
        "prerelease": "false",
    }).encode("utf-8")
    create_req = urllib.request.Request(_gitee_url("/releases"), data=create_data, method="POST")
    create_req.timeout = 60
    try:
        resp = request_with_retry(create_req)
        release = json.loads(resp.read().decode("utf-8"))
        resp.close()
        release_id = release["id"]
        print(f"Gitee Release 创建成功 id={release_id}")
    except urllib.error.HTTPError:
        # 已存在则按 tag 查询复用
        get_req = urllib.request.Request(
            _gitee_url(f"/releases/tags/{tag}"), method="GET"
        )
        get_req.timeout = 60
        resp2 = request_with_retry(get_req)
        release = json.loads(resp2.read().decode("utf-8"))
        resp2.close()
        release_id = release["id"]
        print(f"Gitee Release 已存在，复用 id={release_id}")
        try:
            patch_data = urllib.parse.urlencode({"body": body}).encode("utf-8")
            patch_req = urllib.request.Request(
                _gitee_url(f"/releases/{release_id}"), data=patch_data, method="PATCH"
            )
            patch_req.timeout = 60
            r = request_with_retry(patch_req)
            r.read()
            r.close()
            print("  已更新 Release body（追加下载地址）")
        except Exception as e2:
            print(f"  ::warning::更新 Release body 失败（不影响附件上传）: {e2}")

    # 2) 逐个上传附件（Gitee: POST /releases/{id}/attach_files，multipart/form-data）
    for path in assets:
        if not path.exists():
            print(f"::warning::附件不存在，跳过: {path}")
            continue
        boundary = "----VibeOCRBoundary" + str(int(time.time() * 1000))
        file_bytes = path.read_bytes()
        # 构造 multipart/form-data body（字段名 name=file）
        body_parts = [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="name"\r\n\r\n',
            f"{path.name}\r\n".encode(),
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\n'
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        upload_req = urllib.request.Request(
            _gitee_url(f"/releases/{release_id}/attach_files"),
            data=b"".join(body_parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        upload_req.timeout = 600  # 主包 ~70MB，给足时间
        try:
            r = request_with_retry(upload_req, attempts=3, base_delay=15)
            r.read()
            r.close()
            print(f"  上传附件成功: {path.name}")
        except Exception as e:
            print(f"  ::warning::上传附件失败 {path.name}: {e}")

    print("Gitee Release 同步完成")
    return 0


def cmd_prune_github(args: argparse.Namespace) -> int:
    import subprocess

    # gh 可能不在本机 PATH（仅 CI runner 有）；缺失则跳过
    try:
        listing = subprocess.run(
            ["gh", "release", "list", "--repo", GITHUB_OWNER_REPO, "--limit", "50",
             "--json", "tagName,isDraft,isPreRelease"],
            capture_output=True, encoding="utf-8", check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"::warning::gh 调用失败，跳过 GitHub Release 清理: {e}")
        return 0

    try:
        data = json.loads(listing.stdout or "[]")
    except json.JSONDecodeError:
        data = []

    to_delete = select_for_prune(rank_releases(data), keep=KEEP)
    if not to_delete:
        print("  无需清理（<= 10 个 Release）")
        return 0

    for r in to_delete:
        tag = r["tagName"]
        print(f"  删除 GitHub Release 记录: {tag}（保留 tag）")
        subprocess.run(
            ["gh", "release", "delete", tag, "--repo", GITHUB_OWNER_REPO,
             "--cleanup-tag=false", "--yes"],
            check=False,
        )
    print("GitHub Release 清理完成")
    return 0


def cmd_prune_gitee(args: argparse.Namespace) -> int:
    token = os.environ.get("GITEE_TOKEN", "")
    if not token:
        print("::warning::未配置 GITEE_TOKEN，跳过 Gitee Release 清理")
        return 0

    # Gitee v5 列出 releases（含 draft/prerelease 字段）
    list_req = urllib.request.Request(
        f"{_GITEE_API}/releases?access_token={token}&page=1&per_page=100",
        method="GET",
    )
    list_req.timeout = 60
    try:
        with urllib.request.urlopen(list_req, timeout=60) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"::warning::获取 Gitee Release 列表失败: HTTP {e.code}")
        return 0

    # Gitee 单仓库附件 1GB 上限，保留近 3 版（每版主包+资源包 ~150MB）
    to_delete = select_for_prune(rank_releases(releases), keep=KEEP_GITEE)
    if not to_delete:
        print(f"  无需清理（<= {KEEP_GITEE} 个 Release）")
        return 0

    for r in to_delete:
        rid = r.get("id")
        tag = r.get("tag_name", r.get("tagName"))
        print(f"  删除 Gitee Release 记录: {tag} (id={rid})")
        dreq = urllib.request.Request(
            f"{_GITEE_API}/releases/{rid}?access_token={token}", method="DELETE"
        )
        dreq.timeout = 60
        try:
            with urllib.request.urlopen(dreq, timeout=60) as dresp:
                dresp.read()
        except urllib.error.HTTPError as e:
            print(f"  ::warning::删除失败 {rid}: HTTP {e.code}")
    print("Gitee Release 清理完成")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync_gitee", help="同步 Release 产物到 Gitee")
    p_sync.add_argument("--version", required=True)
    p_sync.add_argument("--notes", default="release_notes.md")
    p_sync.set_defaults(func=cmd_sync_gitee)

    sub.add_parser("prune_github", help="清理 GitHub Release（保留 10）").set_defaults(func=cmd_prune_github)
    sub.add_parser("prune_gitee", help="清理 Gitee Release（保留 3）").set_defaults(func=cmd_prune_gitee)

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
