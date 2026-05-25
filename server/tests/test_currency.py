from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import currency
from app.services.currency import ExchangeRate
from app.services.price_intent import apply_price_intent


class CurrencyNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        currency.clear_rate_cache()

    @patch("app.services.currency._request_rate")
    def test_foreign_product_gets_cny_price_and_sku_prices(self, request_rate) -> None:
        request_rate.return_value = ExchangeRate("USD", "CNY", 7.0, "2026-05-25")
        product = {
            "product_id": "p_intl",
            "base_price": 72.0,
            "skus": [{"price": 80.0}],
            "provenance": {"currency": "USD"},
        }

        result = currency.normalize_product_price(product)

        self.assertEqual(result["price_cny"], 504.0)
        self.assertEqual(result["skus"][0]["price_cny"], 560.0)
        self.assertEqual(result["exchange_rate"]["source_currency"], "USD")
        self.assertEqual(result["exchange_rate"]["rate_date"], "2026-05-25")
        self.assertNotIn("price_cny", product)
        request_rate.assert_called_once_with("USD", "CNY")

    @patch("app.services.currency._request_rate")
    def test_cny_product_never_calls_exchange_provider(self, request_rate) -> None:
        result = currency.normalize_product_price(
            {"base_price": 89.0, "provenance": {"currency": "CNY"}}
        )

        self.assertEqual(result["price_cny"], 89.0)
        self.assertNotIn("exchange_rate", result)
        request_rate.assert_not_called()

    @patch("app.services.currency._request_rate")
    def test_stale_quote_is_used_when_refresh_fails(self, request_rate) -> None:
        request_rate.return_value = ExchangeRate("USD", "CNY", 7.0, "2026-05-25")
        currency.get_exchange_rate("USD")

        request_rate.side_effect = RuntimeError("provider offline")
        with patch.object(currency, "_RATE_TTL_SECONDS", 0):
            quote = currency.get_exchange_rate("USD")

        self.assertIsNotNone(quote)
        self.assertTrue(quote.stale)
        self.assertEqual(quote.rate, 7.0)

    def test_price_intent_compares_normalized_cny_amounts(self) -> None:
        products = [
            {
                "product_id": "usd",
                "base_price": 20.0,
                "price_cny": 140.0,
                "provenance": {"currency": "USD"},
            },
            {
                "product_id": "cny",
                "base_price": 80.0,
                "price_cny": 80.0,
                "provenance": {"currency": "CNY"},
            },
        ]

        selected = apply_price_intent(products, "100元以下的商品")

        self.assertEqual([product["product_id"] for product in selected], ["cny"])

    def test_foreign_price_without_fx_does_not_satisfy_cny_budget(self) -> None:
        products = [
            {"product_id": "usd", "base_price": 20.0, "provenance": {"currency": "USD"}},
            {"product_id": "cny", "base_price": 80.0, "provenance": {"currency": "CNY"}},
        ]

        selected = apply_price_intent(products, "100元以下的商品")

        self.assertEqual([product["product_id"] for product in selected], ["cny"])


if __name__ == "__main__":
    unittest.main()
