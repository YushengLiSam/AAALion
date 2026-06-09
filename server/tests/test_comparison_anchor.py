"""Regression test: the product-line anchor filter must NOT strip the other
brand in a comparison.

"iPhone和小米哪个好" / "小米手机和iphone的对比" used to return only Apple, because
the 'iphone' product-line anchor dropped every non-iPhone candidate — so the
LLM said "目录里没有小米". The anchor filter is now skipped for comparison /
multi-brand queries (verified end-to-end against the live index separately).
This pins the discriminator that drives that skip.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for root in (REPO_ROOT, REPO_ROOT / "server"):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest

from app.services.rag_client import _COMPARISON_RE


@pytest.mark.parametrize("text", [
    "iPhone和小米哪个好",
    "小米手机和iphone的对比",
    "对比一下小米和苹果手机",
    "小米手机怎么样,和华为比呢",
    "华为和苹果和小米三个对比",
    "这两款手机哪个更好",
])
def test_comparison_detected(text):
    assert _COMPARISON_RE.search(text)


@pytest.mark.parametrize("text", [
    "推荐iphone",
    "给我一个iphone手机",
    "iphone13多少钱",
    "推荐降噪耳机",
])
def test_single_lookup_not_comparison(text):
    # These must STILL get the anchor filter (iPhone13→iPad fix preserved).
    assert not _COMPARISON_RE.search(text)
