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
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 仓库信息 —— 运行时 SSOT 在 src/vibeocr/services/env_config.py
# （GITHUB_OWNER / GITHUB_REPO / GITEE_OWNER / GITEE_REPO 等）。
# 本脚本因纯 stdlib 独立运行（python scripts/ci_release_sync.py）无法 import；
# 改动 owner/repo 时需手动同步两处。
GITHUB_OWNER_REPO = "FelixJI/VibeOCR"
GITEE_OWNER_REPO = "felixjii/vibeocr"
KEEP = 10  # GitHub 保留数
KEEP_GITEE = 3  # Gitee 保留数（单仓库附件 1GB 上限，每版 ~150MB）

_GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER_REPO}"
_GITEE_API = f"https://gitee.com/api/v5/repos/{GITEE_OWNER_REPO}"

# Gitee 大文件上传参数：海外 runner → 国内 Gitee，慢且不稳，须流式 + 进度 + 墙钟。
# 历史 CI 此处 socket timeout 600s 静默卡满 + 重试 3 次，导致发版卡 27 分钟被取消。
GITEE_UPLOAD_HOST = "gitee.com"
GITEE_UPLOAD_PER_ASSET_LIMIT = 8 * 60   # 单附件墙钟上限（秒），超时主动 abort
GITEE_UPLOAD_CHUNK = 1024 * 1024        # 1MB/块，每块后报进度 + 查墙钟
GITEE_UPLOAD_RETRIES = 2                # 网络错误重试次数（HTTPError 不重试）


def _gitee_auth_header(token: str) -> dict[str, str]:
    """Gitee v5 REST 鉴权头。

    用 ``Authorization: token <token>`` 替代 URL query 里的 access_token，
    避免 token 出现在 URL / CI 日志 / 代理日志里。

    注意：附件上传（attach_files，multipart/form-data）不走这里——multipart 时
    Gitee 不读 query token，access_token 必须作为 multipart body 的 form 字段，
    见 upload_asset_streaming。
    """
    return {"Authorization": f"token {token}"}


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
# Gitee 大文件流式上传（http.client 分块 send + 进度回调 + 墙钟超时）
# ---------------------------------------------------------------------------


def upload_asset_streaming(
    host: str,
    path: str,
    token: str,
    file_path: Path,
    *,
    deadline: float,
    chunk_size: int = GITEE_UPLOAD_CHUNK,
    on_progress=None,
    conn_factory=None,
) -> tuple[int, bytes]:
    """流式上传单附件到 Gitee attach_files，返回 (status, body)。

    用 http.client 手动分块 send（每 chunk_size 字节一块），每块后：
    - 调 on_progress(sent, total, rate) 上报进度；
    - 检查 deadline（墙钟），超时抛 TimeoutError。

    multipart/form-data 与现有 Gitee 上传一致：access_token 作为 body 的 form
    字段（不放 URL query），file 字段靠 Content-Disposition 带 filename。

    HTTP 非 2xx 抛 RuntimeError（含状态码 + 响应体），供调用方记 ::error::。
    conn_factory 可注入 fake connection，便于测试。

    总体大小须在 Content-Length 中预先告知（multipart 是一次性拼接的固定结构，
    无法真正的 transfer-encoding:chunked 流式，但分块 send 仍带来进度+墙钟收益）。
    """
    file_bytes = file_path.read_bytes()
    total = len(file_bytes)

    # multipart body 的固定头部与尾部（不含文件体）
    boundary = "----VibeOCRBoundary" + str(int(time.time() * 1000))
    head = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="access_token"\r\n\r\n',
        f"{token}\r\n".encode(),
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{file_path.name}"\r\n'
        ).encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
    ])
    tail = f"\r\n--{boundary}--\r\n".encode()
    content_length = len(head) + total + len(tail)

    conn = (conn_factory or http.client.HTTPSConnection)(host, timeout=60)
    try:
        conn.putrequest("POST", path)
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(content_length))
        conn.endheaders()

        # 头部一次性发送（小）
        conn.send(head)

        # 文件体分块发送，每块报进度 + 查墙钟
        sent = 0
        start = time.time()
        for off in range(0, total, chunk_size):
            chunk = file_bytes[off:off + chunk_size]
            conn.send(chunk)
            sent += len(chunk)
            now = time.time()
            elapsed = max(now - start, 1e-6)
            rate = sent / elapsed
            if on_progress is not None:
                on_progress(sent, total, rate)
            if now > deadline:
                raise TimeoutError(
                    f"上传 {file_path.name} 已超墙钟上限（{int(deadline - start)}s+）"
                )

        # 尾部 boundary
        conn.send(tail)

        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    finally:
        conn.close()

    if not (200 <= status < 300):
        raise RuntimeError(f"HTTP {status} {body.decode('utf-8', 'replace')[:300]}")
    return status, body


# ---------------------------------------------------------------------------
# 子命令
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
            except urllib.error.HTTPError:
                # HTTPError（401/403/500 等）是确定性失败，重试无意义且会拖时间
                # （每附件最多 attempts×base_delay 秒）。立即抛出，由调用方打印
                # 状态码 + 响应体定位。
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                if i == attempts:
                    raise
                delay = base_delay * i
                print(f"  请求失败 ({e})，{delay}s 后第 {i + 1} 次尝试...")
                time.sleep(delay)
        raise last_err  # 不可达

    def _gitee_url(path: str) -> str:
        # REST 端点 URL（不带 token；鉴权走 Authorization header，见下方 headers=）
        return f"{_GITEE_API}{path}"

    auth = _gitee_auth_header(token)

    # 1) 创建 Release；已存在则按 tag 复用并 PATCH 更新 body（补下载地址）
    release_id = None
    create_data = urllib.parse.urlencode({
        "tag_name": tag,
        "name": tag,
        "body": body,
        "target_commitish": "main",
        "prerelease": "false",
    }).encode("utf-8")
    create_req = urllib.request.Request(_gitee_url("/releases"), data=create_data, method="POST", headers=auth)
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
            _gitee_url(f"/releases/tags/{tag}"), method="GET", headers=auth
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
                _gitee_url(f"/releases/{release_id}"), data=patch_data, method="PATCH", headers=auth
            )
            patch_req.timeout = 60
            r = request_with_retry(patch_req)
            r.read()
            r.close()
            print("  已更新 Release body（追加下载地址）")
        except Exception as e2:
            print(f"  ::warning::更新 Release body 失败（不影响附件上传）: {e2}")

    # 2) 逐个上传附件（Gitee: POST /releases/{id}/attach_files，multipart/form-data）
    # 对齐 Gitee 官方 gitee-release-cli：access_token 作为 multipart body 的 form
    # 字段（不能放 URL query——multipart 请求时 query token 不被读取），且只发
    # file 字段（filename 靠 Content-Disposition 带，没有独立的 name 字段）。
    any_failed = False
    upload_path = f"/api/v5/repos/{GITEE_OWNER_REPO}/releases/{release_id}/attach_files"
    for path in assets:
        if not path.exists():
            print(f"::warning::附件不存在，跳过: {path}")
            continue

        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"  开始上传 {path.name}（{size_mb:.1f}MB）…")

        # 进度回调：每 5% 或每 3 秒打一行，避免 CI 日志被淹没
        state = {"last_pct": -5, "last_ts": 0.0}

        def on_progress(sent, total, rate, _name=path.name, _st=state):
            pct = int(sent * 100 / total) if total else 100
            now = time.time()
            if pct >= _st["last_pct"] + 5 or now - _st["last_ts"] >= 3:
                _st["last_pct"] = pct
                _st["last_ts"] = now
                sent_mb = sent / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                rate_mb = rate / (1024 * 1024)
                print(f"    …上传 {_name} {pct}% "
                      f"({sent_mb:.1f}MB/{total_mb:.1f}MB) {rate_mb:.2f}MB/s")

        # 重试：网络错误/超时重试 GITEE_UPLOAD_RETRIES 次（每次重新建连 + 新 deadline）；
        # HTTPError（由 upload_asset_streaming 转为 RuntimeError）是确定性失败，不重试。
        attempt = 0
        while True:
            attempt += 1
            deadline = time.time() + GITEE_UPLOAD_PER_ASSET_LIMIT
            try:
                upload_asset_streaming(
                    GITEE_UPLOAD_HOST, upload_path, token, path,
                    deadline=deadline, on_progress=on_progress,
                )
                # 覆盖到 100% 的收尾进度行
                on_progress(path.stat().st_size, path.stat().st_size, 0.0)
                print(f"  上传附件成功: {path.name}")
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt > GITEE_UPLOAD_RETRIES:
                    any_failed = True
                    print(f"  ::error::上传附件失败 {path.name} "
                          f"（{GITEE_UPLOAD_RETRIES + 1} 次尝试后仍失败）: {e}")
                    break
                delay = 15 * attempt
                print(f"  上传 {path.name} 失败 ({e})，{delay}s 后重试 "
                      f"（{attempt}/{GITEE_UPLOAD_RETRIES}）…")
                time.sleep(delay)
            except Exception as e:
                # RuntimeError(HTTP 非 2xx) 或其他确定性失败，不重试
                any_failed = True
                print(f"  ::error::上传附件失败 {path.name}: {e}")
                break

    if any_failed:
        print(
            "::warning::部分附件上传失败，详见上方 ::error::；"
            "按设计不阻断 GitHub 主发版"
        )
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

    # Gitee v5 列出 releases（含 draft/prerelease 字段）。鉴权走 header，
    # 不再把 token 拼进 URL（与 cmd_sync_gitee 保持一致）。
    auth = _gitee_auth_header(token)
    list_req = urllib.request.Request(
        f"{_GITEE_API}/releases?page=1&per_page=100",
        method="GET", headers=auth,
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
            f"{_GITEE_API}/releases/{rid}", method="DELETE", headers=auth
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
