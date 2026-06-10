"""R13 — Cluster A (English mode) + Cluster B (constraint-only follow-ups).

Repro sequences come from the 2026-06-09 live-probe review (63 probes against
prod). Layer 1 asserts the pure state (no retrieval); the env-gated layer 2
runs the real top_k pipeline like test_context_contamination does.

  A: English queries extracted NO category/sub_category hard filter (the
     keyword tables are Chinese), so a price-only WHERE returned cheap
     unrelated cards and the reply claimed the catalog had no such product.
  B: follow-up turns carrying ONLY a constraint ("1000以内" / "要降噪的" /
     "休闲款1000以内" / "苹果的呢" / "any cheaper ones?") weren't recognized
     as follow-ups, so retrieval got a context-free fragment; "苹果的呢" was
     additionally mis-read as a topic switch, dropping the whole filter.
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
from app.services.price_intent import parse_price_intent
from rag.retrieve.constraints import build_retrieval_filter

_E2E_ENABLED = os.getenv("LIONPICK_E2E_TESTS") == "1"
_SKIP_E2E_MSG = "set LIONPICK_E2E_TESTS=1 to run end-to-end retrieval tests"

_HEADPHONE_SUBS = {"无线降噪耳机", "真无线耳机", "真无线降噪耳机"}


def _turn(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def _history(*pairs: tuple[str, str]) -> list[ChatMessage]:
    return [_turn(role, text) for role, text in pairs]


def _run_top_k(messages: list[ChatMessage]) -> list[dict]:
    """Mirror the chat.py call sequence (same as test_context_contamination)."""
    from app.services.rag_client import top_k
    from rag.retrieve.english_terms import augment_english_query

    inherited = build_conversation_filter(messages, None)
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    raw_text = last_user.content if last_user and isinstance(last_user.content, str) else ""
    retrieval_query = augment_english_query(build_retrieval_query(messages), raw_text)
    return top_k(
        retrieval_query,
        k=5,
        filters=None,
        conversation_filter=inherited,
        intent_text=raw_text,
    )


# ---------------------------------------------------------------------------
# Cluster A — English hard-filter extraction + price intent
# ---------------------------------------------------------------------------

class EnglishFilterExtraction(unittest.TestCase):
    def test_english_headphones_under_1000(self) -> None:
        f = build_retrieval_filter("noise cancelling headphones under 1000")
        self.assertIsNotNone(f)
        self.assertEqual(f.category, "数码电子")
        self.assertTrue(set(f.sub_categories or []) & _HEADPHONE_SUBS, f.sub_categories)
        self.assertEqual(f.price_max_cny, 1000)

    def test_english_laptop_under_7000(self) -> None:
        f = build_retrieval_filter("laptop for programming under 7000")
        self.assertIsNotNone(f)
        self.assertEqual(f.sub_categories, ["笔记本电脑"])
        self.assertEqual(f.price_max_cny, 7000)

    def test_chinese_unaffected_by_hint_table(self) -> None:
        f = build_retrieval_filter("1000以内的降噪耳机")
        self.assertEqual(f.category, "数码电子")
        self.assertEqual(f.price_max_cny, 1000)

    def test_english_price_direction(self) -> None:
        self.assertEqual(parse_price_intent("any cheaper ones?").direction, "cheap")
        self.assertEqual(parse_price_intent("something under 800").price_max, 800)


class EnglishFollowupRewrite(unittest.TestCase):
    # ¥7000 keeps real matches in the catalog (cheapest phone is ¥3299), so
    # the e2e layer can assert actual phone cards; the no-match budget case
    # is covered by test_english_headphones_under_1000_no_garbage.
    HISTORY = _history(
        ("user", "recommend a smartphone under 7000"),
        ("assistant", "Here are some options..."),
        ("user", "any cheaper ones?"),
    )

    def test_english_followup_inherits_anchor(self) -> None:
        q = build_retrieval_query(self.HISTORY)
        self.assertIn("smartphone", q)
        self.assertIn("cheaper", q)

    def test_english_followup_inherits_filter(self) -> None:
        inh = build_conversation_filter(self.HISTORY, None)
        self.assertEqual(inh.category, "数码电子")
        self.assertEqual(inh.sub_categories, ["智能手机"])

    def test_english_single_turn_not_treated_as_followup(self) -> None:
        q = build_retrieval_query(
            _history(("user", "noise cancelling headphones under 1000"))
        )
        self.assertEqual(q, "noise cancelling headphones under 1000")


# ---------------------------------------------------------------------------
# Cluster B — constraint-only follow-ups inherit the prior category
# ---------------------------------------------------------------------------

class BudgetOnlyFollowup(unittest.TestCase):
    HISTORY = _history(
        ("user", "推荐个耳机"),
        ("assistant", "..."),
        ("user", "要降噪的"),
        ("assistant", "..."),
        ("user", "1000以内"),
    )

    def test_rewrite_keeps_category(self) -> None:
        q = build_retrieval_query(self.HISTORY)
        self.assertIn("耳机", q)
        self.assertIn("1000以内", q)

    def test_state_keeps_category_and_budget(self) -> None:
        inh = build_conversation_filter(self.HISTORY, None)
        self.assertEqual(inh.category, "数码电子")
        self.assertTrue(set(inh.sub_categories or []) & _HEADPHONE_SUBS)
        self.assertEqual(inh.price_max_cny, 1000)

    def test_self_contained_budget_query_unchanged(self) -> None:
        q = build_retrieval_query(_history(("user", "1000以内的耳机")))
        self.assertEqual(q, "1000以内的耳机")


class AttributeFollowup(unittest.TestCase):
    HISTORY = _history(
        ("user", "帮我挑双球鞋"),
        ("assistant", "..."),
        ("user", "休闲款1000以内"),
    )

    def test_rewrite_keeps_category(self) -> None:
        q = build_retrieval_query(self.HISTORY)
        self.assertIn("球鞋", q)
        self.assertIn("休闲款", q)

    def test_sneaker_term_maps_to_shoe_subcategories(self) -> None:
        inh = build_conversation_filter(self.HISTORY, None)
        self.assertTrue(
            set(inh.sub_categories or []) & {"篮球鞋", "跑步鞋", "运动休闲鞋"},
            inh.sub_categories,
        )
        self.assertEqual(inh.price_max_cny, 1000)


class BrandRefinementFollowup(unittest.TestCase):
    HISTORY = _history(
        ("user", "推荐个笔记本"),
        ("assistant", "..."),
        ("user", "苹果的呢"),
    )

    def test_rewrite_keeps_category(self) -> None:
        q = build_retrieval_query(self.HISTORY)
        self.assertIn("笔记本", q)

    def test_state_keeps_subcategory_and_adds_brand(self) -> None:
        inh = build_conversation_filter(self.HISTORY, None)
        self.assertEqual(inh.sub_categories, ["笔记本电脑"])
        self.assertTrue(
            any("苹果" in b or "apple" in b.lower() for b in (inh.brand_include or [])),
            inh.brand_include,
        )


# ---------------------------------------------------------------------------
# Layer 2 — end-to-end retrieval (env-gated, mirrors the live probes)
# ---------------------------------------------------------------------------

class EndToEndRepros(unittest.TestCase):
    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_headphone_budget_followup_returns_no_tshirts(self) -> None:
        products = _run_top_k(BudgetOnlyFollowup.HISTORY)
        titles = [p.get("title", "") for p in products]
        print(f"\n[B1 耳机→降噪→1000以内] {titles}")
        # The budget excludes every headphone; chat.py's fallback re-fetches the
        # category without the price. At THIS layer the contract is: nothing
        # off-category. (Empty is acceptable — chat.py then relaxes the budget.)
        for t in titles:
            self.assertFalse(
                any(tok in t for tok in ("T恤", "短裤", "帽", "酸奶", "牛奶")),
                f"off-category card leaked: {t}",
            )

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_sneaker_followup_returns_shoes(self) -> None:
        products = _run_top_k(AttributeFollowup.HISTORY)
        titles = [p.get("title", "") for p in products]
        print(f"\n[B2 球鞋→休闲款1000以内] {titles}")
        self.assertGreater(len(products), 0)
        shoe_hits = [t for t in titles if "鞋" in t]
        self.assertEqual(len(shoe_hits), len(titles), f"non-shoe cards: {titles}")

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_apple_refinement_returns_macbooks(self) -> None:
        products = _run_top_k(BrandRefinementFollowup.HISTORY)
        titles = [p.get("title", "") for p in products]
        print(f"\n[B3 笔记本→苹果的呢] {titles}")
        self.assertGreater(len(products), 0)
        for t in titles:
            self.assertIn("MacBook", t, f"non-MacBook card: {titles}")

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_english_laptop_under_7000_returns_laptops(self) -> None:
        products = _run_top_k(
            _history(("user", "laptop for programming under 7000"))
        )
        titles = [p.get("title", "") for p in products]
        print(f"\n[A2 laptop under 7000] {titles}")
        self.assertGreater(len(products), 0)
        for t in titles:
            self.assertTrue(
                ("笔记本" in t) or ("Book" in t),
                f"non-laptop card: {titles}",
            )

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_english_headphones_under_1000_no_garbage(self) -> None:
        products = _run_top_k(
            _history(("user", "noise cancelling headphones under 1000"))
        )
        titles = [p.get("title", "") for p in products]
        print(f"\n[A1 headphones under 1000] {titles}")
        # No headphone is ≤¥1000, so empty is correct here (chat.py's fallback
        # then relaxes the budget). What must NOT happen is yogurt/T-shirt cards.
        for t in titles:
            self.assertTrue(
                any(tok in t for tok in ("耳机", "AirPods", "Bose", "索尼", "FreeBuds")),
                f"off-category card leaked: {t}",
            )

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_english_cheaper_followup_stays_on_phones(self) -> None:
        products = _run_top_k(EnglishFollowupRewrite.HISTORY)
        titles = [p.get("title", "") for p in products]
        print(f"\n[A3 smartphone→cheaper] {titles}")
        self.assertGreater(len(products), 0)
        phone_hits = [t for t in titles if any(tok in t for tok in ("手机", "iPhone", "Phone"))]
        self.assertEqual(len(phone_hits), len(titles), f"non-phone cards: {titles}")
        # "cheaper" must sort ascending — the live probe got a PRICIER 折叠屏.
        prices = [float(p.get("price_cny") or p.get("base_price")) for p in products]
        self.assertEqual(prices, sorted(prices), f"not cheap-first: {prices}")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# R13 — workflow-audit fixes (2026-06-09 200-probe sweep): marker-first budget
# phrasing, "lining" alias collision, 买辆车 out-of-domain gap.
# ---------------------------------------------------------------------------

class WorkflowAuditFixes(unittest.TestCase):
    def test_marker_first_budget_extracts_price_cap(self) -> None:
        self.assertEqual(build_retrieval_filter("耳机不超过1800").price_max_cny, 1800)
        self.assertEqual(build_retrieval_filter("不要超过300的口红").price_max_cny, 300)
        self.assertEqual(build_retrieval_filter("最多500块的跑鞋").price_max_cny, 500)
        intent = parse_price_intent("不超过300")
        self.assertEqual(intent.price_max, 300)
        self.assertEqual(intent.direction, "cheap")

    def test_lining_noun_pins_no_brand(self) -> None:
        f = build_retrieval_filter("a hoodie with fleece lining")
        self.assertFalse(f and f.brand_include, f and f.brand_include)
        # The brand itself must still be reachable.
        f2 = build_retrieval_filter("李宁篮球鞋")
        self.assertTrue(any("李宁" in b for b in (f2.brand_include or [])))
        f3 = build_retrieval_filter("li-ning basketball shoes")
        self.assertTrue(any("李宁" in b for b in (f3.brand_include or [])))

    def test_buy_a_car_is_out_of_domain(self) -> None:
        from app.routes.chat import _is_out_of_domain

        self.assertTrue(_is_out_of_domain("我想买辆车"))
        self.assertTrue(_is_out_of_domain("我想买车"))
        self.assertFalse(_is_out_of_domain("买个车载手机支架"))


# ---------------------------------------------------------------------------
# R13 — Cluster C (card noise / relevance gate) + Cluster D (exclusion wording)
# ---------------------------------------------------------------------------

class ClusterDNegationStripper(unittest.TestCase):
    def test_strips_exclusion_clause(self) -> None:
        from app.routes.chat import _strip_negation

        self.assertEqual(_strip_negation("推荐咖啡，不要速溶的"), "推荐咖啡")
        self.assertEqual(_strip_negation("推荐耳机 不要索尼的"), "推荐耳机")
        self.assertEqual(_strip_negation("coffee without instant ones"), "coffee")

    def test_keeps_budget_phrase(self) -> None:
        from app.routes.chat import _strip_negation

        # 不要超过300 is a PRICE bound, not an exclusion — must survive.
        self.assertEqual(_strip_negation("耳机不要超过300"), "耳机不要超过300")


class ClusterCRelevanceGate(unittest.TestCase):
    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_nonexistent_category_returns_empty(self) -> None:
        from app.services.rag_client import top_k

        for q in ("电饭煲", "香薰", "recommend a rice cooker"):
            products = top_k(q, k=5)
            titles = [p.get("title", "") for p in products]
            print(f"\n[C no-match {q!r}] {titles}")
            self.assertEqual(products, [], f"junk cards leaked for {q!r}: {titles}")

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_aisle_query_never_leaks_cross_aisle(self) -> None:
        from app.services.rag_client import top_k

        # "家居香薰" pins the 家居家具 aisle: same-aisle substitutes are an
        # acceptable "closest match" answer, but the original bug — a
        # cross-aisle skincare gift set riding in via a bad sub_category —
        # must stay dead.
        products = top_k("推荐个家居香薰", k=5)
        cats = {p.get("category") for p in products}
        print(f"\n[C 家居香薰 aisles] {cats}")
        self.assertLessEqual(cats, {"家居家具"}, f"cross-aisle leak: {cats}")

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_oxygen_machine_has_no_filler_cards(self) -> None:
        from app.services.rag_client import top_k

        products = top_k("医用制氧机", k=5)
        titles = [p.get("title", "") for p in products]
        print(f"\n[C 制氧机] {titles}")
        self.assertGreater(len(products), 0)
        for t in titles:
            self.assertIn("制氧机", t, f"filler card alongside 制氧机: {titles}")

    @unittest.skipUnless(_E2E_ENABLED, _SKIP_E2E_MSG)
    def test_scene_query_exempt_from_gate(self) -> None:
        from app.services.rag_client import top_k

        # 场景搭配 queries score low by nature (top ~0.046 for this one) and
        # must NOT be no-matched when chat.py disables the gate for them.
        products = top_k("三亚度假要准备什么", k=5, relevance_gate=False)
        print(f"\n[C scene exempt] {[p.get('title','')[:30] for p in products]}")
        self.assertGreater(len(products), 0)


class ClusterDNegatedBrandList(unittest.TestCase):
    def test_enumerated_negated_brands_all_excluded(self) -> None:
        f = build_retrieval_filter("推荐耳机，不要索尼的、Bose的、苹果的、华为的")
        excluded = "、".join(f.brand_exclude or [])
        for b in ("索尼", "Sony"):
            self.assertTrue(b in excluded or "Sony" in excluded, excluded)
        self.assertIn("Bose", excluded)
        self.assertTrue("苹果" in excluded or "Apple" in excluded, excluded)
        self.assertTrue("华为" in excluded, excluded)
        # And none of them may leak into brand_include.
        included = "、".join(f.brand_include or [])
        for tok in ("Sony", "Bose", "Apple", "苹果", "华为"):
            self.assertNotIn(tok, included)

    def test_positive_object_after_negated_modifier_survives(self) -> None:
        # "不要苹果的耳机" — 耳机 is the POSITIVE object; only Apple is excluded.
        f = build_retrieval_filter("耳机不要苹果的")
        self.assertTrue(any("苹果" in b or "Apple" in b for b in (f.brand_exclude or [])))
        self.assertEqual(f.category, "数码电子")

    def test_positive_brand_after_list_negation_not_excluded(self) -> None:
        # "不要苹果的、要华为的" — Apple excluded, Huawei POSITIVELY wanted.
        f = build_retrieval_filter("耳机不要苹果的、要华为的")
        self.assertTrue(any("华为" in b for b in (f.brand_include or [])), f.brand_include)
        self.assertFalse(any("华为" in b for b in (f.brand_exclude or [])), f.brand_exclude)
