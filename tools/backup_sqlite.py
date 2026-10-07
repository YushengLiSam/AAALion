#!/usr/bin/env python3
"""SQLite 用户数据备份(P0.3)。只用标准库,VM 上直接用系统 python3 跑(不依赖 venv)。

    python3 tools/backup_sqlite.py                    # data/*.db → ~/lionpick-backups/sqlite/<UTC 时间戳>/
    python3 tools/backup_sqlite.py --dest /path --retention-days 14

每个 data/*.db(users / preferences / price_watch / repurchase / group_buy):
  1. 以只读 URI(mode=ro)打开源库,用 sqlite3 的 **在线备份 API**(Connection.backup)拷到临时文件
     ——和 sqlite3 CLI 的 ``.backup`` 是同一个机制:持读锁逐页复制,WAL 里已提交的数据也会带上,
     服务不用停。VM 上没装 sqlite3 CLI,所以用 Python。
  2. 对备份副本跑 ``PRAGMA integrity_check``(必须是 ok),并记下每张表的行数;
  3. gzip 压缩成 ``<name>.db.gz``;
  4. 写 ``manifest.json``(行数 / 大小 / sha256 / integrity)和 ``SHA256SUMS``(可直接 ``sha256sum -c``)。
整批先写进 ``.<时间戳>.partial``,全部成功后再原子 rename,所以目录名是时间戳的一定是完整备份。
保留期默认 14 天(按目录名里的时间戳算),最新的一份永远不删。

退出码:0 成功;1 有库缺失 / integrity_check 不是 ok / 拷贝失败(systemd 会把这次运行标成 failed)。
恢复演练见 tools/restore_drill.py;操作手册见 docs/RUNBOOK_OPS.md §4。
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DEST = Path.home() / "lionpick-backups" / "sqlite"
# 线上应有的 5 个库;缺了就报错(说明路径不对或者数据丢了),而不是默默少备一个。
EXPECTED_DBS = ("users.db", "preferences.db", "price_watch.db", "repurchase.db", "group_buy.db")
TS_FORMAT = "%Y%m%dT%H%M%SZ"
TS_RE = re.compile(r"^\d{8}T\d{6}Z$")


def utc_stamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).strftime(TS_FORMAT)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_readonly(path: Path) -> sqlite3.Connection:
    """只读打开(mode=ro):库不存在时报错而不是新建一个空库。"""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    # 表名来自 sqlite_master,用双引号转义后拼进 SQL。
    return {n: int(conn.execute(f'SELECT COUNT(*) FROM "{n.replace(chr(34), chr(34) * 2)}"').fetchone()[0])
            for n in names}


def integrity(conn: sqlite3.Connection) -> str:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    return "ok" if rows == [("ok",)] else "; ".join(str(r[0]) for r in rows[:20])


def backup_one(src_path: Path, out_dir: Path) -> dict:
    """备份单个库到 out_dir/<name>.db.gz,返回 manifest 条目。"""
    raw = out_dir / (src_path.name + ".tmp")
    src = open_readonly(src_path)
    try:
        dst = sqlite3.connect(raw)
        try:
            src.backup(dst)
            # 备份副本本身是独立文件;切成 DELETE 日志模式,解压后单文件即可打开。
            dst.execute("PRAGMA journal_mode=DELETE")
            check = integrity(dst)
            counts = table_counts(dst)
        finally:
            dst.close()
    finally:
        src.close()
    db_sha = sha256_file(raw)
    db_bytes = raw.stat().st_size
    gz_path = out_dir / (src_path.name + ".gz")
    with open(raw, "rb") as fin, gzip.open(gz_path, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    raw.unlink()
    return {
        "name": src_path.name,
        "file": gz_path.name,
        "integrity_check": check,
        "tables": counts,
        "db_bytes": db_bytes,
        "db_sha256": db_sha,
        "gz_bytes": gz_path.stat().st_size,
        "gz_sha256": sha256_file(gz_path),
    }


def prune(dest: Path, retention_days: float, now: dt.datetime | None = None) -> list[str]:
    """删掉超过保留期的完整备份(最新一份永远保留)和超过 1 天的残留 .partial 目录。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    removed: list[str] = []
    complete = sorted(p for p in dest.iterdir() if p.is_dir() and TS_RE.match(p.name)) if dest.is_dir() else []
    cutoff = now - dt.timedelta(days=retention_days)
    for p in complete[:-1]:  # 最新的一份不参与
        ts = dt.datetime.strptime(p.name, TS_FORMAT).replace(tzinfo=dt.timezone.utc)
        if ts < cutoff:
            shutil.rmtree(p)
            removed.append(p.name)
    for p in (dest.iterdir() if dest.is_dir() else []):
        m = re.match(r"^\.(\d{8}T\d{6}Z)\.partial$", p.name)
        if p.is_dir() and m:
            ts = dt.datetime.strptime(m.group(1), TS_FORMAT).replace(tzinfo=dt.timezone.utc)
            if ts < now - dt.timedelta(days=1):
                shutil.rmtree(p)
                removed.append(p.name)
    return removed


def run_backup(data_dir: Path, dest: Path, *, expected: tuple[str, ...] = EXPECTED_DBS,
               retention_days: float = 14, now: dt.datetime | None = None) -> tuple[int, dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = utc_stamp(now)
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)  # 用户数据:只有部署用户可读
    partial = dest / f".{stamp}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(mode=0o700)

    sources = sorted(p for p in data_dir.glob("*.db") if p.is_file())
    names = {p.name for p in sources}
    missing = [n for n in expected if n not in names]
    entries, errors = [], []
    for src in sources:
        try:
            entry = backup_one(src, partial)
        except sqlite3.Error as exc:
            errors.append(f"{src.name}: {type(exc).__name__}: {exc}")
            continue
        if entry["integrity_check"] != "ok":
            errors.append(f"{src.name}: integrity_check={entry['integrity_check']}")
        entries.append(entry)
    for n in missing:
        errors.append(f"{n}: missing from {data_dir}")

    manifest = {
        "created_utc": now.isoformat(timespec="seconds"),
        "stamp": stamp,
        "host": socket.gethostname(),
        "source_dir": str(data_dir),
        "method": "sqlite3 online backup API (Connection.backup), source opened mode=ro",
        "files": entries,
        "errors": errors,
    }
    (partial / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (partial / "SHA256SUMS").write_text("".join(f"{e['gz_sha256']}  {e['file']}\n" for e in entries), encoding="utf-8")

    final = dest / stamp
    if errors and not entries:
        shutil.rmtree(partial)
        return 1, manifest
    os.replace(partial, final)
    manifest["path"] = str(final)
    manifest["pruned"] = prune(dest, retention_days, now)
    return (1 if errors else 0), manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--retention-days", type=float, default=14)
    ap.add_argument("--expect", default=",".join(EXPECTED_DBS),
                    help="comma-separated DB names that must exist (empty string = no requirement)")
    args = ap.parse_args(argv)
    expected = tuple(s.strip() for s in args.expect.split(",") if s.strip())
    code, manifest = run_backup(args.data_dir.resolve(), args.dest.expanduser().resolve(),
                                expected=expected, retention_days=args.retention_days)
    summary = {
        "path": manifest.get("path"),
        "files": {e["name"]: {"integrity": e["integrity_check"], "rows": sum(e["tables"].values())}
                  for e in manifest["files"]},
        "errors": manifest["errors"],
        "pruned": manifest.get("pruned", []),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
