from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.price_intent import apply_price_intent
from app.services.rag_client import top_k
from rag.retrieve.constraints import build_retrieval_filter
from rag.retrieve.query import Filter, _build_where, product_matches_filter


class RetrievalConstraintTests(unittest.TestCase):
    def test_parses_category_subcategory_brand_and_rmb_budget(self) -> None:
        result = build_retrieval_filter("200元以下的 Sony 蓝牙耳机有哪些")

        self.assertIsNotNone(result)
        self.assertEqual(result.category, "数码电子")
        self.assertIn("真无线耳机", result.sub_categories)
        self.assertEqual(result.brand_include, ["Sony"])
        self.assertEqual(result.price_max_cny, 200.0)

    def test_negative_brand_is_not_added_as_positive_constraint(self) -> None:
        result = build_retrieval_filter("不要 Apple 的笔记本电脑推荐")

        self.assertIsNone(result.brand_include)
        self.assertIn("Apple 苹果", result.brand_exclude)
        self.assertEqual(result.sub_categories, ["笔记本电脑"])

    def test_explicit_filter_fields_override_inferred_values(self) -> None:
        result = build_retrieval_filter(
            "推荐面霜",
            {"category": "数码电子", "sub_category": "平板电脑", "price_max": 3000, "include_brands": ["Apple 苹果"]},
        )

        self.assertEqual(result.category, "数码电子")
        self.assertEqual(result.sub_category, "平板电脑")
        self.assertEqual(result.price_max_cny, 3000.0)
        self.assertEqual(result.brand_include, ["Apple 苹果"])

    def test_retrieval_where_preserves_foreign_products_for_live_fx_check(self) -> None:
        where = _build_where(Filter(category="数码电子", price_max_cny=200))

        self.assertIn("$and", where)
        self.assertIn("$or", where["$and"][-1])

        foreign = {"category": "数码电子", "base_price": 20, "provenance": {"currency": "USD"}}
        converted_over_budget = {
            "category": "数码电子",
            "base_price": 20,
            "price_cny": 240,
            "provenance": {"currency": "USD"},
        }
        self.assertTrue(product_matches_filter(foreign, Filter(price_max_cny=200)))
        self.assertFalse(product_matches_filter(converted_over_budget, Filter(price_max_cny=200), strict_cny_price=True))

    def test_price_range_does_not_fall_back_to_over_budget_results(self) -> None:
        products = [{"product_id": "expensive", "base_price": 280, "provenance": {"currency": "CNY"}}]

        self.assertEqual(apply_price_intent(products, "100元以下的面霜"), [])

    def test_parses_budget_replacement_as_current_upper_bound(self) -> None:
        result = build_retrieval_filter("预算加到3500元")

        self.assertEqual(result.price_max_cny, 3500.0)

    @patch("rag.retrieve.hybrid.hybrid_topk")
    def test_production_path_passes_inferred_filter_to_hybrid(self, hybrid_topk) -> None:
        hybrid_topk.return_value = []

        top_k("500元以下的运动T恤", k=5)

        passed_filter = hybrid_topk.call_args.kwargs["f"]
        self.assertEqual(passed_filter.price_max_cny, 500.0)
        self.assertEqual(passed_filter.sub_categories, ["短袖T恤", "速干T恤"])

    @patch("app.services.currency.normalize_product_prices")
    @patch("rag.retrieve.query._keyword_fallback")
    @patch("rag.retrieve.query.query", side_effect=RuntimeError("dense unavailable"))
    @patch("rag.retrieve.hybrid.hybrid_topk", side_effect=RuntimeError("hybrid unavailable"))
    def test_keyword_fallback_still_enforces_converted_budget(
        self, _hybrid_topk, _dense_query, keyword_fallback, normalize_product_prices
    ) -> None:
        foreign = {
            "product_id": "usd-headphones",
            "category": "数码电子",
            "sub_category": "无线降噪耳机",
            "brand": "Sony",
            "base_price": 20,
            "provenance": {"currency": "USD"},
        }
        converted = {**foreign, "price_cny": 240}
        keyword_fallback.return_value = [SimpleNamespace(product=foreign)]
        normalize_product_prices.return_value = [converted]

        self.assertEqual(top_k("200元以下的 Sony 蓝牙耳机有哪些", k=5), [])


if __name__ == "__main__":
    unittest.main()
