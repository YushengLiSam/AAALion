"""Offline helper to propose ecommerce synonym candidates.

This script is intentionally not part of the serving path. It can use optional
general synonym packages when they are installed, then blends those suggestions
with terms mined from the local product catalog and the reviewed runtime
dictionary in ``rag.retrieve.synonyms``.

Example:
    python tools/build_synonym_candidates.py --terms 无线耳机 降噪 舒敏 控油
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_TERMS = ("无线耳机", "降噪", "舒敏", "控油", "平价", "精华", "防晒")
STOPWORDS = {
    "一个",
    "一些",
    "可以",
    "适合",
    "推荐",
    "产品",
    "商品",
    "使用",
    "这款",
    "支持",
    "需要",
    "场景",
    "用户",
    "日常",
    "高频",
    "版本",
    "规格",
    "标准",
}


def _iter_products() -> Iterable[dict]:
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


def _product_text(product: dict) -> str:
    rag = product.get("rag_knowledge", {}) or {}
    parts = [
        product.get("title", ""),
        product.get("brand", ""),
        product.get("category", ""),
        product.get("sub_category", ""),
        rag.get("marketing_description", ""),
    ]
    for faq in rag.get("official_faq", []) or []:
        parts.extend([faq.get("question", ""), faq.get("answer", "")])
    for review in rag.get("user_reviews", []) or []:
        parts.append(review.get("content", ""))
    return " ".join(str(p) for p in parts if p)


def _extract_terms(text: str, *, top_k: int = 40) -> list[str]:
    try:
        import jieba.analyse

        words = jieba.analyse.extract_tags(text, topK=top_k * 2)
    except Exception:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+-]{1,20}|[\u4e00-\u9fff]{2,8}", text)

    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        word = word.strip()
        if not _valid_candidate(word) or word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= top_k:
            break
    return out


def _valid_candidate(word: str) -> bool:
    if not (2 <= len(word) <= 24):
        return False
    if word in STOPWORDS:
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", word))


def _generic_synonym_candidates(term: str, *, limit: int) -> tuple[list[str], str | None]:
    try:
        import synonyms  # type: ignore

        words, _scores = synonyms.nearby(term, limit + 5)
        return [w for w in words if _valid_candidate(w) and w != term][:limit], None
    except Exception as exc:
        return [], f"python package 'synonyms' unavailable: {exc.__class__.__name__}"


def _catalog_candidates(term: str, product_texts: list[str], *, limit: int) -> list[str]:
    matched = [text for text in product_texts if term.lower() in text.lower()]
    if not matched:
        matched = [text for text in product_texts if any(ch in text for ch in term)]

    counts: Counter[str] = Counter()
    for text in matched[:20]:
        counts.update(_extract_terms(text, top_k=30))

    for token in list(counts):
        if token == term or token.lower() in term.lower():
            del counts[token]

    return [token for token, _ in counts.most_common(limit)]


def build_candidates(terms: list[str], *, limit: int) -> dict:
    from rag.retrieve.synonyms import SYNONYM_TERMS

    product_texts = [_product_text(p) for p in _iter_products()]
    all_catalog_text = "\n".join(product_texts).lower()
    warnings: set[str] = set()
    results: dict[str, list[dict]] = {}

    for term in terms:
        bucket: dict[str, dict] = {}

        def add(candidate: str, source: str, weight: float) -> None:
            candidate = candidate.strip()
            if not _valid_candidate(candidate) or candidate == term:
                return
            item = bucket.setdefault(
                candidate,
                {
                    "term": candidate,
                    "score": 0.0,
                    "sources": [],
                    "in_catalog": candidate.lower() in all_catalog_text,
                },
            )
            item["score"] += weight
            if source not in item["sources"]:
                item["sources"].append(source)

        for candidate in SYNONYM_TERMS.get(term, ()):
            add(candidate, "curated_runtime_dictionary", 3.0)

        generic, warning = _generic_synonym_candidates(term, limit=limit)
        if warning:
            warnings.add(warning)
        for candidate in generic:
            add(candidate, "optional_general_synonym_package", 1.0)

        for candidate in _catalog_candidates(term, product_texts, limit=limit * 2):
            add(candidate, "catalog_cooccurrence", 2.0)

        ranked = sorted(
            bucket.values(),
            key=lambda item: (item["score"], item["in_catalog"], item["term"]),
            reverse=True,
        )
        results[term] = ranked[:limit]

    return {
        "terms": terms,
        "warnings": sorted(warnings),
        "candidates": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terms", nargs="*", default=list(DEFAULT_TERMS))
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    payload = build_candidates(args.terms, limit=args.limit)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
