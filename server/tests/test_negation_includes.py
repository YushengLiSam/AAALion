"""Regression tests for the negation-vs-positive-intent fixes (pre-freeze audit).

Covers four holes that returned 0 cards or the OPPOSITE of what the user asked:
  * "有没有X" (availability question) was read as "exclude X" because 没有 was a
    raw negation trigger — now gated by (?<!有).
  * "不想要/别要 X" was unknown to the downstream gates, so the brand the user
    rejected came back as a positive include — now in all gates.
  * A price-modifier negation ("不要超过五千的苹果手机") bled across 的 and
    excluded the positive brand — now 的 is a boundary.
  * A brand/category in BOTH include and exclude emptied the result — the
    reconcile guard makes positive intent win.

All pure-function level (no Chroma); the end-to-end behavior was verified
separately against the live index.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

# Force the deterministic local (no-LLM) negation path.
os.environ.pop("TOKENROUTER_API_KEY", None)

import pytest

from rag.retrieve.constraints import _brands
from rag.retrieve.negation import extract_negation
from rag.retrieve.query import Filter
from app.services.contextual_query import _reorder_negation_object
from app.services.rag_client import (
    _negation_signals,
    _reconcile_negation_with_includes,
)


def _has(brand_substr, brands):
    return any(brand_substr in b for b in brands)


# ── (没有) availability question is not an exclusion ──────────────────────────
def test_youmeiyou_is_not_exclusion():
    # "有没有华为" is an availability question; the co-occurring "不要太贵" opens
    # the extractor, but 华为 must NOT be swept into the exclusion set.
    text = "有没有华为手机,不要太贵的"
    assert extract_negation(text)["exclude_brands"] == []   # 华为 NOT excluded
    included, _ = _brands(text)
    assert _has("华为", included)                            # 华为 is a positive ask


def test_bare_meiyou_still_excludes():
    # A bare "没有X" (NOT preceded by 有) still excludes when the extractor is
    # open — here "不要" opens it and "没有苹果" should drop Apple.
    text = "不要太贵的,没有苹果的耳机"
    assert _has("苹果", extract_negation(text)["exclude_brands"])


# ── (不想要/别要) gates ───────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["不想要苹果的耳机", "别要苹果的耳机", "不需要苹果的耳机"])
def test_soft_negation_excludes_not_includes(raw):
    reordered = _reorder_negation_object(raw)        # → "耳机 不想要苹果"
    assert _negation_signals(reordered)
    included, excluded = _brands(reordered)
    assert _has("苹果", excluded) or _has("Apple", excluded)
    assert not _has("苹果", included)
    assert _has("苹果", extract_negation(reordered)["exclude_brands"])


# ── (的 boundary) price-negation must not swallow a positive brand ────────────
def test_price_negation_does_not_exclude_brand():
    included, excluded = _brands("不要超过五千的苹果手机")
    assert _has("苹果", included) or _has("Apple", included)
    assert not (_has("苹果", excluded) or _has("Apple", excluded))


# ── (reconcile) positive intent wins over a conflicting exclusion ─────────────
def _f(**kw):
    base = dict(category=None, sub_categories=None, brand_include=None,
                brand_exclude=None, price_max_cny=None, price_min_cny=None)
    base.update(kw)
    return Filter(**base)


def test_reconcile_drops_excluded_brand_that_is_included():
    neg = {"exclude_brands": ["华为"], "exclude_categories": [], "exclude_keywords": []}
    out = _reconcile_negation_with_includes(neg, _f(brand_include=["华为"]))
    assert out["exclude_brands"] == []


def test_reconcile_matches_aliases():
    neg = {"exclude_brands": ["Apple"], "exclude_categories": [], "exclude_keywords": []}
    out = _reconcile_negation_with_includes(neg, _f(brand_include=["苹果"]))
    assert out["exclude_brands"] == []  # Apple ≡ 苹果


def test_reconcile_drops_excluded_category_that_is_positive():
    neg = {"exclude_brands": [], "exclude_categories": ["数码电子"], "exclude_keywords": []}
    out = _reconcile_negation_with_includes(neg, _f(category="数码电子"))
    assert out["exclude_categories"] == []


def test_reconcile_keeps_unrelated_exclusion():
    neg = {"exclude_brands": ["小米"], "exclude_categories": [], "exclude_keywords": []}
    out = _reconcile_negation_with_includes(neg, _f(brand_include=["华为"]))
    assert out["exclude_brands"] == ["小米"]


# ── regression: genuine brand negations still work ───────────────────────────
@pytest.mark.parametrize("raw,brand", [("不要苹果的耳机", "苹果"), ("别要小米", "小米")])
def test_real_negations_unbroken(raw, brand):
    reordered = _reorder_negation_object(raw)
    _, excluded = _brands(reordered)
    assert _has(brand, excluded)
