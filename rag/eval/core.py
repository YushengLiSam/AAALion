"""Reusable evaluation core for the golden retrieval eval.

This module separates evaluation logic from presentation so it can power
both the CLI (``rag.eval.run``) and the HTML dashboard (``rag.eval.report``).

Layout:
  * ``load_cases()``                  — load and validate ``golden.jsonl``
  * ``retrieve(query, mode, k)``      — call one of the three retrievers
  * ``case_query(case)``              — resolve query text (handles multi-turn)
  * ``score_case(retrieved, ...)``    — per-case metric dict
  * ``evaluate(modes, k)``            — structured run across all cases

Metric names are stable strings (``"recall@5"`` etc.) so reports can iterate
over them without hard-coding names.

Phase 3 will extend ``score_case`` with precision@5 / negation accuracy /
no-match correctness / latency. The structure here is designed for that
extension.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ----------------------------------------------------------------------------
# Mode registry
# ----------------------------------------------------------------------------

#: Stable list of retrieval modes — defines the column order in reports.
MODES: tuple[str, ...] = ("dense", "hybrid", "hybrid_rerank")

#: Mode → human label for tables / charts.
MODE_LABELS: dict[str, str] = {
    "dense": "Dense",
    "hybrid": "Hybrid (Dense + BM25)",
    "hybrid_rerank": "Hybrid + Rerank",
}


# ----------------------------------------------------------------------------
# Case loading
# ----------------------------------------------------------------------------

def load_cases(path: Path | None = None) -> list[dict]:
    """Load every JSON line from ``golden.jsonl``.

    Each case has shape::

        {
          "query": "推荐...",                  # or "messages": [...] for multi-turn
          "expected_product_ids": ["p_xxx"],   # may be [] for no-match cases
          "forbidden_product_ids": ["p_yyy"],  # optional, Phase 2+ for negation
          "tags": ["basic", "intent"]
        }
    """
    target = path if path is not None else Path(__file__).with_name("golden.jsonl")
    cases: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        cases.append(json.loads(line))
    return cases


def case_query(case: dict) -> str:
    """Return the query string to retrieve against.

    For multi-turn cases (``messages`` field present), delegate to the
    server-side contextual builder so eval matches production behavior.
    """
    if case.get("messages"):
        if str(REPO_ROOT / "server") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "server"))
        from app.schemas.chat import ChatMessage
        from app.services.contextual_query import build_retrieval_query

        messages = [ChatMessage.model_validate(m) for m in case["messages"]]
        return build_retrieval_query(messages)
    return case.get("query", "")


# ----------------------------------------------------------------------------
# Retrieval dispatch
# ----------------------------------------------------------------------------

def retrieve(query_text: str, mode: str, k: int) -> list[str]:
    """Dispatch to one of the three retrievers and return product_id list.

    Notes on return shapes (verified against the actual code):
      * ``dense_query`` / ``hybrid_topk`` → objects with ``.product_id`` attr
      * ``top_k`` (server-side full pipeline) → list[dict], use ``["product_id"]``
    """
    if mode == "dense":
        from rag.retrieve.query import query as dense_query
        return [h.product_id for h in dense_query(query_text, k=k)]
    if mode == "hybrid":
        from rag.retrieve.hybrid import hybrid_topk
        return [h.product_id for h in hybrid_topk(query_text, k=k)]
    if mode == "hybrid_rerank":
        if str(REPO_ROOT / "server") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "server"))
        from app.services.rag_client import top_k as full_top_k
        prods = full_top_k(query_text, k=k)
        return [p["product_id"] for p in prods]
    raise ValueError(f"unknown mode: {mode}")


# ----------------------------------------------------------------------------
# Per-case scoring
# ----------------------------------------------------------------------------

def _recall_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    if not expected:
        return 1.0
    hit = len(expected & set(retrieved[:k]))
    return hit / len(expected)


def _mrr(retrieved: list[str], expected: set[str]) -> float:
    if not expected:
        return 0.0
    for i, pid in enumerate(retrieved, 1):
        if pid in expected:
            return 1.0 / i
    return 0.0


def _precision_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    """Fraction of top-k that are in expected. Complement of recall.

    For sparse expected sets (e.g. expected has 1 product), precision is
    inherently low (a single hit at top is 1/k). That's by design — it
    surfaces "are we cluttering the top-k with irrelevant items".
    """
    if not expected:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    return len(expected & set(top)) / k


def _negation_accuracy(retrieved: list[str], forbidden: set[str], k: int = 5) -> float:
    """1.0 if no forbidden id appears in top-k, else fraction kept clean.

    Granular version (fraction of slots that are non-forbidden) so partial
    leaks show up in the aggregate. A single forbidden product leaked into
    top-5 scores 0.8 rather than 0.0.
    """
    if not forbidden:
        return 1.0
    top = retrieved[:k]
    if not top:
        return 1.0
    leaked = sum(1 for pid in top if pid in forbidden)
    return 1.0 - leaked / len(top)


@lru_cache(maxsize=1)
def _product_titles() -> dict[str, str]:
    """Map product_id → title for keyword-overlap heuristic on no-match cases."""
    titles: dict[str, str] = {}
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            pid = d.get("product_id")
            if isinstance(pid, str):
                titles[pid] = d.get("title", "")
        except Exception:
            continue
    return titles


def _chinese_chars(s: str) -> set[str]:
    """Set of CJK chars in the string. Used for crude overlap on no-match."""
    return {ch for ch in s if "一" <= ch <= "鿿"}


def _no_match_correctness(query: str, retrieved: list[str], k: int = 5) -> float:
    """For ``expected=[]`` cases: 1.0 if retrieval is being honest about
    the lack of a match, lower if it's force-matching.

    Heuristic: measure average Chinese-character overlap (Jaccard) between
    the query and top-k product titles. A truly unrelated retrieval has
    low overlap (e.g. query "电动牙刷" vs returned cosmetic products → low).
    A force-matched retrieval reuses the query's distinctive chars in titles.

    Score = 1 - mean(jaccard). High when titles share little with the query.
    """
    titles = _product_titles()
    qset = _chinese_chars(query)
    if not qset:
        return 1.0
    top = retrieved[:k]
    if not top:
        return 1.0
    overlaps: list[float] = []
    for pid in top:
        tset = _chinese_chars(titles.get(pid, ""))
        if not tset:
            continue
        union = qset | tset
        inter = qset & tset
        overlaps.append(len(inter) / len(union) if union else 0.0)
    if not overlaps:
        return 1.0
    mean_overlap = sum(overlaps) / len(overlaps)
    # Invert so "honest no-match" → high score.
    return max(0.0, min(1.0, 1.0 - mean_overlap * 2))  # 2× to amplify signal


def score_case(
    retrieved: list[str],
    expected: list[str] | None,
    *,
    forbidden: list[str] | None = None,
    query: str = "",
    latency_ms: float | None = None,
) -> dict[str, float | None]:
    """Compute every metric for one (retrieved, expected) pair.

    Returns a dict keyed by stable metric name. Metrics that do not apply
    to this case (e.g. recall on a no-match case) return ``None`` so the
    aggregator can skip them. ``None`` values are filtered in averaging.

    Metric matrix (which metrics apply when):

      expected non-empty: recall@5, recall@10, mrr, precision@5
      forbidden non-empty: negation_accuracy
      expected == []:     no_match_correctness
      always (if measured): latency_ms
    """
    exp_set = set(expected or [])
    forb_set = set(forbidden or [])

    out: dict[str, float | None] = {
        "recall@5":      _recall_at_k(retrieved, exp_set, 5) if exp_set else None,
        "recall@10":     _recall_at_k(retrieved, exp_set, 10) if exp_set else None,
        "mrr":           _mrr(retrieved, exp_set) if exp_set else None,
        "precision@5":   _precision_at_k(retrieved, exp_set, 5) if exp_set else None,
        "negation_accuracy":   _negation_accuracy(retrieved, forb_set, 5) if forb_set else None,
        "no_match_correctness": _no_match_correctness(query, retrieved, 5) if expected == [] else None,
    }
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    return out


# ----------------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------------

def _avg(values: list[float | None]) -> float | None:
    """Average non-None values; return None if all are None."""
    real = [v for v in values if v is not None]
    if not real:
        return None
    return sum(real) / len(real)


def _aggregate_per_case(per_case: list[dict]) -> dict[str, float | None]:
    """Average each metric across all per-case rows."""
    if not per_case:
        return {}
    metric_names = set()
    for row in per_case:
        metric_names.update(k for k in row.get("metrics", {}).keys())
    return {
        name: _avg([row["metrics"].get(name) for row in per_case])
        for name in sorted(metric_names)
    }


def _group_by_tag(per_case: list[dict]) -> dict[str, dict[str, float | None]]:
    """Group per-case rows by each tag, then average within each group."""
    by_tag: dict[str, list[dict]] = {}
    for row in per_case:
        for tag in row.get("tags") or ["_untagged"]:
            by_tag.setdefault(tag, []).append(row)
    return {tag: _aggregate_per_case(rows) for tag, rows in by_tag.items()}


# ----------------------------------------------------------------------------
# Top-level evaluate
# ----------------------------------------------------------------------------

@dataclass
class CaseRecord:
    """A single (case × mode) evaluation result, for serialization."""
    query: str
    expected: list[str]
    forbidden: list[str]
    tags: list[str]
    retrieved: list[str]
    metrics: dict[str, float | None]
    latency_ms: float | None
    error: str | None = None


def evaluate(modes: list[str] | None = None, k: int = 10) -> dict[str, Any]:
    """Run every case through every mode; return structured results.

    The returned dict can be JSON-serialized and consumed by the report
    generator. Layout::

        {
          "meta": {...},
          "modes": {
            "<mode>": {
              "overall": {<metric>: <value>},
              "by_tag":  {<tag>: {<metric>: <value>}},
              "per_case": [<CaseRecord-as-dict>]
            }
          }
        }
    """
    modes = list(modes or MODES)
    cases = load_cases()
    n_scored = sum(1 for c in cases if c.get("expected_product_ids") is not None)

    result: dict[str, Any] = {
        "meta": {
            "n_cases": len(cases),
            "n_scored": n_scored,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "dataset_size": _count_dataset(),
            "k": k,
            "modes": modes,
        },
        "modes": {},
    }

    for mode in modes:
        per_case: list[dict] = []
        errors = 0
        for case in cases:
            query = case_query(case)
            expected = case.get("expected_product_ids", [])
            forbidden = case.get("forbidden_product_ids", []) or []
            tags = case.get("tags", []) or []

            try:
                t0 = time.perf_counter()
                retrieved = retrieve(query, mode, k=k)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                metrics = score_case(
                    retrieved,
                    expected,
                    forbidden=forbidden,
                    query=query,
                    latency_ms=latency_ms,
                )
                record = CaseRecord(
                    query=query,
                    expected=list(expected) if expected else [],
                    forbidden=forbidden,
                    tags=tags,
                    retrieved=retrieved,
                    metrics=metrics,
                    latency_ms=latency_ms,
                )
            except Exception as e:  # noqa: BLE001
                errors += 1
                record = CaseRecord(
                    query=query,
                    expected=list(expected) if expected else [],
                    forbidden=forbidden,
                    tags=tags,
                    retrieved=[],
                    metrics={},
                    latency_ms=None,
                    error=f"{type(e).__name__}: {e}",
                )
            per_case.append(_record_to_dict(record))

        overall = _aggregate_per_case(per_case)
        by_tag = _group_by_tag(per_case)

        result["modes"][mode] = {
            "label": MODE_LABELS.get(mode, mode),
            "overall": overall,
            "by_tag": by_tag,
            "per_case": per_case,
            "errors": errors,
        }

    return result


def _record_to_dict(r: CaseRecord) -> dict[str, Any]:
    return {
        "query": r.query,
        "expected": r.expected,
        "forbidden": r.forbidden,
        "tags": r.tags,
        "retrieved": r.retrieved,
        "metrics": r.metrics,
        "latency_ms": r.latency_ms,
        "error": r.error,
    }


def _count_dataset() -> int:
    seed = REPO_ROOT / "data" / "seed"
    if not seed.is_dir():
        return 0
    return sum(1 for _ in seed.glob("*/data/*.json"))
