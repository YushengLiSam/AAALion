"""偏好重加权先验(R9.B / 提案 #12)。

消费由用户 👍/👎 点击构建的按 (brand/category/sub_category) 维度的得分表
(见 server/app/services/preferences_db.py),对已排好序的候选列表做温和的
重排序:用户喜欢的簇上浮、不喜欢的簇下沉。

设计意图 — 温和、可见、有界:
  * 重排(rerank)后的列表是相关性的主干,偏好只起"轻推"作用。
  * 每个候选保留基础分 = (N - rank_index),即相邻位次间距为 1.0。偏好项
    额外加上 `alpha * sum(各匹配维度得分)`。在 alpha=0.5、得分钳制在 ±10
    的设定下,单次 👎(brand −2 + category −1 + sub −2 ≈ −5 → 加成 −2.5)
    就能让商品下降约 2 个位次——演示中点一下就能看到效果;而被强烈嫌弃的
    簇最多下沉约 5 位,仍然有界,确保不相关商品永远不会排到强匹配之上。
  * 稳定排序:得分相同时保持原有的 rerank 顺序。

这里刻意不用机器学习:它是一个透明的加性先验,用户可以直接控制、也能一键
清空。这正是整套答辩叙事的核心:"你的点击就是模型本身。"
"""

from __future__ import annotations

import os

_DIMENSIONS = ("brand", "category", "sub_category")


def _preference_bonus(product: dict, weights: dict[str, dict[str, float]]) -> float:
    """累加用户在该商品 brand / category / sub_category 三个维度上的得分。
    缺失的维度按 0 计。"""
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
    """用温和的偏好先验对 `candidates`(已按 rerank 顺序排列)重排序。
    返回新列表;得分相同时以输入顺序作为决胜依据(tiebreaker)。

    没有任何权重时直接原样返回(no-op)——因此从未点过 👍/👎 的新用户
    看到的就是纯相关性排序。
    """
    if not weights or len(candidates) <= 1:
        return candidates
    a = alpha if alpha is not None else float(os.getenv("PREF_ALPHA", "0.5"))
    n = len(candidates)
    scored: list[tuple[float, int, dict]] = []
    for idx, p in enumerate(candidates):
        base = float(n - idx)                       # 保持原有 rerank 顺序
        bonus = a * _preference_bonus(p, weights)   # alpha=0.3、得分 ±10 时约 ±3
        # 把加成写回商品,这样 chat.py 以后若想在调试卡片里展示
        # "你喜欢这个品牌 → +N" 就能直接取用。
        sig = p.setdefault("_retrieval", {})
        if bonus:
            sig["preference_bonus"] = round(bonus, 3)
        scored.append((base + bonus, idx, p))
    # 按调整后得分降序排序;idx 升序作为稳定的决胜依据。
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in scored]
