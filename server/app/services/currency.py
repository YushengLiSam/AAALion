"""Currency normalization for user-facing product prices.

Catalog prices remain in their source currency. This module enriches product
payloads with a CNY display price using the latest available reference rate
from Frankfurter, and keeps the original amount for transparency.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Sequence

import httpx

log = logging.getLogger("currency")

TARGET_CURRENCY = "CNY"
_API_BASE_URL = os.getenv("FX_API_BASE_URL", "https://api.frankfurter.dev/v2").rstrip("/")
_PROVIDER_LABEL = os.getenv("FX_PROVIDER_LABEL", "Frankfurter latest reference rate")
_RATE_TTL_SECONDS = max(float(os.getenv("FX_RATE_TTL_SECONDS", "3600")), 0.0)
_HTTP_TIMEOUT_SECONDS = max(float(os.getenv("FX_HTTP_TIMEOUT_SECONDS", "3.0")), 0.1)
_CODE_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class ExchangeRate:
    source_currency: str
    target_currency: str
    rate: float
    rate_date: str
    provider: str = _PROVIDER_LABEL
    stale: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "source_currency": self.source_currency,
            "target_currency": self.target_currency,
            "rate": self.rate,
            "rate_date": self.rate_date,
            "provider": self.provider,
            "stale": self.stale,
        }


_cache: dict[tuple[str, str], tuple[float, ExchangeRate]] = {}
_cache_lock = threading.Lock()


def clear_rate_cache() -> None:
    """Clear in-memory exchange-rate state, primarily for deterministic tests."""
    with _cache_lock:
        _cache.clear()


def get_exchange_rate(source_currency: str, target_currency: str = TARGET_CURRENCY) -> ExchangeRate | None:
    """Return the latest available quote, or a marked stale fallback.

    A live request is made after the local TTL expires. If the provider is
    temporarily unavailable, a previously fetched quote is reused with
    ``stale=True``; without any quote, callers leave the original price shown.
    """
    source = _normalized_code(source_currency)
    target = _normalized_code(target_currency)
    if not source or not target or source == target:
        return None

    key = (source, target)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and _RATE_TTL_SECONDS > 0 and now - cached[0] <= _RATE_TTL_SECONDS:
            return cached[1]

    try:
        quote = _request_rate(source, target)
    except Exception as exc:  # noqa: BLE001
        log.warning("FX quote unavailable for %s/%s: %s", source, target, exc)
        if cached:
            return replace(cached[1], stale=True)
        return None

    with _cache_lock:
        _cache[key] = (now, quote)
    return quote


def normalize_product_price(product: dict) -> dict:
    """Return a product payload enriched with CNY display prices."""
    normalized = copy.deepcopy(product)
    source_currency = _product_currency(product)
    base_price = _amount(product.get("base_price"))
    if base_price is None:
        return normalized

    if source_currency == TARGET_CURRENCY:
        normalized["price_cny"] = round(base_price, 2)
        return normalized

    quote = get_exchange_rate(source_currency)
    if quote is None:
        return normalized

    normalized["price_cny"] = _convert(base_price, quote.rate)
    normalized["exchange_rate"] = quote.payload()
    skus = normalized.get("skus")
    if isinstance(skus, list):
        for sku in skus:
            if isinstance(sku, dict):
                sku_price = _amount(sku.get("price"))
                if sku_price is not None:
                    sku["price_cny"] = _convert(sku_price, quote.rate)
    return normalized


def normalize_product_prices(products: Sequence[dict]) -> list[dict]:
    return [normalize_product_price(product) for product in products]


def pricing_cache_token(products: Sequence[dict]) -> str:
    """Build a stable token so cached answers track the displayed FX quote."""
    pieces: list[str] = []
    for product in products:
        rate = product.get("exchange_rate") or {}
        pieces.append(
            f"{product.get('product_id', '')}:{product.get('price_cny', '')}:"
            f"{rate.get('rate_date', '')}:{rate.get('rate', '')}:{rate.get('stale', '')}"
        )
    return "|".join(pieces)


def _request_rate(source: str, target: str) -> ExchangeRate:
    response = httpx.get(
        f"{_API_BASE_URL}/rate/{source}/{target}",
        timeout=_HTTP_TIMEOUT_SECONDS,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    rate = _amount(payload.get("rate"))
    date = payload.get("date")
    if rate is None or rate <= 0 or not isinstance(date, str):
        raise ValueError("malformed exchange-rate response")
    return ExchangeRate(
        source_currency=source,
        target_currency=target,
        rate=rate,
        rate_date=date,
    )


def _product_currency(product: dict) -> str:
    provenance = product.get("provenance")
    raw = provenance.get("currency") if isinstance(provenance, dict) else TARGET_CURRENCY
    return _normalized_code(raw) or TARGET_CURRENCY


def _normalized_code(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    code = raw.upper().strip()
    return code if _CODE_RE.match(code) else None


def _amount(raw: object) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _convert(amount: float, rate: float) -> float:
    return round(amount * rate, 2)
