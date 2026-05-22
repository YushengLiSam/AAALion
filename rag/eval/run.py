"""Run the golden eval set against the current retriever.

Usage: ``python -m rag.eval.run``
Reports recall@5 per query and overall.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rag.retrieve.query import query  # noqa: E402


def main() -> int:
    path = Path(__file__).with_name("golden.jsonl")
    total = 0
    hits = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        expected = set(case.get("expected_product_ids", []))
        if not expected:
            print(f"skip (no expected ids): {case['query']!r}")
            continue
        total += 1
        result = query(case["query"], k=5)
        result_ids = {h.product_id for h in result}
        ok = bool(expected & result_ids)
        hits += int(ok)
        print(f"{'PASS' if ok else 'MISS'} {case['query']!r} → {sorted(result_ids)}")
    if total == 0:
        print("no scored cases")
        return 0
    print(f"\nrecall@5: {hits}/{total} = {hits / total:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
