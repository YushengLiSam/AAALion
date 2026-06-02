"""Phase 0 of the multi-turn context-contamination diagnostic plan.

These tests fix the SYMPTOMS in code so we can stop reproducing them
by hand. They deliberately do NOT prescribe a fix — Phase 4 wires the
chosen-by-user solution after Phase 2-3 establish root cause and pick
a direction.

Each test asserts on TWO layers:

  (1) The inherited `conversation_filter` after multi-turn rewriting.
      This is what `build_conversation_filter` produces — pure-function,
      deterministic, fast.

  (2) The actual products returned by `top_k` given that filter +
      raw new query. This is end-to-end through Path A/B + hybrid +
      rerank + product-line-anchor filter. Slower (needs Chroma +
      BM25 + cross-encoder), but it's what the LLM actually sees.

Layer (1) tells us "what state was inherited"; layer (2) tells us
"did Path A/B successfully reset it before retrieval ran."

`@unittest.skipUnless` keeps slow e2e tests opt-in via env var
LIONPICK_E2E_TESTS=1; otherwise only the pure-state layer runs.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.schemas.chat import ChatMessage
from app.services.constraint_state import build_conversation_filter
from app.services.contextual_query import build_retrieval_query
from rag.retrieve.query import Filter


def _turn(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def _history(*pairs: tuple[str, str]) -> list[ChatMessage]:
    """Build a chat history from (role, text) tuples."""
    return [_turn(role, text) for role, text in pairs]


def _run_top_k(messages: list[ChatMessage]) -> tuple[Filter | None, list[dict]]:
    """Mirror the chat.py call sequence to top_k. Returns
    (inherited_filter, products) so tests can see both layers.

    Does NOT need a live FastAPI server — calls the function directly.
    """
    from app.services.rag_client import top_k

    inherited = build_conversation_filter(messages, None)
    retrieval_query = build_retrieval_query(messages)
    last_user = next(
        (m for m in reversed(messages) if m.role == "user"), None
    )
    raw_text = last_user.content if last_user and isinstance(last_user.content, str) else ""
    products = top_k(
        retrieval_query,
        k=5,
        filters=None,
        conversation_filter=inherited,
        intent_text=raw_text,
    )
    return inherited, products


# Skip e2e (retrieval-required) tests by default — they need Chroma +
# bge-* + cross-encoder + ~3-15s per case. Set LIONPICK_E2E_TESTS=1 to run.
_E2E_ENABLED = os.getenv("LIONPICK_E2E_TESTS") == "1"
_SKIP_E2E_MSG = "set LIONPICK_E2E_TESTS=1 to run end-to-end retrieval tests"


# ---------------------------------------------------------------------------
# Case A — Category contamination
# ---------------------------------------------------------------------------

class CaseACategoryContamination(unittest.TestCase):
    """Skincare turn → 'iPhone 12' next turn. The inherited skincare
    category should not survive into the iPhone request."""

    HISTORY = _history(
        ("user", "推荐适合敏感肌的洁面"),
        ("assistant", "为您推荐薇诺娜舒敏..."),
        ("user", "I want a iPhone 12"),
    )

    def test_inherited_state_after_history(self) -> None:
        """LAYER 1: what does build_conversation_filter inherit?
        Documents the contaminated input that downstream sees."""
        inh = build_conversation_filter(self.HISTORY, None)
        print(
            f"\n[CaseA inherited] category={inh.category!r} "
            f"sub={inh.sub_category!r} brands={inh.brand_include!r} "
            f"excl_keywords={inh.exclude_keywords!r} "
            f"price_max_cny={inh.price_max_cny!r}"
        )

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_no_skincare_in_iphone_results(self) -> None:
        """LAYER 2: after Path A/B reset, retrieval must not give the
        LLM a catalog block that's still skincare."""
        inherited, products = _run_top_k(self.HISTORY)
        titles = [p.get("title", "") for p in products]
        print(f"\n[CaseA products] inherited.category={inherited.category!r} "
              f"brand_include={inherited.brand_include!r}")
        for t in titles:
            print(f"  - {t[:70]}")
        skincare_tokens = ("洁面", "面霜", "精华", "护肤", "化妆", "爽肤")
        polluted = [t for t in titles if any(tok in t for tok in skincare_tokens)]
        self.assertEqual(
            polluted,
            [],
            f"skincare contamination: {polluted}",
        )


# ---------------------------------------------------------------------------
# Case B — Brand contamination
# ---------------------------------------------------------------------------

class CaseBBrandContamination(unittest.TestCase):
    """Multi-turn iPad/iPhone history → 'iPad turn(s)' pin brand=Apple
    into conversation_filter. The next 护肤品 query carries a CATEGORY
    signal (美妆护肤) but no BRAND signal. Old Path B (category-only
    check) failed to reset, so inherited brand=Apple keeps narrowing
    retrieval to 0 results in 美妆护肤 — there are no Apple skincare
    products."""

    HISTORY = _history(
        ("user", "推荐 iPad"),
        ("assistant", "为您推荐 Apple iPad Air..."),
        ("user", "再来一个 iPad Pro"),
        ("assistant", "Apple iPad Pro 13..."),
        ("user", "护肤品"),
    )

    def test_inherited_state_after_history(self) -> None:
        inh = build_conversation_filter(self.HISTORY, None)
        print(
            f"\n[CaseB inherited] category={inh.category!r} "
            f"sub={inh.sub_category!r} brands={inh.brand_include!r} "
            f"excl_keywords={inh.exclude_keywords!r}"
        )

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_skincare_results_not_blocked_by_apple_brand(self) -> None:
        """The brand_include = ['Apple'] should be dropped on this topic
        switch so the 美妆护肤 query actually returns skincare."""
        inherited, products = _run_top_k(self.HISTORY)
        titles = [p.get("title", "") for p in products]
        print(f"\n[CaseB products] inherited.brand_include={inherited.brand_include!r}")
        for t in titles:
            print(f"  - {t[:70]}")
        self.assertGreater(len(products), 0, "0 results = inherited Apple brand wiped catalog")
        skincare_tokens = ("洁面", "面霜", "精华", "护肤", "化妆", "爽肤", "防晒", "肤")
        skincare_hits = [t for t in titles if any(tok in t for tok in skincare_tokens)]
        self.assertGreater(
            len(skincare_hits), 0,
            f"expected skincare titles, got: {titles}",
        )


# ---------------------------------------------------------------------------
# Case C — Cumulative contamination across many topic switches
# ---------------------------------------------------------------------------

class CaseCCumulativeContamination(unittest.TestCase):
    """The user reported: first time '护肤品' / '鞋子' / '纸尿片' return
    products; after several other turns, the same queries return 0.
    Each turn should reflect only the CURRENT query, not accumulate."""

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_each_turn_reflects_current_query_only(self) -> None:
        """Run a 5-turn conversation switching topic each time, asserting
        EACH turn's output contains products relevant to THAT turn only."""
        # Each tuple is (user_query, expected_category_token_in_titles).
        sequence = [
            ("推荐适合敏感肌的洁面", ("洁面", "面霜", "肤", "护理")),
            ("推荐 iPad", ("iPad", "ipad")),
            ("我想要 鞋子", ("鞋", "Nike", "耐克", "阿迪", "安踏", "特步")),
            ("纸尿片", ("帮宝适", "Pampers", "纸尿", "尿", "婴儿", "母婴")),
            ("护肤品", ("精华", "面霜", "肤", "护肤", "化妆")),
        ]
        history: list[ChatMessage] = []
        for query, expected_tokens in sequence:
            history.append(_turn("user", query))
            inherited, products = _run_top_k(history)
            titles = [p.get("title", "") for p in products]
            print(f"\n[CaseC turn={query!r}]")
            print(f"  inherited.category={inherited.category!r}")
            print(f"  inherited.sub_category={inherited.sub_category!r}")
            print(f"  inherited.sub_categories={inherited.sub_categories!r}")
            print(f"  inherited.brand_include={inherited.brand_include!r}")
            print(f"  inherited.brand_exclude={inherited.brand_exclude!r}")
            print(f"  inherited.exclude_keywords={inherited.exclude_keywords!r}")
            print(f"  inherited.price_max_cny={inherited.price_max_cny!r}")
            print(f"  n_products={len(products)}")
            for t in titles[:3]:
                print(f"  - {t[:70]}")
            hits = [t for t in titles if any(tok in t for tok in expected_tokens)]
            self.assertGreater(
                len(hits),
                0,
                f"turn {query!r}: cumulative pollution drained results. "
                f"Expected any of {expected_tokens}, got {titles}",
            )
            # Simulate an assistant reply so the rolling history shape is
            # realistic (chat.py appends both sides to req.messages).
            history.append(_turn("assistant", "（mock response）"))


# ---------------------------------------------------------------------------
# Invariant test — legitimate inheritance must still work
# ---------------------------------------------------------------------------

class InvariantLegitimateInheritance(unittest.TestCase):
    """The whole point of multi-turn state is that follow-up queries
    LIKE 're cheaper' or '颜色' CAN inherit the previous turn's
    category/sub_category. These tests must keep passing after the
    fix — they prove the topic-switch detector doesn't false-positive."""

    def test_followup_price_keeps_category(self) -> None:
        """'再便宜点的' has no category signal — must inherit."""
        inh = build_conversation_filter(
            _history(
                ("user", "推荐 Sony 降噪耳机"),
                ("assistant", "..."),
                ("user", "再便宜点的"),
            ),
            None,
        )
        self.assertEqual(inh.category, "数码电子")

    def test_followup_after_iphone_keeps_iphone(self) -> None:
        """iPhone → 再便宜点 should keep iPhone family inheritance."""
        inh = build_conversation_filter(
            _history(
                ("user", "推荐 iPhone"),
                ("assistant", "..."),
                ("user", "再便宜点的"),
            ),
            None,
        )
        self.assertEqual(inh.category, "数码电子")


if __name__ == "__main__":
    unittest.main()
