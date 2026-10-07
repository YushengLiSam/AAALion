#!/usr/bin/env python3
"""SQLite 备份恢复演练(P0.3 验收)。只用标准库;对线上库**只读**。

    python3 tools/restore_drill.py                      # 最新一份备份 → 临时目录,逐库核对
    python3 tools/restore_drill.py --backup-dir ~/lionpick-backups/sqlite/20261008T033012Z --strict

步骤:
  1. 选 ``--dest`` 下最新的完整备份(目录名是 UTC 时间戳;``.partial`` 不算);
  2. 校验 SHA256SUMS(gz 文件没被截断 / 篡改);
  3. 解压到临时目录,对每个恢复出来的库跑 ``PRAGMA integrity_check``;
  4. 每张表的行数和 manifest 里记录的行数**必须完全一致**(证明恢复是逐字节忠实的);
  5. 再和线上库(``mode=ro`` 打开,不加锁不写)逐表比行数:
       - 表结构对不上(恢复出的库缺表 / 多表)→ 失败;
       - 行数不同 → 默认只记为 drift(备份之后线上本来就会有新写入),``--strict`` 时判失败。
临时目录默认用完即删(``--keep`` 保留以便手工查看)。结果以 JSON 打印,退出码 0 = 通过。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_sqlite import (  # noqa: E402
    DEFAULT_DATA_DIR,
    DEFAULT_DEST,
    TS_RE,
    integrity,
    open_readonly,
    sha256_file,
    table_counts,
)


def latest_backup(dest: Path) -> Path | None:
    if not dest.is_dir():
        return None
    complete = sorted(p for p in dest.iterdir() if p.is_dir() and TS_RE.match(p.name))
    return complete[-1] if complete else None


def verify_sums(backup_dir: Path) -> list[str]:
    errors = []
    sums = backup_dir / "SHA256SUMS"
    if not sums.exists():
        return ["SHA256SUMS missing"]
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        f = backup_dir / name
        if not f.exists():
            errors.append(f"{name}: missing")
        elif sha256_file(f) != digest:
            errors.append(f"{name}: sha256 mismatch")
    return errors


def drill(backup_dir: Path, live_dir: Path, work_dir: Path, *, strict: bool = False) -> dict:
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    report: dict = {
        "backup": str(backup_dir),
        "backup_created_utc": manifest.get("created_utc"),
        "live_dir": str(live_dir),
        "ok": True,
        "failures": [],
        "drift": [],
        "dbs": {},
    }

    def fail(msg: str) -> None:
        report["ok"] = False
        report["failures"].append(msg)

    for msg in verify_sums(backup_dir):
        fail(msg)
    for msg in manifest.get("errors") or []:
        fail(f"backup recorded error: {msg}")

    for entry in manifest.get("files") or []:
        name = entry["name"]
        gz = backup_dir / entry["file"]
        restored = work_dir / name
        db_report: dict = {}
        report["dbs"][name] = db_report
        try:
            with gzip.open(gz, "rb") as fin, open(restored, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        except (OSError, EOFError) as exc:
            fail(f"{name}: cannot decompress: {exc}")
            continue
        if hashlib.sha256(restored.read_bytes()).hexdigest() != entry.get("db_sha256"):
            fail(f"{name}: restored db sha256 != manifest")
        conn = sqlite3.connect(restored)
        try:
            db_report["integrity_check"] = integrity(conn)
            restored_counts = table_counts(conn)
        except sqlite3.Error as exc:
            fail(f"{name}: cannot open restored db: {exc}")
            conn.close()
            continue
        conn.close()
        db_report["restored_rows"] = restored_counts
        if db_report["integrity_check"] != "ok":
            fail(f"{name}: integrity_check={db_report['integrity_check']}")
        if restored_counts != entry.get("tables"):
            fail(f"{name}: restored row counts {restored_counts} != manifest {entry.get('tables')}")

        live = live_dir / name
        if not live.exists():
            fail(f"{name}: live db not found at {live}")
            continue
        lconn = open_readonly(live)
        try:
            live_counts = table_counts(lconn)
        finally:
            lconn.close()
        db_report["live_rows"] = live_counts
        if set(live_counts) != set(restored_counts):
            fail(f"{name}: table set differs (live={sorted(live_counts)}, restored={sorted(restored_counts)})")
        for table in sorted(set(live_counts) & set(restored_counts)):
            if live_counts[table] != restored_counts[table]:
                msg = f"{name}.{table}: live={live_counts[table]} restored={restored_counts[table]}"
                report["drift"].append(msg)
                if strict:
                    fail(f"row count drift (strict): {msg}")
    if not manifest.get("files"):
        fail("backup contains no databases")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="backup root (picks the latest)")
    ap.add_argument("--backup-dir", type=Path, default=None, help="a specific backup directory")
    ap.add_argument("--live-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--strict", action="store_true", help="fail on any live/restored row-count difference")
    ap.add_argument("--keep", action="store_true", help="keep the temp restore directory")
    ap.add_argument("--out", type=Path, default=None, help="also write the JSON report here")
    args = ap.parse_args(argv)

    backup_dir = args.backup_dir or latest_backup(args.dest.expanduser())
    if backup_dir is None or not backup_dir.is_dir():
        print(json.dumps({"ok": False, "failures": [f"no backup found under {args.dest}"]}))
        return 1
    work = Path(tempfile.mkdtemp(prefix="lionpick-restore-drill-"))
    try:
        report = drill(backup_dir.expanduser().resolve(), args.live_dir.resolve(), work, strict=args.strict)
        report["restore_dir"] = str(work) if args.keep else None
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
