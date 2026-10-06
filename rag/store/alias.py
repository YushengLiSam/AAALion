"""Milvus 集合别名运维:查看、切换、回滚(P1)。

    python -m rag.store.alias --list
    python -m rag.store.alias --switch products_text__v2_202610061830
    python -m rag.store.alias --rollback                    # text 与 image 都退回上一个版本
    python -m rag.store.alias --rollback --collection text  # 只退 text

连接参数沿用 ``RAG_MILVUS_URI`` / ``RAG_MILVUS_TOKEN`` / ``RAG_MILVUS_DB``;
在 Standalone 上需要用有 CreateAlias / DropAlias 权限的账号(``lionpick_admin``),
只读的 ``lionpick_app`` 会被服务端拒绝——这正是想要的。

切换前会先 load 目标集合并校验"非空、schema v2 字段齐全",校验不过就不切。
别名切换对正在服务的进程是透明的(读路径一直用逻辑名);进程内按集合名缓存的
条数只影响 HNSW 搜索宽度的选择,不影响结果正确性,最多 ``RAG_MILVUS_COUNT_CACHE_TTL`` 秒后自然过期。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rag.store.base import IMAGE_COLLECTION, TEXT_COLLECTION  # noqa: E402

_LABELS = {"text": TEXT_COLLECTION, "image": IMAGE_COLLECTION}


def _store(uri: str | None):
    from rag.store.milvus_store import MilvusStore

    return MilvusStore(uri=uri)


def main(argv: list[str] | None = None, store=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="show alias targets and retained physical collections")
    g.add_argument("--switch", metavar="PHYSICAL", help="point the matching alias at this physical collection")
    g.add_argument("--rollback", action="store_true", help="point alias(es) back at the previous physical collection")
    ap.add_argument("--collection", choices=("text", "image", "all"), default="all", help="for --rollback")
    ap.add_argument("--uri", default=None, help="override RAG_MILVUS_URI")
    ap.add_argument("--json", action="store_true", help="machine-readable output for --list")
    args = ap.parse_args(argv)

    st = store if store is not None else _store(args.uri)

    if args.list:
        status = st.alias_status()
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        for logical, info in status.items():
            target = info["alias_target"]
            if target:
                print(f"{logical}  ->  {target}")
            elif info["unversioned_collection"]:
                print(f"{logical}  (plain collection, no alias — not versioned yet)")
            else:
                print(f"{logical}  (missing)")
            for row in info["physical"]:
                mark = "*" if row["serving"] else " "
                rows = "?" if row["rows"] is None else row["rows"]
                print(f"   {mark} {row['name']}  rows={rows}")
        return 0

    if args.switch:
        try:
            previous = st.activate(args.switch)
        except Exception as exc:
            print(f"switch failed: {exc}", file=sys.stderr)
            return 1
        print(f"switched: {args.switch} (previous: {previous})")
        return 0

    labels = ("text", "image") if args.collection == "all" else (args.collection,)
    rc = 0
    for label in labels:
        logical = _LABELS[label]
        try:
            old, new = st.rollback_alias(logical)
        except Exception as exc:
            print(f"[{label}] rollback failed: {exc}", file=sys.stderr)
            rc = 1
            continue
        print(f"[{label}] {logical}: {old} -> {new}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
