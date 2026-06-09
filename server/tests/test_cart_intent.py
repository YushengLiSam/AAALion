"""Regression tests for conversational cart-intent detection (chat route).

The R12 short-circuit makes a cart-intent match skip retrieval AND the LLM, so a
false positive silently swallows a real product search. These cases pin the
misfires found in the pre-freeze audit:
  * "买单反相机" must NOT be read as the "买单" (pay/checkout) verb.
  * A negated checkout ("先不要下单") must NOT trigger checkout.
  * "不要第N个,换别的" (refine search results) must NOT be read as a cart
    delete — only "不要 + 序数" WITH explicit cart context is a delete.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest

from app.routes.chat import _detect_cart_intent


def _action(text):
    got = _detect_cart_intent(text)
    return got.get("action") if got else None


@pytest.mark.parametrize("text", [
    "我想买单反相机",
    "双十一买单反划算吗",
    "买单反相机推荐",
])
def test_dslr_is_not_checkout(text):
    assert _action(text) is None


@pytest.mark.parametrize("text", [
    "先不要下单",
    "不下单了,再看看别的",
    "我不想结账了",
    "暂时不结算",
    "先别下单",
])
def test_negated_checkout_is_not_checkout(text):
    assert _action(text) is None


@pytest.mark.parametrize("text", [
    "不要第一个,换别的推荐",
    "不要第二个了,有没有别的",
])
def test_refine_results_is_not_cart_remove(text):
    assert _action(text) is None


@pytest.mark.parametrize("text,expected", [
    ("帮我下单", "checkout"),
    ("去结算", "checkout"),
    ("买单", "checkout"),
    ("别犹豫了,赶紧下单", "checkout"),
    ("我要下单", "checkout"),
    ("加入购物车", "add"),
    ("删掉第二个", "remove"),
    ("把购物车里的第一个删掉", "remove"),
    ("不要购物车里的第二个", "remove"),
    ("清空购物车", "clear"),
    ("把数量改成2", "set_quantity"),
])
def test_legit_cart_ops_still_fire(text, expected):
    assert _action(text) == expected
