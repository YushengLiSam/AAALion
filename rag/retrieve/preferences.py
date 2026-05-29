"""Preference re-weighting prior (R9.B / proposal #12).

Consumes the per-(brand/category/sub_category) score table built from the
user's 👍/👎 taps (see server/app/services/preferences_db.py) and gently
re-orders an already-ranked candidate list so liked clusters float up and
disliked ones sink.

Design intent — GENTLE, VISIBLE, BOUNDED:
  * The reranked list is the relevance backbone. Preference only nudges.
  * Each candidate keeps a base score = (N - rank_index), i.e. 1.0 of
    spacing per position. Preference adds `alpha * sum(matching dim
    scores)`. With alpha=0.3 and scores clamped to ±10, a strongly-liked
    brand moves at most ~3 positions — enough to SEE in a demo, not
    enough to surface an irrelevant product.
  * Stable sort: ties preserve the original rerank order.

This is deliberately NOT machine learning. It's a transparent additive
prior the user controls directly and can wipe with one tap. That's the
whole defense narrative: "your taps are the model."
"""

from __future__ import annotations

import os

_DIMENSIONS = ("brand", "category", "sub_category")


def _preference_bonus(product: dict, weights: dict[str, dict[str, float]]) -> float:
    """Sum the user's scores for this product's brand / category /
    sub_category. Missing dimensions contribute 0."""
    total = 0.0
    for dim in _DIMENSIONS:
        value = (product.get(dim) or "").strip()
        if not value:
            continue
        total += weights.get(dim, {}).get(value, 0.0)
    return total


def apply_preference_prior(
    candidates: list[dict],
    weights: dict[str, dict[str, float]] | None,
    *,
    alpha: float | None = None,
) -> list[dict]:
    """Re-order `candidates` (already in rerank order) by a gentle
    preference prior. Returns a new list; input order is the tiebreaker.

    No-ops (returns the input unchanged) when there are no weights — so
    a brand-new user with zero taps sees pure relevance ranking.
    """
    if not weights or len(candidates) <= 1:
        return candidates
    a = alpha if alpha is not None else float(os.getenv("PREF_ALPHA", "0.3"))
    n = len(candidates)
    scored: list[tuple[float, int, dict]] = []
    for idx, p in enumerate(candidates):
        base = float(n - idx)                       # preserve rerank order
        bonus = a * _preference_bonus(p, weights)   # ±3 at alpha=0.3, score ±10
        # Stamp the bonus so chat.py can expose it in the debug card if
        # we ever want to show "you liked this brand → +N".
        sig = p.setdefault("_retrieval", {})
        if bonus:
            sig["preference_bonus"] = round(bonus, 3)
        scored.append((base + bonus, idx, p))
    # Sort by adjusted score desc; idx asc as the stable tiebreaker.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in scored]
