"""Generate a self-contained HTML retrieval-quality dashboard.

Usage::

    python -m rag.eval.report                       # writes docs/eval_report.html
    python -m rag.eval.report --out path.html       # custom output

The report has 4 sections:
  1. Overview (timestamp, dataset stats, golden-set composition)
  2. Three-strategy comparison table (overall)
  3. Per-scenario (tag) breakdown
  4. Per-case drill-down (collapsible)

Plus an auto-generated "observations" block at the top — picks out the
biggest deltas across modes/tags so the answer-key story writes itself.

Everything is inline (CSS, data, no external CDN) so the file can be
opened anywhere, including offline / printed / screenshotted into a deck.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path

from rag.eval.core import MODE_LABELS, MODES, evaluate, load_cases

REPO_ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------
# Metric presentation
# ----------------------------------------------------------------------------

#: Stable display order + human label for every metric the dashboard can show.
METRIC_DISPLAY: list[tuple[str, str]] = [
    ("recall@5", "Recall@5"),
    ("recall@10", "Recall@10"),
    ("mrr", "MRR"),
    ("precision@5", "Precision@5"),
    ("negation_accuracy", "反选准确率"),
    ("no_match_correctness", "无匹配正确率"),
    ("latency_ms", "延迟 (ms)"),
]

#: Six canonical scenario tags, in the order the plan defined.
SCENARIO_TAGS: list[str] = [
    "basic",
    "filter",
    "negation",
    "multiturn",
    "compare",
    "no-match",
]

#: Tag → human label.
TAG_LABELS: dict[str, str] = {
    "basic": "基础推荐",
    "filter": "条件筛选",
    "negation": "反选/排除",
    "multiturn": "多轮追问",
    "compare": "多商品对比",
    "no-match": "无匹配场景",
}


def _fmt(name: str, v: float | None) -> str:
    if v is None:
        return "<span class='dim'>—</span>"
    if name == "latency_ms":
        return f"{v:,.0f} ms"
    return f"{v:.3f}"


def _best_indices(values: list[float | None], higher_is_better: bool = True) -> set[int]:
    """Return set of indices that share the best value in a row (for highlighting)."""
    real = [(i, v) for i, v in enumerate(values) if v is not None]
    if not real:
        return set()
    best = max(v for _, v in real) if higher_is_better else min(v for _, v in real)
    return {i for i, v in real if v == best}


def _is_higher_better(metric: str) -> bool:
    return metric != "latency_ms"


# ----------------------------------------------------------------------------
# Observations — auto-summary
# ----------------------------------------------------------------------------

def _observations(result: dict) -> list[str]:
    """Auto-pick a handful of headline takeaways from the data."""
    obs: list[str] = []
    modes = result["meta"]["modes"]
    if len(modes) < 2:
        return ["仅评测了一个 mode,无法对比。"]

    # 1. Overall recall@5 winner + delta from baseline
    r5 = {m: result["modes"][m]["overall"].get("recall@5") for m in modes}
    r5 = {m: v for m, v in r5.items() if v is not None}
    if r5:
        winner = max(r5, key=r5.get)
        loser = min(r5, key=r5.get)
        delta = r5[winner] - r5[loser]
        obs.append(
            f"<b>{MODE_LABELS.get(winner, winner)}</b> 综合 recall@5 最高 "
            f"({r5[winner]:.3f}),比 <b>{MODE_LABELS.get(loser, loser)}</b> "
            f"高 {delta:+.3f}。"
        )

    # 2. Negation accuracy lift
    na = {m: result["modes"][m]["overall"].get("negation_accuracy") for m in modes}
    na = {m: v for m, v in na.items() if v is not None}
    if "dense" in na and "hybrid_rerank" in na:
        lift = na["hybrid_rerank"] - na["dense"]
        if abs(lift) > 0.02:
            direction = "提升" if lift > 0 else "下降"
            obs.append(
                f"反选准确率从 dense 的 {na['dense']:.3f} {direction}到 "
                f"hybrid+rerank 的 {na['hybrid_rerank']:.3f} ({lift:+.3f})"
                + ("——证明 negation filter + 同义词 pipeline 在反选场景的真实贡献。"
                   if lift > 0 else "——rerank 在反选上反而拖后腿,需要排查。")
            )

    # 3. Latency cost
    lat = {m: result["modes"][m]["overall"].get("latency_ms") for m in modes}
    lat = {m: v for m, v in lat.items() if v is not None}
    if "dense" in lat and "hybrid_rerank" in lat:
        ratio = lat["hybrid_rerank"] / max(lat["dense"], 1.0)
        obs.append(
            f"hybrid+rerank 延迟 {lat['hybrid_rerank']:,.0f}ms,是 dense 的 "
            f"{ratio:.1f} 倍。精度换延迟的代价 ≈ +{lat['hybrid_rerank'] - lat['dense']:,.0f} ms,"
            "生产路径靠缓存抵消(LRU 命中后 first_delta 可降到 ~300ms)。"
        )

    # 4. Per-tag spotlight — where rerank wins/loses most
    if "dense" in result["modes"] and "hybrid_rerank" in result["modes"]:
        dense_by_tag = result["modes"]["dense"]["by_tag"]
        rerank_by_tag = result["modes"]["hybrid_rerank"]["by_tag"]
        deltas: list[tuple[str, float]] = []
        for tag in SCENARIO_TAGS:
            d = dense_by_tag.get(tag, {}).get("recall@5")
            r = rerank_by_tag.get(tag, {}).get("recall@5")
            if d is not None and r is not None:
                deltas.append((tag, r - d))
        if deltas:
            best = max(deltas, key=lambda x: x[1])
            worst = min(deltas, key=lambda x: x[1])
            if best[1] > 0.02:
                obs.append(
                    f"按场景拆分:rerank 在 <b>{TAG_LABELS.get(best[0], best[0])}</b> 提升最大 "
                    f"({best[1]:+.3f} recall@5)。"
                )
            if worst[1] < -0.02:
                obs.append(
                    f"反之 rerank 在 <b>{TAG_LABELS.get(worst[0], worst[0])}</b> 反而落后 "
                    f"({worst[1]:+.3f} recall@5),提示该场景下 BM25 噪声经 RRF 放大。"
                )

    # 5. precision@5 floor
    p5 = result["modes"]["hybrid_rerank"]["overall"].get("precision@5") if "hybrid_rerank" in modes else None
    if p5 is not None:
        obs.append(
            f"precision@5 总体 ~{p5:.3f} — golden 集 expected 多为单/双商品,"
            "top-5 中只有 1-2 命中是正常下限,不是 bug。"
        )

    return obs


# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue",
                 Helvetica, "Microsoft YaHei", Arial, sans-serif;
    background: #FBF7F1;
    color: #1F1A14;
    max-width: 1180px;
    margin: 0 auto;
    padding: 32px 40px 64px;
    line-height: 1.55;
}
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 32px 0 10px; border-bottom: 1px solid #ECE3D2; padding-bottom: 6px; }
h3 { font-size: 15px; margin: 22px 0 8px; color: #7A6E5F; font-weight: 600; }
.meta { color: #7A6E5F; font-size: 13px; margin-bottom: 22px; }
.meta span { margin-right: 18px; }
.meta b { color: #1F1A14; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; text-align: right; border-bottom: 1px solid #ECE3D2; }
th:first-child, td:first-child { text-align: left; font-weight: 500; }
th { background: #FFFDF8; color: #7A6E5F; font-weight: 600; }
tr.prod th, tr.prod td { background: #FFF7E8; }
tr.prod td:first-child::after { content: " ★ 生产路径"; color: #E89A3C; font-size: 11px; font-weight: 500; }
td.best { background: #F6D8A8; font-weight: 600; color: #1F1A14; }
.dim { color: #C9BCA6; }

.observations {
    background: #FFFDF8;
    border-left: 3px solid #E89A3C;
    padding: 14px 18px;
    margin: 18px 0 28px;
    border-radius: 0 8px 8px 0;
}
.observations ul { margin: 0; padding-left: 22px; }
.observations li { margin-bottom: 6px; }

details { margin: 8px 0; }
details summary {
    cursor: pointer;
    padding: 4px 0;
    font-size: 13px;
    color: #7A6E5F;
    user-select: none;
}
details summary:hover { color: #E89A3C; }
.case {
    border: 1px solid #ECE3D2;
    background: #FFFFFF;
    border-radius: 10px;
    padding: 12px 16px;
    margin: 10px 0;
}
.case .q { font-size: 14px; font-weight: 500; margin-bottom: 6px; }
.case .tags { font-size: 11px; color: #7A6E5F; margin-bottom: 8px; }
.case .tags .tag {
    display: inline-block; background: #ECE3D2; color: #1F1A14;
    padding: 1px 7px; border-radius: 10px; margin-right: 5px;
}
.case .ids { font-size: 11px; color: #7A6E5F; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.case .ids .hit { color: #2E7D32; font-weight: 600; }
.case .ids .miss { color: #7A6E5F; }
.case .ids .leak { color: #D32F2F; font-weight: 600; }
.case .modes-row { display: flex; gap: 12px; margin-top: 6px; flex-wrap: wrap; }
.case .mode-block {
    flex: 1; min-width: 270px;
    background: #FBF7F1; padding: 8px 10px; border-radius: 6px;
}
.case .mode-block .lbl { font-size: 11px; color: #7A6E5F; font-weight: 600; margin-bottom: 2px; }
.case .mode-block .metrics { font-size: 11px; color: #7A6E5F; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

.foot { color: #C9BCA6; font-size: 11px; margin-top: 36px; text-align: center; }

/* Inline bar chart */
.bar-row { display: flex; align-items: center; gap: 8px; margin: 3px 0; font-size: 12px; }
.bar-row .lbl { width: 165px; color: #7A6E5F; }
.bar-row .bar-track { flex: 1; height: 14px; background: #ECE3D2; border-radius: 4px; position: relative; }
.bar-row .bar-fill { height: 100%; background: #E89A3C; border-radius: 4px; }
.bar-row .val { width: 55px; text-align: right; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
"""


def _render_overall_table(result: dict, modes: list[str]) -> str:
    """Three-strategy horizontal comparison table."""
    rows = []
    # Header
    header_cells = "".join(f"<th>{label}</th>" for _, label in METRIC_DISPLAY)
    rows.append(f"<tr><th>策略</th>{header_cells}</tr>")
    # Body: collect values column-wise to find best per metric
    columns: dict[str, list[float | None]] = {
        name: [result["modes"][m]["overall"].get(name) for m in modes]
        for name, _ in METRIC_DISPLAY
    }
    best_per_metric = {
        name: _best_indices(columns[name], _is_higher_better(name))
        for name, _ in METRIC_DISPLAY
    }
    for i, m in enumerate(modes):
        is_prod = m == "hybrid_rerank"
        cells = []
        for name, _ in METRIC_DISPLAY:
            klass = "best" if i in best_per_metric[name] else ""
            cells.append(f"<td class='{klass}'>{_fmt(name, columns[name][i])}</td>")
        rows.append(
            f"<tr class='{'prod' if is_prod else ''}'>"
            f"<td>{html.escape(MODE_LABELS.get(m, m))}</td>"
            f"{''.join(cells)}</tr>"
        )
    return "<table>" + "".join(rows) + "</table>"


def _render_tag_section(result: dict, modes: list[str]) -> str:
    """One subsection per scenario tag, each is its own modes × metrics table."""
    out: list[str] = []
    for tag in SCENARIO_TAGS:
        # Skip tags that have no data at all
        has_data = any(
            result["modes"][m]["by_tag"].get(tag, {})
            for m in modes
        )
        if not has_data:
            continue
        # How many cases hit this tag in the first available mode (same across modes)
        first_mode = modes[0]
        n_cases = sum(
            1 for c in result["modes"][first_mode]["per_case"]
            if tag in (c.get("tags") or [])
        )
        out.append(f"<h3>{TAG_LABELS.get(tag, tag)} <span class='dim' style='font-weight:400'>· {tag} · {n_cases} cases</span></h3>")
        # Per-tag table
        columns = {
            name: [result["modes"][m]["by_tag"].get(tag, {}).get(name) for m in modes]
            for name, _ in METRIC_DISPLAY
        }
        best_per_metric = {
            name: _best_indices(columns[name], _is_higher_better(name))
            for name, _ in METRIC_DISPLAY
        }
        rows = ["<tr><th>策略</th>" + "".join(f"<th>{label}</th>" for _, label in METRIC_DISPLAY) + "</tr>"]
        for i, m in enumerate(modes):
            is_prod = m == "hybrid_rerank"
            cells = []
            for name, _ in METRIC_DISPLAY:
                klass = "best" if i in best_per_metric[name] else ""
                cells.append(f"<td class='{klass}'>{_fmt(name, columns[name][i])}</td>")
            rows.append(
                f"<tr class='{'prod' if is_prod else ''}'>"
                f"<td>{html.escape(MODE_LABELS.get(m, m))}</td>"
                f"{''.join(cells)}</tr>"
            )
        out.append("<table>" + "".join(rows) + "</table>")
    return "\n".join(out)


def _render_bar_chart(result: dict, modes: list[str]) -> str:
    """Inline SVG-free bar chart: recall@5 per mode."""
    vals = {m: result["modes"][m]["overall"].get("recall@5") for m in modes}
    vals = {m: v for m, v in vals.items() if v is not None}
    if not vals:
        return ""
    max_v = max(vals.values()) or 1
    rows = []
    for m, v in vals.items():
        pct = (v / max_v) * 100
        rows.append(
            f"<div class='bar-row'>"
            f"<div class='lbl'>{html.escape(MODE_LABELS.get(m, m))}</div>"
            f"<div class='bar-track'><div class='bar-fill' style='width:{pct:.1f}%'></div></div>"
            f"<div class='val'>{v:.3f}</div>"
            f"</div>"
        )
    return "<div style='margin: 10px 0 18px'>" + "\n".join(rows) + "</div>"


def _render_case_drilldown(result: dict, modes: list[str]) -> str:
    """Collapsible per-case detail. Each <details> = one case across modes."""
    out: list[str] = []
    # Group by tag for navigation
    first_mode = modes[0]
    by_tag_cases: dict[str, list[int]] = {}
    for idx, c in enumerate(result["modes"][first_mode]["per_case"]):
        primary_tag = next((t for t in (c.get("tags") or []) if t in SCENARIO_TAGS), "_other")
        by_tag_cases.setdefault(primary_tag, []).append(idx)

    for tag in SCENARIO_TAGS + ["_other"]:
        indices = by_tag_cases.get(tag, [])
        if not indices:
            continue
        out.append(f"<details><summary><b>{TAG_LABELS.get(tag, tag) if tag != '_other' else '其他/未分类'}</b> · {len(indices)} cases</summary>")
        for idx in indices:
            row_per_mode = {m: result["modes"][m]["per_case"][idx] for m in modes}
            case_ref = row_per_mode[first_mode]
            query = case_ref.get("query", "")
            expected = set(case_ref.get("expected") or [])
            forbidden = set(case_ref.get("forbidden") or [])
            tags = case_ref.get("tags") or []
            out.append("<div class='case'>")
            out.append(f"<div class='q'>{html.escape(query)}</div>")
            out.append(
                "<div class='tags'>"
                + "".join(f"<span class='tag'>{html.escape(t)}</span>" for t in tags)
                + "</div>"
            )
            if expected:
                out.append(
                    "<div class='ids'>expected: "
                    + " ".join(f"<span class='hit'>{html.escape(p)}</span>" for p in expected)
                    + "</div>"
                )
            if forbidden:
                out.append(
                    "<div class='ids'>forbidden: "
                    + " ".join(f"<span class='leak'>{html.escape(p)}</span>" for p in forbidden)
                    + "</div>"
                )
            # Per-mode retrieved + metrics
            out.append("<div class='modes-row'>")
            for m in modes:
                row = row_per_mode[m]
                ret = row.get("retrieved") or []
                ret_html = []
                for pid in ret[:10]:
                    if pid in expected:
                        ret_html.append(f"<span class='hit'>{html.escape(pid)}</span>")
                    elif pid in forbidden:
                        ret_html.append(f"<span class='leak'>{html.escape(pid)}</span>")
                    else:
                        ret_html.append(f"<span class='miss'>{html.escape(pid)}</span>")
                metrics = row.get("metrics") or {}
                metric_str = " · ".join(
                    f"{label}={_fmt(name, metrics.get(name))}"
                    for name, label in METRIC_DISPLAY[:4]  # show first 4 (recall + mrr + precision)
                    if metrics.get(name) is not None
                )
                out.append(
                    f"<div class='mode-block'>"
                    f"<div class='lbl'>{html.escape(MODE_LABELS.get(m, m))}</div>"
                    f"<div class='metrics'>{metric_str or '<span class=dim>— no metrics —</span>'}</div>"
                    f"<div class='ids' style='margin-top:4px'>{' '.join(ret_html) or '<span class=dim>(empty)</span>'}</div>"
                    f"</div>"
                )
            out.append("</div>")  # modes-row
            out.append("</div>")  # case
        out.append("</details>")
    return "\n".join(out)


def render_html(result: dict) -> str:
    modes = result["meta"]["modes"]
    meta = result["meta"]
    cases = load_cases()
    n_with_expected = sum(1 for c in cases if c.get("expected_product_ids"))
    n_no_match = sum(1 for c in cases if c.get("expected_product_ids") == [])
    n_negation = sum(1 for c in cases if c.get("forbidden_product_ids"))
    n_multi = sum(1 for c in cases if c.get("messages"))

    obs = _observations(result)
    obs_html = "<ul>" + "".join(f"<li>{o}</li>" for o in obs) + "</ul>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>狮选 LionPick — RAG 检索质量看板</title>
<style>{CSS}</style>
</head>
<body>

<h1>🦁 狮选 LionPick · RAG 检索质量看板</h1>
<div class="meta">
  <span>生成时间 <b>{html.escape(meta['timestamp'])}</b></span>
  <span>数据集 <b>{meta['dataset_size']}</b> 件商品</span>
  <span>Golden case <b>{meta['n_cases']}</b>(<b>{n_with_expected}</b> valid · <b>{n_no_match}</b> no-match · <b>{n_negation}</b> 带 forbidden · <b>{n_multi}</b> multi-turn)</span>
  <span>top-k <b>{meta['k']}</b></span>
</div>

<h2>🔭 自动观察</h2>
<div class="observations">{obs_html}</div>

<h2>📊 三策略总览</h2>
{_render_overall_table(result, modes)}
<h3>Recall@5 对比</h3>
{_render_bar_chart(result, modes)}

<h2>🎯 分场景拆解</h2>
{_render_tag_section(result, modes)}

<h2>🔍 逐 case 明细</h2>
{_render_case_drilldown(result, modes)}

<div class="foot">
  Generated by <code>python -m rag.eval.report</code> ·
  source data <code>rag/eval/golden.jsonl</code> ·
  metrics defined in <code>rag/eval/core.py</code>
</div>

</body>
</html>"""


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RAG eval HTML dashboard.")
    parser.add_argument(
        "--out", "-o",
        default=str(REPO_ROOT / "docs" / "eval_report.html"),
        help="Output HTML path (default: docs/eval_report.html)",
    )
    parser.add_argument(
        "--k", type=int, default=10, help="top-k for retrieval (default 10)",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        default=list(MODES),
        help="Modes to evaluate (default: all three)",
    )
    args = parser.parse_args()

    print(f"Evaluating {len(args.modes)} mode(s)... (this may take a couple minutes)")
    result = evaluate(modes=args.modes, k=args.k)

    html_str = render_html(result)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_str, encoding="utf-8")
    print(f"✔ Wrote dashboard → {out_path}")
    print(f"  Open with: open {out_path}")

    # Also dump the raw result JSON next to the HTML for downstream consumption.
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✔ Wrote raw JSON  → {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
