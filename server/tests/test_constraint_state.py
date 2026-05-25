from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.schemas.chat import ChatMessage
from app.services.constraint_state import build_conversation_filter
from app.services.rag_client import top_k
from rag.retrieve.query import Filter


def _turns(*texts: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text) for text in texts]


class ConversationConstraintStateTests(unittest.TestCase):
    def test_budget_override_keeps_product_and_brand_state(self) -> None:
        result = build_conversation_filter(_turns("想买2500元以下的 Sony 降噪耳机", "预算加到3500元"))

        self.assertEqual(result.category, "数码电子")
        self.assertIn("无线降噪耳机", result.sub_categories)
        self.assertEqual(result.brand_include, ["Sony"])
        self.assertEqual(result.price_max_cny, 3500.0)

    def test_replaced_brand_removes_previous_positive_brand(self) -> None:
        result = build_conversation_filter(_turns("想买3500元以下的 Sony 降噪耳机", "不要 Sony 了，改成 Bose"))

        self.assertEqual(result.brand_include, ["Bose"])
        self.assertIn("Sony", result.brand_exclude)
        self.assertEqual(result.price_max_cny, 3500.0)

    def test_cancelled_budget_and_brand_keep_product_type(self) -> None:
        result = build_conversation_filter(_turns("想买3000元以下的 Apple 平板电脑", "品牌不限，预算不限"))

        self.assertEqual(result.category, "数码电子")
        self.assertEqual(result.sub_categories, ["平板电脑"])
        self.assertIsNone(result.brand_include)
        self.assertIsNone(result.brand_exclude)
        self.assertIsNone(result.price_max_cny)

    def test_explicit_filter_overrides_conversation_state(self) -> None:
        result = build_conversation_filter(
            _turns("想买 Sony 耳机"),
            {"include_brands": ["Bose"], "price_max": 4000},
        )

        self.assertEqual(result.brand_include, ["Bose"])
        self.assertEqual(result.price_max_cny, 4000.0)

    def test_explicit_new_category_clears_inherited_subcategory(self) -> None:
        result = build_conversation_filter(
            _turns("想买 Sony 平板电脑"),
            {"category": "美妆护肤", "exclude_brands": ["Sony"]},
        )

        self.assertEqual(result.category, "美妆护肤")
        self.assertIsNone(result.sub_category)
        self.assertIsNone(result.sub_categories)
        self.assertIsNone(result.brand_include)
        self.assertEqual(result.brand_exclude, ["Sony"])

    @patch("rag.retrieve.hybrid.hybrid_topk")
    def test_empty_authoritative_state_does_not_reinfer_cancelled_anchor(self, hybrid_topk) -> None:
        hybrid_topk.return_value = []

        top_k(
            "3000元以下的 Sony 耳机 品牌不限 预算不限",
            conversation_filter=Filter(),
            intent_text="品牌不限，预算不限",
        )

        passed_filter = hybrid_topk.call_args.kwargs["f"]
        self.assertIsNotNone(passed_filter)
        self.assertFalse(passed_filter.active)

    @patch("rag.retrieve.hybrid.hybrid_topk")
    def test_state_inference_toggle_does_not_drop_explicit_api_filter(self, hybrid_topk) -> None:
        hybrid_topk.return_value = []

        with patch.dict(os.environ, {"RAG_HARD_FILTERS": "0"}):
            top_k(
                "Sony 耳机",
                filters={"price_max": 2000},
                conversation_filter=Filter(brand_include=["Sony"]),
            )

        passed_filter = hybrid_topk.call_args.kwargs["f"]
        self.assertIsNone(passed_filter.brand_include)
        self.assertEqual(passed_filter.price_max_cny, 2000.0)


if __name__ == "__main__":
    unittest.main()
