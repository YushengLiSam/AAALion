"""Cross-encoder reranker, language-aware.

R8.F.4: a single cross-encoder cannot serve both Chinese and English
well in this project's catalog:
  * ``BAAI/bge-reranker-base`` (Chinese-trained, ~280 MB) gives the
    Chinese-query golden set recall@5 = 0.983 / MRR = 0.844 — best
    numbers on the Chinese baseline. BUT it does not understand
    English query semantics, so "Give me an iPhone" got iPad reranked
    above iPhone (eval blind spot — golden set was all-Chinese).
  * ``BAAI/bge-reranker-v2-m3`` (multilingual, ~568 MB) correctly
    handles English ("Give me an iPhone" → iPhone #1) but costs 1.6pp
    on the Chinese golden set and ~3× latency on CPU.

Resolution: **route by query language**. Any query containing at least
one Chinese character → base model (handles English brand-name tokens
embedded in Chinese sentences fine). Pure-English query →
multilingual model. This keeps Chinese metrics flat at the original
0.983 baseline while fixing the English-query failure mode.

Override env vars (testing / rollback):
  RERANK_MODEL_ZH=BAAI/bge-reranker-base
  RERANK_MODEL_MULTI=BAAI/bge-reranker-v2-m3
  RERANK_FORCE_MODEL=<name>   # bypass the router entirely
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Sequence


_CHINESE_MODEL = os.getenv("RERANK_MODEL_ZH", "BAAI/bge-reranker-base")
_MULTILINGUAL_MODEL = os.getenv("RERANK_MODEL_MULTI", "BAAI/bge-reranker-v2-m3")
_FORCE_MODEL = os.getenv("RERANK_FORCE_MODEL")  # optional override
# Back-compat: if the older RERANK_MODEL env is set, treat it as a force-all.
_LEGACY_MODEL = os.getenv("RERANK_MODEL")
if _LEGACY_MODEL and not _FORCE_MODEL:
    _FORCE_MODEL = _LEGACY_MODEL


@lru_cache(maxsize=4)
def _model(name: str):
    from sentence_transformers import CrossEncoder
    # R10.perf — max_length is the dominant CPU cost knob for the
    # cross-encoder (attention is ~quadratic in sequence length). The
    # reranker doc is title+brand+a description snippet; 256 tokens keeps
    # the full snippet, 128 truncates the long tail with negligible
    # relevance loss (title/brand/category carry the signal). Env-tunable
    # so we can trade a little recall for latency on the CPU VM and revert
    # instantly. Default 256 = original behaviour.
    max_len = int(os.getenv("RERANK_MAX_LENGTH", "256"))
    return CrossEncoder(name, max_length=max_len)


def _has_ascii_letter(text: str) -> bool:
    """True iff text has at least one [A-Za-z] character. Used as the
    routing key for the cross-encoder picker."""
    return any("a" <= c.lower() <= "z" for c in text or "")


def _pick_model_name(query: str) -> str:
    """Return the cross-encoder model name to use for this query.

    Heuristic (R8.F.4 revised):
      * Pure Chinese (no ASCII letters at all) → ``bge-reranker-base``
        — preserves the Chinese golden-set recall@5 = 0.983 baseline.
      * Anything containing at least one ASCII letter (pure English,
        OR Chinese with embedded brand name like "我想买 iPhone",
        OR English-only product code) → ``bge-reranker-v2-m3``,
        which handles cross-lingual semantics correctly.

    Why "any ASCII letter" instead of "majority ASCII":
      Empirically the base model fails the moment an English brand
      token shows up — even "我想买 iPhone" (75% Chinese) reranked
      iPad above iPhone. The multilingual model handles both ends of
      the spectrum, so erring on its side for any English presence
      is the safer call. Cost: ~3× latency only on those queries
      (~270 ms vs ~95 ms; CPU-only, would be ~100 ms each on GPU).
    """
    if _FORCE_MODEL:
        return _FORCE_MODEL
    if not query:
        return _CHINESE_MODEL
    return _MULTILINGUAL_MODEL if _has_ascii_letter(query) else _CHINESE_MODEL


def warmup_reranker() -> None:
    """Load BOTH cross-encoders and run a tiny prediction each before
    traffic, so the first real request doesn't pay the model-load cost.
    The two models share lru_cache; sequential warmup keeps cold-start
    bounded around 5-8 s on CPU.
    """
    for name in {_CHINESE_MODEL, _MULTILINGUAL_MODEL}:
        try:
            model = _model(name)
            model.predict(
                [("推荐一款日常洁面产品", "温和洁面乳 适合日常清洁")],
                batch_size=1,
                show_progress_bar=False,
            )
        except Exception as e:  # noqa: BLE001
            import sys
            print(f"[rerank] warmup failed for {name}: {e}", file=sys.stderr)


def rerank(query: str, candidates: Sequence[dict], top_k: int = 5) -> list[dict]:
    """Rerank product dict candidates by cross-encoder score against the
    query. Returns the top_k. On any failure (model not installed, etc.),
    falls back to input order.

    Model choice is per-query (see `_pick_model_name`)."""
    if not query or len(candidates) <= 1:
        return list(candidates)[:top_k]
    model_name = _pick_model_name(query)
    try:
        model = _model(model_name)
    except Exception as e:
        import sys
        print(f"[rerank] cross-encoder {model_name} unavailable: {e}", file=sys.stderr)
        return list(candidates)[:top_k]

    pairs: list[tuple[str, str]] = []
    for p in candidates:
        rag = p.get("rag_knowledge", {}) or {}
        doc = " ".join([
            p.get("title", ""),
            p.get("brand", ""),
            (rag.get("marketing_description") or "")[:240],
        ])
        pairs.append((query, doc))

    scores = model.predict(pairs, batch_size=8, show_progress_bar=False)
    ranked = sorted(zip(list(candidates), scores), key=lambda x: float(x[1]), reverse=True)
    # R9.A.2 — attach the rerank score to each product dict so chat.py can
    # surface it on the iOS "why this is recommended" debug card. Stored in
    # a private "_retrieval" key the chat route reads + strips before
    # sending to the iOS client.
    out: list[dict] = []
    for rank_idx, (p, s) in enumerate(ranked[:top_k]):
        sig = p.setdefault("_retrieval", {})
        sig["rerank_score"] = float(s)
        sig["rerank_rank"] = rank_idx
        sig["rerank_model"] = model_name
        out.append(p)
    return out
