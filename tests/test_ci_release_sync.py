"""ci_release_sync 纯函数测试（版本排序与清理选择）"""

import io
import sys
from pathlib import Path

# scripts/ 不在包里，手动加入 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _gh_release(tag, draft=False, pre=False):
    return {"tagName": tag, "isDraft": draft, "isPreRelease": pre}


def test_rank_descends_by_semver():
    from ci_release_sync import rank_releases

    releases = [
        _gh_release("v0.3.0"),
        _gh_release("v0.10.0"),  # 应排在 0.3.0 之后（语义版本，非字典序）
        _gh_release("v0.2.0"),
    ]
    ranked = rank_releases(releases)
    assert [r["tagName"] for r in ranked] == ["v0.10.0", "v0.3.0", "v0.2.0"]


def test_rank_skips_draft_and_prerelease():
    from ci_release_sync import rank_releases

    releases = [
        _gh_release("v0.3.0"),
        _gh_release("v0.9.0-pre", pre=True),
        _gh_release("v0.2.0", draft=True),
    ]
    ranked = rank_releases(releases)
    assert [r["tagName"] for r in ranked] == ["v0.3.0"]


def test_select_for_prune_keeps_10():
    from ci_release_sync import rank_releases, select_for_prune

    releases = [_gh_release(f"v0.{i}.0") for i in range(15)]
    ranked = rank_releases(releases)
    to_delete = select_for_prune(ranked, keep=10)
    # 第 11 个及以后被选中删除（版本号较小的 5 个：v0.0.0..v0.4.0）
    deleted_tags = {r["tagName"] for r in to_delete}
    assert deleted_tags == {f"v0.{i}.0" for i in range(5)}


def test_select_for_prune_under_threshold_empty():
    from ci_release_sync import rank_releases, select_for_prune

    releases = [_gh_release(f"v0.{i}.0") for i in range(8)]
    ranked = rank_releases(releases)
    assert select_for_prune(ranked, keep=10) == []


def test_build_release_body_appends_download_links():
    from ci_release_sync import build_release_body

    body = build_release_body(base="## [0.3.0]\n- fix", tag="v0.3.0")
    assert "## [0.3.0]" in body
    assert "https://github.com/FelixJI/VibeOCR/releases/v0.3.0" in body
    assert "https://gitee.com/felixjii/vibeocr/releases/v0.3.0" in body


# ---------------------------------------------------------------------------
# cmd_sync_gitee 附件上传 —— 对齐 Gitee 官方 gitee-release-cli 的 multipart 形态
# ---------------------------------------------------------------------------


def _fake_response(payload: bytes):
    """构造一个最小可用 HTTPResponse 替身（支持 read/close/__enter__/__exit__）。"""

    class _Resp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(payload)


def test_upload_puts_access_token_in_multipart_body_not_query(monkeypatch, tmp_path):
    """附件上传：access_token 必须作为 multipart body 的 form 字段，
    而非 URL query（官方 gitee-release-cli 的做法）。"""
    import ci_release_sync

    # cmd_sync_gitee 用相对路径 dist/VibeOCR-v{ver}-win64.zip 找附件，
    # 故切到 tmp_path 并在那里建 dist/
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    asset = dist / "VibeOCR-v9.9.9-win64.zip"
    asset.write_bytes(b"fake-zip-bytes")

    monkeypatch.setenv("GITEE_TOKEN", "secret-token-123")

    captured = {"requests": []}

    def fake_urlopen(req, timeout=None):
        captured["requests"].append(req)
        url = req.full_url
        # 创建 release 的 POST：路径以 /releases 结尾（可能带 query），
        # 不能用 access_token 判断，否则与被测的 bug 行为耦合。
        base = url.split("?", 1)[0]
        if base.endswith("/releases"):
            return _fake_response(b'{"id": 728999}')
        # 附件上传 —— 直接返回成功
        return _fake_response(b'{"name":"ok"}')

    monkeypatch.setattr(ci_release_sync.urllib.request, "urlopen", fake_urlopen)
    # request_with_retry 用 hasattr(req, "timeout") 取超时；fake_req 也要有 timeout
    monkeypatch.setattr(ci_release_sync.time, "sleep", lambda *_: None)

    args = type("A", (), {"version": "9.9.9", "notes": "release_notes.md"})()
    rc = ci_release_sync.cmd_sync_gitee(args)

    assert rc == 0  # 按设计不阻断
    upload_reqs = [r for r in captured["requests"] if "attach_files" in r.full_url]
    assert len(upload_reqs) == 1, f"应有一次上传请求，实际 {len(upload_reqs)}"

    req = upload_reqs[0]
    # (a) URL 里绝不能带 access_token query（token 须进 multipart body）
    assert "access_token=" not in req.full_url, "access_token 不应在 URL query 中"
    # (b) body 里要有 access_token 表单字段
    body = req.data
    assert b'name="access_token"' in body, "multipart 须含 access_token 字段"
    assert b"secret-token-123" in body, "token 值须在 body 中"
    # (c) body 里要有 file 字段（filename 靠 Content-Disposition 带）
    assert b'name="file"' in body, "multipart 须含 file 字段"
    assert b"VibeOCR-v9.9.9-win64.zip" in body, "filename 须在 Content-Disposition 中"
    # (d) 不能有独立的 name="name" 字段（官方 cli 不发这个字段）
    assert b'name="name"' not in body, "不应有独立 name 字段"


def test_request_with_retry_does_not_retry_on_http_error(monkeypatch, tmp_path):
    """HTTPError（401/403/500 等）是确定性失败，不该被当网络错误重试 3 次。"""
    import urllib.error

    import ci_release_sync

    monkeypatch.setenv("GITEE_TOKEN", "secret-token-123")
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    asset = dist / "VibeOCR-v9.9.9-win64.zip"
    asset.write_bytes(b"fake-zip-bytes")

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        base = req.full_url.split("?", 1)[0]
        if base.endswith("/releases"):
            return _fake_response(b'{"id": 728999}')
        # 上传端点一律返回 401
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"message":"unauth"}')
        )

    monkeypatch.setattr(ci_release_sync.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ci_release_sync.time, "sleep", lambda *_: None)

    args = type("A", (), {"version": "9.9.9", "notes": "release_notes.md"})()
    rc = ci_release_sync.cmd_sync_gitee(args)

    assert rc == 0  # 不阻断
    # 创建 release（POST /releases）1 次 + 上传（attach_files）1 次 = 2 次；
    # HTTPError 是确定性失败，不应重试，否则会是 1 + 3 = 4 次（含 sleep）。
    assert call_count["n"] == 2, (
        f"创建 1 次 + 上传 1 次 = 2 次；HTTPError 不重试。实际 {call_count['n']}"
    )
