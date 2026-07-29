from __future__ import annotations

from vibeocr.classic.update_config import (
    GITHUB_API_LATEST,
    GITHUB_DOWNLOAD_BASE,
    GITHUB_REPO_BASE,
    build_asset_url_pairs,
    build_github_asset_urls,
)


def test_classic_update_discovery_targets_only_classic_releases() -> None:
    assert GITHUB_REPO_BASE == "https://github.com/FelixJI/vibeocr-classic"
    assert GITHUB_API_LATEST.endswith(
        "/repos/FelixJI/vibeocr-classic/releases/latest"
    )
    assert GITHUB_DOWNLOAD_BASE.endswith("/vibeocr-classic/releases/download")


def test_domestic_assets_preserve_proxy_then_direct_order() -> None:
    urls = build_github_asset_urls("domestic", "0.7.0", "classic.zip")
    assert urls == [
        "https://gh-proxy.com/"
        "https://github.com/FelixJI/vibeocr-classic/releases/download/"
        "v0.7.0/classic.zip",
        "https://ghproxy.com/"
        "https://github.com/FelixJI/vibeocr-classic/releases/download/"
        "v0.7.0/classic.zip",
        "https://github.com/FelixJI/vibeocr-classic/releases/download/"
        "v0.7.0/classic.zip",
    ]


def test_update_archive_and_checksum_stay_on_same_source() -> None:
    pairs = build_asset_url_pairs(
        "international",
        "0.7.0",
        "classic.zip",
        "classic.zip.sha256",
    )
    assert pairs == [
        (
            "https://github.com/FelixJI/vibeocr-classic/releases/download/"
            "v0.7.0/classic.zip",
            "https://github.com/FelixJI/vibeocr-classic/releases/download/"
            "v0.7.0/classic.zip.sha256",
        )
    ]
