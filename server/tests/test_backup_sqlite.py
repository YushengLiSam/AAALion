"""tools/backup_sqlite.py + tools/restore_drill.py:用临时 WAL 库测备份 / 校验 / 保留期 / 恢复演练。"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import backup_sqlite as bs  # noqa: E402
import restore_drill as rd  # noqa: E402

NAMES = ("users.db", "preferences.db", "price_watch.db", "repurchase.db", "group_buy.db")


def _make_db(path: Path, rows: int, *, uncheckpointed: bool = True) -> sqlite3.Connection:
    """WAL 模式的库,和线上 *_db.py 一样;返回仍打开的连接,让部分数据留在 -wal 里。"""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")  # 不自动 checkpoint:验证备份会带上 WAL 里的数据
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, user_id TEXT, payload TEXT)")
    conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO items(user_id, payload) VALUES (?, ?)",
                     [(f"phone:1380000{i:04d}", "x" * 50) for i in range(rows)])
    conn.execute("INSERT INTO meta VALUES ('schema', '1')")
    conn.commit()
    if not uncheckpointed:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return conn


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sqlite-backup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.dest = self.tmp / "backups"
        self.conns = []
        for i, name in enumerate(NAMES):
            self.conns.append(_make_db(self.data / name, rows=10 + i))
        self.addCleanup(lambda: [c.close() for c in self.conns])

    def test_backup_captures_wal_data_and_writes_manifest(self) -> None:
        self.assertTrue((self.data / "users.db-wal").stat().st_size > 0)  # 数据确实还在 WAL 里
        before = {n: (self.data / n).read_bytes() for n in NAMES}
        code, manifest = bs.run_backup(self.data, self.dest)
        self.assertEqual(code, 0, manifest["errors"])
        out = Path(manifest["path"])
        self.assertRegex(out.name, r"^\d{8}T\d{6}Z$")
        self.assertEqual(sorted(e["name"] for e in manifest["files"]), sorted(NAMES))
        for e in manifest["files"]:
            self.assertEqual(e["integrity_check"], "ok")
            self.assertEqual(e["tables"]["items"], 10 + NAMES.index(e["name"]))
            self.assertEqual(e["tables"]["meta"], 1)
            self.assertEqual(bs.sha256_file(out / e["file"]), e["gz_sha256"])
            # 解压后是独立可用的库(不依赖 -wal)
            raw = self.tmp / ("check-" + e["name"])
            raw.write_bytes(gzip.decompress((out / e["file"]).read_bytes()))
            c = sqlite3.connect(raw)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM items").fetchone()[0], e["tables"]["items"])
            c.close()
        sums = (out / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), len(NAMES))
        on_disk = json.loads((out / "manifest.json").read_text())
        self.assertEqual(on_disk["files"], manifest["files"])
        # 源库只读:主文件字节不变
        self.assertEqual({n: (self.data / n).read_bytes() for n in NAMES}, before)
        self.assertEqual(oct(self.dest.stat().st_mode & 0o777), "0o700")

    def test_missing_expected_db_fails_but_backs_up_the_rest(self) -> None:
        self.conns[0].close()
        for suffix in ("", "-wal", "-shm"):
            p = self.data / f"users.db{suffix}"
            if p.exists():
                p.unlink()
        code, manifest = bs.run_backup(self.data, self.dest)
        self.assertEqual(code, 1)
        self.assertTrue(any("users.db: missing" in e for e in manifest["errors"]))
        self.assertEqual(len(manifest["files"]), len(NAMES) - 1)

    def test_corrupt_source_is_reported(self) -> None:
        bad = self.data / "broken.db"
        bad.write_bytes(b"this is not a sqlite database" * 100)
        code, manifest = bs.run_backup(self.data, self.dest, expected=())
        self.assertEqual(code, 1)
        self.assertTrue(any(e.startswith("broken.db:") for e in manifest["errors"]))

    def test_retention_keeps_14_days_and_always_the_newest(self) -> None:
        now = dt.datetime(2026, 10, 30, 3, 30, tzinfo=dt.timezone.utc)
        self.dest.mkdir()
        for days in (1, 13, 15, 40):
            (self.dest / bs.utc_stamp(now - dt.timedelta(days=days))).mkdir()
        stale_partial = self.dest / f".{bs.utc_stamp(now - dt.timedelta(days=3))}.partial"
        stale_partial.mkdir()
        unrelated = self.dest / "keep-me"
        unrelated.mkdir()
        removed = bs.prune(self.dest, 14, now)
        left = sorted(p.name for p in self.dest.iterdir())
        self.assertIn(bs.utc_stamp(now - dt.timedelta(days=1)), left)
        self.assertIn(bs.utc_stamp(now - dt.timedelta(days=13)), left)
        self.assertNotIn(bs.utc_stamp(now - dt.timedelta(days=15)), left)
        self.assertNotIn(bs.utc_stamp(now - dt.timedelta(days=40)), left)
        self.assertIn("keep-me", left)
        self.assertIn(stale_partial.name, removed)
        # 只剩一份很旧的备份时也不删
        only = self.tmp / "only"
        only.mkdir()
        (only / bs.utc_stamp(now - dt.timedelta(days=100))).mkdir()
        self.assertEqual(bs.prune(only, 14, now), [])

    def test_restore_drill_passes_strict_when_nothing_changed(self) -> None:
        code, manifest = bs.run_backup(self.data, self.dest)
        self.assertEqual(code, 0)
        work = self.tmp / "work"
        work.mkdir()
        report = rd.drill(rd.latest_backup(self.dest), self.data, work, strict=True)
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["drift"], [])
        for name in NAMES:
            self.assertEqual(report["dbs"][name]["integrity_check"], "ok")
            self.assertEqual(report["dbs"][name]["restored_rows"], report["dbs"][name]["live_rows"])

    def test_restore_drill_reports_drift_and_detects_tampering(self) -> None:
        bs.run_backup(self.data, self.dest)
        # 备份之后线上又写了几行:默认只记 drift,--strict 判失败
        self.conns[1].execute("INSERT INTO items(user_id, payload) VALUES ('phone:x', 'y')")
        self.conns[1].commit()
        latest = rd.latest_backup(self.dest)
        work = self.tmp / "w1"
        work.mkdir()
        report = rd.drill(latest, self.data, work)
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["drift"], ["preferences.db.items: live=12 restored=11"])
        work2 = self.tmp / "w2"
        work2.mkdir()
        self.assertFalse(rd.drill(latest, self.data, work2, strict=True)["ok"])
        # 篡改一个 gz → SHA256SUMS 校验失败
        gz = latest / "group_buy.db.gz"
        gz.write_bytes(gz.read_bytes()[:-10])
        work3 = self.tmp / "w3"
        work3.mkdir()
        report = rd.drill(latest, self.data, work3)
        self.assertFalse(report["ok"])
        self.assertIn("group_buy.db.gz: sha256 mismatch", report["failures"])

    def test_restore_drill_never_writes_live_dbs(self) -> None:
        bs.run_backup(self.data, self.dest)
        for c in self.conns:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = {p.name: p.read_bytes() for p in self.data.iterdir() if p.suffix == ".db"}
        work = self.tmp / "w"
        work.mkdir()
        rd.drill(rd.latest_backup(self.dest), self.data, work, strict=True)
        after = {p.name: p.read_bytes() for p in self.data.iterdir() if p.suffix == ".db"}
        self.assertEqual(before, after)

    def test_cli_end_to_end(self) -> None:
        res = subprocess.run([sys.executable, str(TOOLS / "backup_sqlite.py"), "--data-dir", str(self.data),
                              "--dest", str(self.dest)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        summary = json.loads(res.stdout)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(set(summary["files"]), set(NAMES))
        res = subprocess.run([sys.executable, str(TOOLS / "restore_drill.py"), "--dest", str(self.dest),
                              "--live-dir", str(self.data), "--strict"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        report = json.loads(res.stdout)
        self.assertTrue(report["ok"])
        self.assertIsNone(report["restore_dir"])  # 临时目录已清理

    def test_restore_drill_without_backups_fails_cleanly(self) -> None:
        res = subprocess.run([sys.executable, str(TOOLS / "restore_drill.py"), "--dest", str(self.tmp / "none"),
                              "--live-dir", str(self.data)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertFalse(json.loads(res.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
