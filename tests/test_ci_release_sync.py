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
        _gh_release("v0.10.0"),   # 应排在 0.3.0 之后（语义版本，非字典序）
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
