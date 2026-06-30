"""ci_release_sync 纯函数测试（版本排序与清理选择）"""

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
# cmd_sync_gitee 附件上传 —— 流式 http.client 分块 send + 进度 + 墙钟超时
# ---------------------------------------------------------------------------


def _fake_response(payload: bytes):
    """构造一个最小可用 HTTPResponse 替身（支持 read/close/__enter__/__exit__）。
    用于创建/复用 Release 的 urllib 路径（cmd_sync_gitee 第 1 步）。"""

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


class _FakeConn:
    """http.client.HTTPSConnection 替身：记录所有 send 的字节与调用次数，
    getresponse 返回可配置状态码/体。send_failures 控制第几次 send 抛异常。"""

    def __init__(self, host, timeout=None, *, status=200, body=b'{"name":"ok"}',
                 send_failures=0):
        self.host = host
        self.sent_chunks = []          # 每次 send 的参数列表（按顺序）
        self.calls = {"putrequest": [], "putheader": [], "endheaders": 0}
        self._status = status
        self._body = body
        self._send_failures = send_failures  # 前 N 次 send 抛 ConnectionError

    def putrequest(self, method, path):
        self.calls["putrequest"].append((method, path))

    def putheader(self, key, value):
        self.calls["putheader"].append((key, value))

    def endheaders(self):
        self.calls["endheaders"] += 1

    def send(self, data):
        if self._send_failures > 0:
            self._send_failures -= 1
            raise ConnectionError("simulated send failure")
        self.sent_chunks.append(data)

    def getresponse(self):
        return _FakeHTTPResponse(self._status, self._body)

    def close(self):
        pass

    @property
    def sent_all(self) -> bytes:
        return b"".join(self.sent_chunks)


class _FakeHTTPResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body


def _setup_gitee_sync_env(monkeypatch, tmp_path, *, token="secret-token-123"):
    """公共脚手架：建 dist + 测试 zip + mock 创建 Release 的 urllib 路径。"""
    import ci_release_sync

    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    asset = dist / "VibeOCR-v9.9.9-win64.zip"
    asset.write_bytes(b"fake-zip-bytes")
    (dist / "VibeOCR-v9.9.9-win64.zip.sha256").write_bytes(b"aaa  VibeOCR-v9.9.9-win64.zip\n")
    monkeypatch.setenv("GITEE_TOKEN", token)
    monkeypatch.setattr(ci_release_sync.time, "sleep", lambda *_: None)

    def fake_urlopen(req, timeout=None):
        base = req.full_url.split("?", 1)[0]
        if base.endswith("/releases"):
            return _fake_response(b'{"id": 728999}')
        return _fake_response(b'{"name":"ok"}')

    monkeypatch.setattr(ci_release_sync.urllib.request, "urlopen", fake_urlopen)


def test_upload_puts_access_token_in_multipart_body_not_query(monkeypatch, tmp_path):
    """附件上传：access_token 必须作为 multipart body 的 form 字段，
    而非 URL query（官方 gitee-release-cli 的做法）。"""
    import ci_release_sync

    _setup_gitee_sync_env(monkeypatch, tmp_path)

    conn = _FakeConn("gitee.com", status=200, body=b'{"name":"ok"}')
    monkeypatch.setattr(ci_release_sync.http.client, "HTTPSConnection",
                        lambda *a, **kw: conn)

    args = type("A", (), {"version": "9.9.9", "notes": "release_notes.md"})()
    rc = ci_release_sync.cmd_sync_gitee(args)
    assert rc == 0

    # putrequest 的 path 绝不能带 access_token（token 须进 multipart body）
    method, path = conn.calls["putrequest"][0]
    assert method == "POST"
    assert "attach_files" in path
    assert "access_token=" not in path, "access_token 不应在 URL path 中"

    body = conn.sent_all
    assert b'name="access_token"' in body, "multipart 须含 access_token 字段"
    assert b"secret-token-123" in body, "token 值须在 body 中"
    assert b'name="file"' in body, "multipart 须含 file 字段"
    assert b"VibeOCR-v9.9.9-win64.zip" in body, "filename 须在 Content-Disposition 中"
    assert b'name="name"' not in body, "不应有独立 name 字段"


def test_http_error_not_retried(monkeypatch, tmp_path):
    """HTTP 非 2xx（如 401）是确定性失败，upload_asset_streaming 抛 RuntimeError，
    cmd_sync_gitee 不重试，记 ::error:: 不阻断。"""
    import ci_release_sync

    _setup_gitee_sync_env(monkeypatch, tmp_path)

    conn = _FakeConn("gitee.com", status=401, body=b'{"message":"unauth"}')
    monkeypatch.setattr(ci_release_sync.http.client, "HTTPSConnection",
                        lambda *a, **kw: conn)

    args = type("A", (), {"version": "9.9.9", "notes": "release_notes.md"})()
    rc = ci_release_sync.cmd_sync_gitee(args)
    assert rc == 0  # 不阻断

    # 2 个附件各建连 1 次（zip + sha256），HTTP 错误未触发重试 → endheaders == 2
    # 若重试，每个附件会 >1 次 endheaders。这里等于附件数即证明"HTTP 错误不重试"。
    assert conn.calls["endheaders"] == 2, (
        f"2 个附件各 1 次建连（HTTP 错误不重试）；实际 {conn.calls['endheaders']}"
    )


def test_upload_streaming_chunks_the_file(monkeypatch, tmp_path):
    """upload_asset_streaming 必须分块 send 文件体（验证流式，非一次性发全量）。"""
    import ci_release_sync

    _setup_gitee_sync_env(monkeypatch, tmp_path)
    # 用大文件强制多块：chunk_size 默认 1MB，写 2.5MB → 文件体至少 3 块
    asset = tmp_path / "dist" / "VibeOCR-v9.9.9-win64.zip"
    asset.write_bytes(b"x" * (2 * 1024 * 1024 + 500 * 1024))

    conn = _FakeConn("gitee.com", status=200, body=b'{"name":"ok"}')
    sent_counts = []

    def factory(*a, **kw):
        sent_counts.append(0)
        return conn

    # 包装 send 计数
    orig_send = conn.send

    def counting_send(data):
        sent_counts[-1] += 1
        return orig_send(data)

    conn.send = counting_send  # type: ignore
    monkeypatch.setattr(ci_release_sync.http.client, "HTTPSConnection", factory)

    args = type("A", (), {"version": "9.9.9", "notes": "release_notes.md"})()
    rc = ci_release_sync.cmd_sync_gitee(args)
    assert rc == 0

    # 文件体 2.5MB / 1MB = 3 块；再加 head + tail 两次 send = 至少 5 次 send
    assert sent_counts[0] >= 5, (
        f"应分块 send（head + ≥3 文件块 + tail），实际 {sent_counts[0]} 次"
    )


def test_upload_aborts_on_wall_clock_deadline(monkeypatch, tmp_path):
    """墙钟超时：deadline 已过时，upload_asset_streaming 抛 TimeoutError。

    直接测 upload_asset_streaming 单元层（传一个已过期的 deadline），
    避免与 cmd_sync_gitee 里的 deadline=time()+LIMIT 计算耦合。
    """
    import ci_release_sync

    asset = tmp_path / "big.bin"
    asset.write_bytes(b"x" * (5 * 1024 * 1024))  # 多块，确保进 send 循环

    conn = _FakeConn("gitee.com", status=200, body=b'{"name":"ok"}')

    # now 远大于 deadline(=1.0)：第一块 send 后立即判定超时
    monkeypatch.setattr(ci_release_sync.time, "time", lambda: 1_000_000_000.0)

    raised = False
    try:
        ci_release_sync.upload_asset_streaming(
            "gitee.com", "/attach", "tok", asset,
            deadline=1.0,  # 早已过期
            conn_factory=lambda *a, **kw: conn,
        )
    except TimeoutError:
        raised = True

    assert raised, "deadline 已过期应抛 TimeoutError"
    # 发了 head + 至少 1 个文件块后才超时（证明是在循环中而非发 head 前就抛）
    assert len(conn.sent_chunks) >= 2, (
        f"应在发送若干块后才超时；实际 send 次数 {len(conn.sent_chunks)}"
    )


def test_upload_retries_on_connection_error(monkeypatch, tmp_path):
    """send 抛 ConnectionError（瞬时网络错误）应重试；第二次成功则整体成功。"""
    import ci_release_sync

    _setup_gitee_sync_env(monkeypatch, tmp_path)
    # 只留 1 个附件，简化断言（避免多附件混淆 attempt 计数）
    (tmp_path / "dist" / "VibeOCR-v9.9.9-win64.zip.sha256").unlink()

    states = {"attempt": 0}

    def factory(*a, **kw):
        states["attempt"] += 1
        if states["attempt"] == 1:
            # 第一次：send 立即失败（send_failures 足够大，保证首次 send 就抛）
            return _FakeConn("gitee.com", status=200, send_failures=999)
        return _FakeConn("gitee.com", status=200, body=b'{"name":"ok"}')

    monkeypatch.setattr(ci_release_sync.http.client, "HTTPSConnection", factory)

    args = type("A", (), {"version": "9.9.9", "notes": "release_notes.md"})()
    rc = ci_release_sync.cmd_sync_gitee(args)
    assert rc == 0
    # 单附件：第 1 次建连失败 + 第 2 次重试成功 = 2 次
    assert states["attempt"] == 2, (
        f"单附件应重试 1 次后成功（2 次建连），实际 {states['attempt']}"
    )
