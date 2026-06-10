"""面向用户展示的商品价格货币归一化。

商品目录价格保持源币种不变。本模块使用 Frankfurter 最新可用的参考汇率,
为商品 payload 补充一个人民币(CNY)展示价,同时保留原始金额,
以保证价格来源透明、可审计。
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
    """清空内存中的汇率缓存状态,主要用于让测试结果可复现(确定性)。"""
    with _cache_lock:
        _cache.clear()


def get_exchange_rate(source_currency: str, target_currency: str = TARGET_CURRENCY) -> ExchangeRate | None:
    """返回最新可用的汇率报价;取不到时回退到标记为过期(stale)的旧报价。

    仅在本地 TTL 过期后才发起实时请求。若汇率源暂时不可用,则复用之前
    获取过的报价并置 ``stale=True``;若连旧报价都没有,则返回 None,
    调用方会保持展示原币种价格。
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
    """返回补充了人民币(CNY)展示价的商品 payload。"""
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
    """构造稳定的缓存 token,使缓存命中的回答与当前展示的汇率报价保持一致。"""
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
