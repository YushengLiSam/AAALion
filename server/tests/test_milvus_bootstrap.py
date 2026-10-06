"""P1 — tools/milvus_bootstrap.py against a fake RBAC server (no Milvus).

Pins: root rotation from the factory default, idempotent re-runs, least
privilege per account, and that passwords never reach stdout/stderr.
The privilege-group names themselves are only verified against Milvus docs,
not a live Standalone (see the module docstring).
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import milvus_bootstrap as mb  # noqa: E402

ROOT_PW = "root-Secret-123456"
APP_PW = "app-Secret-1234567"
ADMIN_PW = "admin-Secret-12345"


class FakeServer:
    def __init__(self, root_pw="Milvus"):
        self.passwords = {"root": root_pw}
        self.roles: dict[str, set] = {}
        self.bindings: dict[str, set] = {}
        self.log: list[tuple] = []


class FakeClient:
    def __init__(self, server: FakeServer, uri: str, token: str):
        user, pw = token.split(":", 1)
        if server.passwords.get(user) != pw:
            raise PermissionError("auth failed")
        self.s, self.user = server, user

    def list_users(self):
        return list(self.s.passwords)

    def update_password(self, user, old, new, reset_connection=False):
        assert self.s.passwords[user] == old
        self.s.passwords[user] = new
        self.s.log.append(("update_password", user))

    def create_user(self, user, pw):
        self.s.passwords[user] = pw
        self.s.log.append(("create_user", user))

    def drop_user(self, user):
        self.s.passwords.pop(user)
        self.s.log.append(("drop_user", user))

    def list_roles(self):
        return list(self.s.roles)

    def create_role(self, role):
        self.s.roles[role] = set()

    def grant_privilege_v2(self, role, privilege, collection, db_name=None):
        self.s.roles[role].add((privilege, collection, db_name))

    def grant_role(self, user, role):
        self.s.bindings.setdefault(user, set()).add(role)

    def describe_user(self, user):
        return {"user_name": user, "roles": tuple(self.s.bindings.get(user, ()))}

    def describe_role(self, role, db_name=""):
        return {"role": role, "privileges": [{"privilege": p, "object_name": c, "db_name": d}
                                             for p, c, d in self.s.roles.get(role, ())]}


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.env_file = Path(self.tmp) / ".env"
        self.env_file.write_text(
            f"MILVUS_ROOT_PASSWORD={ROOT_PW}\nLIONPICK_MILVUS_APP_PASSWORD='{APP_PW}'\n"
            f"LIONPICK_MILVUS_ADMIN_PASSWORD=\"{ADMIN_PW}\"\n# comment\n", encoding="utf-8")
        os.chmod(self.env_file, 0o600)
        self.server = FakeServer()
        self._env = patch.dict(os.environ, {k: "" for k in ("MILVUS_ROOT_PASSWORD", "LIONPICK_MILVUS_APP_PASSWORD",
                                                             "LIONPICK_MILVUS_ADMIN_PASSWORD", "MILVUS_OLD_ROOT_PASSWORD")})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def _run(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        factory = lambda uri, token: FakeClient(self.server, uri, token)  # noqa: E731
        with redirect_stdout(out), redirect_stderr(err):
            rc = mb.main(["--uri", "http://127.0.0.1:19530", "--env-file", str(self.env_file), *extra], factory=factory)
        return rc, out.getvalue() + err.getvalue()

    def test_first_run_rotates_root_and_grants_least_privilege(self) -> None:
        rc, output = self._run()
        self.assertEqual(rc, 0, output)
        self.assertEqual(self.server.passwords["root"], ROOT_PW)
        self.assertEqual(self.server.passwords["lionpick_app"], APP_PW)
        app = {p for p, _, _ in self.server.roles["lionpick_app_role"]}
        admin = {p for p, _, _ in self.server.roles["lionpick_admin_role"]}
        self.assertEqual(app, {"CollectionReadOnly", "Load"})  # no write / alias / drop privileges
        self.assertIn("CollectionAdmin", admin)
        self.assertIn("RenameCollection", admin)
        self.assertTrue(all(db == "default" for _, _, db in self.server.roles["lionpick_app_role"]))
        self.assertEqual(self.server.bindings["lionpick_app"], {"lionpick_app_role"})
        for secret in (ROOT_PW, APP_PW, ADMIN_PW):
            self.assertNotIn(secret, output)

    def test_rerun_is_idempotent_and_check_passes(self) -> None:
        self.assertEqual(self._run()[0], 0)
        rc, output = self._run()
        self.assertEqual(rc, 0, output)
        self.assertIn("root: already using the configured password", output)
        self.assertEqual([e for e in self.server.log if e[0] == "create_user"].__len__(), 2)
        rc, output = self._run("--check")
        self.assertEqual(rc, 0, output)
        self.assertIn("OK", output)

    def test_check_reports_missing_accounts(self) -> None:
        self.server.passwords["root"] = ROOT_PW
        rc, output = self._run("--check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING user lionpick_app", output)

    def test_rejects_weak_or_reused_passwords_and_lite_uris(self) -> None:
        self.env_file.write_text(f"MILVUS_ROOT_PASSWORD=short\nLIONPICK_MILVUS_APP_PASSWORD={APP_PW}\n"
                                 f"LIONPICK_MILVUS_ADMIN_PASSWORD={ADMIN_PW}\n")
        self.assertEqual(self._run()[0], 2)
        self.env_file.write_text(f"MILVUS_ROOT_PASSWORD={APP_PW}\nLIONPICK_MILVUS_APP_PASSWORD={APP_PW}\n"
                                 f"LIONPICK_MILVUS_ADMIN_PASSWORD={ADMIN_PW}\n")
        self.assertEqual(self._run()[0], 2)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(mb.main(["--uri", "data/.milvus/x.db"], factory=lambda **k: None), 2)
        self.assertEqual(self.server.passwords["root"], "Milvus")  # nothing touched

    def test_wrong_old_password_fails_cleanly(self) -> None:
        self.server.passwords["root"] = "someone-else-changed-it"
        rc, output = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("cannot authenticate as root", output)


if __name__ == "__main__":
    unittest.main()
