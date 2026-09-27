"""向量存储后端一致性比对(Chroma vs Milvus)。

同一批 query、同一批向量(``rag.store.migrate`` 原样搬过去的),在两个后端上
逐条比对检索结果;再用 numpy 暴力算精确余弦 top-k 当"标准答案",
两边对不上的时候看谁离精确解更近。

    python -m rag.eval.store_parity                          # 生产默认:国别下推开
    RAG_FILTER_PUSHDOWN=0 python -m rag.eval.store_parity    # 关掉国别下推再比

每条 query 比两种过滤:不过滤,以及生产路径实际会用的过滤
(对话状态过滤,否则 ``build_retrieval_filter(query)``)。
另外把 145 张商品图的向量逐张当 query,比对图片集合。

退出码:存在真实不一致时为 1(边界并列不算,见 ``_topk_agree``)。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("CHROMA_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

K = int(os.getenv("PARITY_K", "60"))  # 生产路径稠密检索取 k*3 = 60 个块
TOP = 10
SCORE_EPS = 1e-4
TIE_EPS = 1e-5


# ---------------------------------------------------------------------------
# 中立过滤条件的 Python 求值器(语义与 Chroma 一致:$ne/$nin 放行缺失字段)
# ---------------------------------------------------------------------------

def _match(meta: dict, where: dict | None) -> bool:
    if not where:
        return True
    if len(where) > 1:
        return all(_match(meta, {k: v}) for k, v in where.items())
    ((key, val),) = where.items()
    if key == "$and":
        return all(_match(meta, w) for w in val)
    if key == "$or":
        return any(_match(meta, w) for w in val)
    present = key in meta
    x = meta.get(key)
    if not isinstance(val, dict):
        return present and x == val
    ((op, operand),) = val.items()
    if op == "$in":
        return present and x in operand
    if op == "$nin":
        return (not present) or x not in operand
    if op == "$ne":
        return (not present) or x != operand
    if not present:
        return False
    try:
        return {"$eq": x == operand, "$gt": x > operand, "$gte": x >= operand,
                "$lt": x < operand, "$lte": x <= operand}[op]
    except (KeyError, TypeError):
        raise ValueError(f"unsupported op {op}")


class Exact:
    """导出全部向量,按过滤条件暴力求精确余弦 top-k。"""

    def __init__(self, store, collection: str) -> None:
        ids, vecs, metas = [], [], []
        for i, v, m, _ in store.export(collection):
            ids += i
            vecs += v
            metas += m
        self.ids = ids
        self.metas = metas
        mat = np.asarray(vecs, dtype=np.float64)
        self.mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        self.by_id = {i: n for n, i in enumerate(ids)}

    def topk(self, vec, k: int, where: dict | None) -> list[tuple[str, float]]:
        q = np.asarray(vec, dtype=np.float64)
        q = q / np.linalg.norm(q)
        sims = self.mat @ q
        mask = np.array([_match(m, where) for m in self.metas]) if where else np.ones(len(self.ids), bool)
        idx = np.where(mask)[0]
        order = idx[np.argsort(-sims[idx], kind="stable")][:k]
        return [(self.ids[n], float(sims[n])) for n in order]


def _topk_agree(a: list[tuple[str, float]], b: list[tuple[str, float]], n: int) -> bool:
    """两个排序结果的前 n 名集合是否一致。第 n 名处的分数并列造成的差异不算不一致。"""
    sa, sb = {i for i, _ in a[:n]}, {i for i, _ in b[:n]}
    if sa == sb:
        return True
    if len(a) < n or len(b) < n:
        return False
    cut = min(a[n - 1][1], b[n - 1][1])
    diff = sa ^ sb
    scores = dict(a) | dict(b)
    return all(abs(scores[i] - cut) <= TIE_EPS for i in diff)


def _recall(got: list[tuple[str, float]], exact: list[tuple[str, float]], n: int) -> float:
    ref = {i for i, _ in exact[:n]}
    if not ref:
        return 1.0
    return len(ref & {i for i, _ in got[:n]}) / len(ref)


def _cases() -> list[dict]:
    from rag.eval.core import load_cases

    out = []
    for name in ("golden.jsonl", "golden_compositional.jsonl"):
        p = REPO_ROOT / "rag" / "eval" / name
        if p.exists():
            for c in load_cases(p):
                c["_set"] = name
                out.append(c)
    return out


def main() -> int:
    from rag.eval.core import case_query, case_retrieval_state
    from rag.ingest.embed_text import embed_query
    from rag.retrieve.constraints import build_retrieval_filter
    from rag.retrieve.query import Filter, _build_where, _filter_pushdown_on
    from rag.store import IMAGE_COLLECTION, TEXT_COLLECTION, get_store

    chroma, milvus = get_store("chroma"), get_store("milvus")
    for s in (chroma, milvus):
        if "brand_country" not in s.filterable_fields(TEXT_COLLECTION):
            print(f"{s.backend} index is pre-v2; rebuild + migrate first", file=sys.stderr)
            return 2

    exact = Exact(chroma, TEXT_COLLECTION)
    pushdown = _filter_pushdown_on()  # 与 _build_where 用同一个开关和默认值

    rows = []
    for case in _cases():
        text = case_query(case)
        if not text.strip():
            continue
        conv, _ = case_retrieval_state(case)
        f = conv if isinstance(conv, Filter) else build_retrieval_filter(text)
        variants = [("none", None)]
        w = _build_where(f)
        if w:
            variants.append(("prod", w))
        vec = embed_query(text)
        for label, where in variants:
            c = [(h.id, h.score) for h in chroma.query_text(vec, K, where=where)]
            m = [(h.id, h.score) for h in milvus.query_text(vec, K, where=where)]
            e = exact.topk(vec, K, where)
            common = {i for i, _ in c} & {i for i, _ in m}
            dc, dm = dict(c), dict(m)
            rows.append({
                "set": case["_set"],
                "query": text[:40],
                "filter": label,
                "where": json.dumps(where, ensure_ascii=False) if where else "",
                "n_c": len(c), "n_m": len(m), "n_exact": len(e),
                "top10_order": [i for i, _ in c[:TOP]] == [i for i, _ in m[:TOP]],
                "top10_set": _topk_agree(c, m, TOP),
                "jaccard_k": len(common) / max(1, len({i for i, _ in c} | {i for i, _ in m})),
                "max_score_diff": max((abs(dc[i] - dm[i]) for i in common), default=0.0),
                "c_recall10": _recall(c, e, TOP), "m_recall10": _recall(m, e, TOP),
                "c_recallK": _recall(c, e, K), "m_recallK": _recall(m, e, K),
                "count_match": len(c) == len(m),
            })

    # --- 图片集合:每张商品图的向量当 query ---
    img_exact = Exact(chroma, IMAGE_COLLECTION)
    img_rows = []
    for pid, n in img_exact.by_id.items():
        vec = img_exact.mat[n].tolist()
        c = [(h.id, h.score) for h in chroma.query_image(vec, TOP)]
        m = [(h.id, h.score) for h in milvus.query_image(vec, TOP)]
        e = img_exact.topk(vec, TOP, None)
        common = {i for i, _ in c} & {i for i, _ in m}
        img_rows.append({
            "top1": c[:1] == m[:1] or (c and m and c[0][0] == m[0][0]),
            "self_top1_c": bool(c) and c[0][0] == pid,
            "self_top1_m": bool(m) and m[0][0] == pid,
            "top10_set": _topk_agree(c, m, TOP),
            "max_score_diff": max((abs(dict(c)[i] - dict(m)[i]) for i in common), default=0.0),
            "c_recall10": _recall(c, e, TOP), "m_recall10": _recall(m, e, TOP),
        })

    def pct(rs, key):
        return sum(1 for r in rs if r[key]) / max(1, len(rs))

    def avg(rs, key):
        return sum(r[key] for r in rs) / max(1, len(rs))

    print(f"\n# 向量存储一致性:chroma vs milvus  (K={K}, 国别下推={'开' if pushdown else '关'})\n")
    print("| 切片 | 查询数 | top10 顺序一致 | top10 集合一致 | Jaccard@K | 最大分差 | 条数一致 | Chroma recall@10/@K(对精确解) | Milvus recall@10/@K(对精确解) |")
    print("|---|---:|---:|---:|---:|---:|---:|---|---|")
    for label, rs in (
        ("文本 · 不过滤", [r for r in rows if r["filter"] == "none"]),
        ("文本 · 生产过滤", [r for r in rows if r["filter"] == "prod"]),
        ("文本 · 全部", rows),
    ):
        if not rs:
            continue
        print(f"| {label} | {len(rs)} | {pct(rs,'top10_order'):.3f} | {pct(rs,'top10_set'):.3f} | "
              f"{avg(rs,'jaccard_k'):.3f} | {max(r['max_score_diff'] for r in rs):.2e} | {pct(rs,'count_match'):.3f} | "
              f"{avg(rs,'c_recall10'):.3f} / {avg(rs,'c_recallK'):.3f} | {avg(rs,'m_recall10'):.3f} / {avg(rs,'m_recallK'):.3f} |")
    print(f"| 图片 · 145 张 | {len(img_rows)} | top1 {pct(img_rows,'top1'):.3f} | {pct(img_rows,'top10_set'):.3f} | - | "
          f"{max(r['max_score_diff'] for r in img_rows):.2e} | - | "
          f"{avg(img_rows,'c_recall10'):.3f} | {avg(img_rows,'m_recall10'):.3f} |")
    print(f"\n图片自检索 self_recall@1:Chroma {pct(img_rows,'self_top1_c'):.3f} · Milvus {pct(img_rows,'self_top1_m'):.3f}")

    # 判定标准:两个后端都是近似索引(HNSW),彼此逐条相同并不是合理要求——
    # Chroma 默认搜索宽度很小,不带过滤时自己也会漏掉精确近邻。所以以精确解为准:
    # Milvus 在任何一条 query 上的召回都不能低于 Chroma,且共同命中的分数必须一致。
    eps = 1e-9
    worse = [r for r in rows if r["m_recall10"] < r["c_recall10"] - eps or r["m_recallK"] < r["c_recallK"] - eps]
    better = [r for r in rows if r["m_recall10"] > r["c_recall10"] + eps or r["m_recallK"] > r["c_recallK"] + eps]
    score_bad = [r for r in rows if r["max_score_diff"] > SCORE_EPS]
    img_worse = [r for r in img_rows if r["m_recall10"] < r["c_recall10"] - eps or r["max_score_diff"] > SCORE_EPS]
    print(f"\n对精确解:Milvus 更好 {len(better)} 条 · 更差 {len(worse)} 条 · 分数不一致 {len(score_bad)} 条 · 图片更差 {len(img_worse)} 张")
    if worse or score_bad or img_worse:
        for r in (worse + score_bad)[:15]:
            print(f"  - [{r['filter']}] {r['query']!r} n_c={r['n_c']} n_m={r['n_m']} n_exact={r['n_exact']} "
                  f"diff={r['max_score_diff']:.2e} c={r['c_recall10']:.2f}/{r['c_recallK']:.2f} "
                  f"m={r['m_recall10']:.2f}/{r['m_recallK']:.2f} where={r['where'][:100]}")
        return 1
    print("✓ Milvus 在每条 query 上都不差于 Chroma(以精确解为准),共同命中的分数一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
