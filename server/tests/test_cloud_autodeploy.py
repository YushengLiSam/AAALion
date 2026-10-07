"""tools/cloud-autodeploy.sh 的端到端测试(不用 bats)。

在临时目录里搭一个"线上 VM":
  * origin.git(裸仓库)+ vm/(VM 上的 git clone)+ dev/(开发者推新提交);
  * 假的 curl:check-runs API 按 FAKE_CI 返回 green / pending / failed / missing,或模拟网络不通;
    /ready 按 FAKE_READY 返回 2xx 或失败;每次调用记到 curl.log;
  * 假的 systemctl:只把参数记到 systemctl.log;
  * AUTODEPLOY_SUDO="" + AUTODEPLOY_DROPIN_DIR 指向临时目录,代替 /etc/systemd/system/lionpick.service.d。
真的 git、真的 bash 脚本,只把外部副作用换成桩。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "cloud-autodeploy.sh"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DROPIN_REL = "deploy/systemd/lionpick.service.d"
REQUIRED = ["pytest (py3.10)", "pytest (py3.11)", "retrieval eval gate"]

HAS_TOOLS = shutil.which("bash") is not None and shutil.which("git") is not None

FAKE_CURL = r"""#!/usr/bin/env bash
# 假 curl:解析 -o / -w,按 URL 分流。
out=""; url=""; want_code=0
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) want_code=1; shift 2 ;;
    -H|--max-time) shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
echo "$url" >> "$FAKE_LOG_DIR/curl.log"
case "$url" in
  *"/check-runs"*)
    if [ "$FAKE_CI" = "unreachable" ]; then
      [ "$want_code" = 1 ] && printf '000'
      exit 28
    fi
    if [ "$FAKE_CI" = "ratelimited" ]; then
      echo '{"message":"API rate limit exceeded"}' > "${out:-/dev/stdout}"
      printf '403'; exit 0
    fi
    cp "$FAKE_FIXTURES/$FAKE_CI.json" "${out:-/dev/stdout}"
    [ "$want_code" = 1 ] && printf '200'
    exit 0 ;;
  *"/ready"*)
    if [ "$FAKE_READY" = "ok" ]; then echo '{"status":"ready"}'; exit 0; fi
    exit 22 ;;
esac
exit 1
"""

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG_DIR/systemctl.log"
"""


def _run(cmd, cwd, env=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, check=check, capture_output=True, text=True)


def _check_runs(statuses: dict[str, tuple[str, str | None]]) -> dict:
    runs = []
    for i, (name, (status, conclusion)) in enumerate(statuses.items(), 1):
        runs.append({"id": 100 + i, "name": name, "status": status, "conclusion": conclusion,
                     "app": {"slug": "github-actions"}})
    # 一个无关 workflow 的失败 check run:不应影响判定
    runs.append({"id": 999, "name": "Build IPA", "status": "completed", "conclusion": "failure"})
    return {"total_count": len(runs), "check_runs": runs}


@unittest.skipUnless(HAS_TOOLS, "bash and git are required")
class AutodeployScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="autodeploy-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.git_env = dict(os.environ)
        self.git_env.update({
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        })
        (self.tmp / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")

        # origin + dev(初始提交 A)
        _run(["git", "init", "-q", "--bare", "origin.git"], self.tmp, self.git_env)
        _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], self.tmp / "origin.git", self.git_env)
        _run(["git", "clone", "-q", str(self.tmp / "origin.git"), "dev"], self.tmp, self.git_env)
        self.dev = self.tmp / "dev"
        self._write_dropin("05-a.conf", "[Service]\nEnvironment=A=old\n")
        self.sha_a = self._commit_and_push("A")

        # VM 上的 clone,停在 A
        _run(["git", "clone", "-q", str(self.tmp / "origin.git"), "vm"], self.tmp, self.git_env)
        self.vm = self.tmp / "vm"

        # /etc 里的 drop-in 现状:与 A 一致的 05-a.conf + 机器本地的 jwt.conf(不受管)
        self.dropin_dir = self.tmp / "etc-dropins"
        self.dropin_dir.mkdir()
        (self.dropin_dir / "05-a.conf").write_text("[Service]\nEnvironment=A=old\n")
        (self.dropin_dir / "jwt.conf").write_text("[Service]\nEnvironment=LIONPICK_JWT_SECRET=s3cret\n")

        # 新提交 B:改 05-a,新增 30-b
        self._write_dropin("05-a.conf", "[Service]\nEnvironment=A=new\n")
        self._write_dropin("30-b.conf", "[Service]\nEnvironment=B=1\n")
        self.sha_b = self._commit_and_push("B")

        # 桩
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("curl", FAKE_CURL), ("systemctl", FAKE_SYSTEMCTL)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.logs = self.tmp / "logs"
        self.logs.mkdir()
        self.fixtures = self.tmp / "fixtures"
        self.fixtures.mkdir()
        fx = {
            "green": {n: ("completed", "success") for n in REQUIRED},
            "pending": {**{n: ("completed", "success") for n in REQUIRED}, REQUIRED[2]: ("in_progress", None)},
            "failed": {**{n: ("completed", "success") for n in REQUIRED}, REQUIRED[0]: ("completed", "failure")},
            "missing": {REQUIRED[0]: ("completed", "success")},
            "cancelled": {**{n: ("completed", "success") for n in REQUIRED}, REQUIRED[1]: ("completed", "cancelled")},
        }
        for name, statuses in fx.items():
            (self.fixtures / f"{name}.json").write_text(json.dumps(_check_runs(statuses)))
        self.state = self.tmp / "state"
        self.state.mkdir()

    # -- helpers ---------------------------------------------------------------
    def _write_dropin(self, name: str, text: str) -> None:
        d = self.dev / DROPIN_REL
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)

    def _commit_and_push(self, msg: str) -> str:
        _run(["git", "add", "-A"], self.dev, self.git_env)
        _run(["git", "commit", "-q", "-m", msg], self.dev, self.git_env)
        _run(["git", "push", "-q", "origin", "HEAD:main"], self.dev, self.git_env)
        return _run(["git", "rev-parse", "HEAD"], self.dev, self.git_env).stdout.strip()

    def _deploy(self, ci: str = "green", ready: str = "ok", *args: str, **extra: str) -> subprocess.CompletedProcess:
        env = dict(self.git_env)
        env.update({
            "PATH": f"{self.bin}{os.pathsep}{env.get('PATH', '')}",
            "LIONPICK_REPO": str(self.vm),
            "AUTODEPLOY_STATE_DIR": str(self.state),
            "AUTODEPLOY_DROPIN_DIR": str(self.dropin_dir),
            "AUTODEPLOY_SUDO": "",
            "AUTODEPLOY_PYTHON": sys.executable,
            "AUTODEPLOY_READY_POLLS": "2",
            "AUTODEPLOY_READY_INTERVAL": "0",
            "FAKE_CI": ci,
            "FAKE_READY": ready,
            "FAKE_LOG_DIR": str(self.logs),
            "FAKE_FIXTURES": str(self.fixtures),
        })
        env.update(extra)
        return _run(["bash", str(SCRIPT), *args], self.vm, env, check=False)

    def _head(self) -> str:
        return _run(["git", "rev-parse", "HEAD"], self.vm, self.git_env).stdout.strip()

    def _calls(self, name: str) -> list[str]:
        p = self.logs / f"{name}.log"
        return p.read_text().splitlines() if p.exists() else []

    def _marker(self) -> str | None:
        p = self.state / ".lionpick-autodeploy-bad-sha"
        return p.read_text().strip() if p.exists() else None

    def _dropins(self) -> dict[str, str]:
        return {p.name: p.read_text() for p in sorted(self.dropin_dir.iterdir())}

    # -- CI gate ---------------------------------------------------------------
    def test_green_deploys_and_syncs_dropins(self) -> None:
        res = self._deploy("green", "ok")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self._head(), self.sha_b)
        self.assertIn("deploy OK", res.stdout)
        drop = self._dropins()
        self.assertEqual(drop["05-a.conf"], "[Service]\nEnvironment=A=new\n")
        self.assertEqual(drop["30-b.conf"], "[Service]\nEnvironment=B=1\n")
        self.assertIn("LIONPICK_JWT_SECRET", drop["jwt.conf"])  # 不受管文件原样保留
        # daemon-reload 必须在 restart 之前
        self.assertEqual(self._calls("systemctl"), ["daemon-reload", "restart lionpick"])
        self.assertIsNone(self._marker())
        api_calls = [u for u in self._calls("curl") if "/check-runs" in u]
        self.assertEqual(api_calls, [
            f"https://api.github.com/repos/YushengLiSam/AAALion/commits/{self.sha_b}/check-runs?per_page=100"
        ])
        managed = (self.state / ".lionpick-autodeploy-dropins").read_text().split()
        self.assertEqual(managed, ["05-a.conf", "30-b.conf"])

    def test_pending_skips_this_tick_without_marking(self) -> None:
        res = self._deploy("pending", "ok")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(self._head(), self.sha_a)
        self.assertIn("CI pending", res.stdout)
        self.assertIn("retrieval eval gate=in_progress", res.stdout)
        self.assertEqual(self._calls("systemctl"), [])
        self.assertIsNone(self._marker())
        # 下一轮变绿 → 部署
        res = self._deploy("green", "ok")
        self.assertEqual(self._head(), self.sha_b)

    def test_missing_required_check_counts_as_pending(self) -> None:
        res = self._deploy("missing", "ok")
        self.assertEqual(self._head(), self.sha_a)
        self.assertIn("pytest (py3.11)=missing", res.stdout)
        self.assertIsNone(self._marker())

    def test_cancelled_counts_as_pending_not_failure(self) -> None:
        res = self._deploy("cancelled", "ok")
        self.assertEqual(self._head(), self.sha_a)
        self.assertIn("CI pending", res.stdout)
        self.assertIsNone(self._marker())

    def test_failed_ci_marks_bad_and_never_deploys(self) -> None:
        res = self._deploy("failed", "ok")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(self._head(), self.sha_a)
        self.assertIn("CI FAILED", res.stdout)
        self.assertEqual(self._marker(), self.sha_b)
        self.assertEqual(self._calls("systemctl"), [])
        # 下一轮:已知坏 SHA,连 API 都不查
        n_api = len([u for u in self._calls("curl") if "/check-runs" in u])
        res = self._deploy("green", "ok")
        self.assertIn("known-bad", res.stdout)
        self.assertEqual(self._head(), self.sha_a)
        self.assertEqual(len([u for u in self._calls("curl") if "/check-runs" in u]), n_api)

    def test_unreachable_api_never_deploys_blind(self) -> None:
        res = self._deploy("unreachable", "ok")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(self._head(), self.sha_a)
        self.assertIn("NOT deploying blind", res.stdout)
        self.assertIsNone(self._marker())
        self.assertEqual(self._calls("systemctl"), [])

    def test_rate_limited_api_is_treated_as_unreachable(self) -> None:
        res = self._deploy("ratelimited", "ok")
        self.assertEqual(self._head(), self.sha_a)
        self.assertIn("http=403", res.stdout)
        self.assertIsNone(self._marker())

    def test_override_skips_ci_check(self) -> None:
        res = self._deploy("unreachable", "ok", AUTODEPLOY_REQUIRE_CI="0")
        self.assertEqual(self._head(), self.sha_b)
        self.assertIn("WITHOUT checking CI", res.stdout)
        self.assertEqual([u for u in self._calls("curl") if "/check-runs" in u], [])

    # -- ready-check rollback ---------------------------------------------------
    def test_failed_ready_rolls_back_code_and_dropins(self) -> None:
        before = self._dropins()
        res = self._deploy("green", "fail")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("rolling back", res.stdout)
        self.assertEqual(self._head(), self.sha_a)
        self.assertEqual(self._marker(), self.sha_b)
        self.assertEqual(self._dropins(), before)  # 05-a 恢复旧内容,30-b 被删,jwt.conf 不动
        self.assertEqual(self._calls("systemctl"),
                         ["daemon-reload", "restart lionpick", "daemon-reload", "restart lionpick"])
        self.assertFalse((self.state / ".lionpick-autodeploy-dropins").exists())

    def test_up_to_date_is_a_noop(self) -> None:
        self._deploy("green", "ok")
        (self.logs / "systemctl.log").unlink()
        res = self._deploy("green", "ok")
        self.assertEqual(res.stdout, "")
        self.assertEqual(self._calls("systemctl"), [])

    # -- drop-in removal / protection ------------------------------------------
    def test_dropin_removed_from_repo_is_removed_only_if_managed(self) -> None:
        self._deploy("green", "ok")
        (self.dev / DROPIN_REL / "30-b.conf").unlink()
        # jwt.conf 即使被误提交进仓库也不能安装
        self._write_dropin("jwt.conf", "[Service]\nEnvironment=LIONPICK_JWT_SECRET=leaked\n")
        sha_c = self._commit_and_push("C")
        res = self._deploy("green", "ok")
        self.assertEqual(self._head(), sha_c, res.stdout + res.stderr)
        drop = self._dropins()
        self.assertNotIn("30-b.conf", drop)
        self.assertIn("s3cret", drop["jwt.conf"])
        self.assertIn("refusing to manage drop-in 'jwt.conf'", res.stderr)


    def test_symlinked_dropin_is_refused(self) -> None:
        # 仓库里的符号链接 drop-in:sudo install 会读链接目标(可能是 root 600 的机密)再装成 644。
        secret = self.tmp / "milvus.secret.env"
        secret.write_text("RAG_MILVUS_TOKEN=lionpick_app:hunter2\n")
        os.symlink(secret, self.dev / DROPIN_REL / "50-evil.conf")
        sha_c = self._commit_and_push("C")
        res = self._deploy("green", "ok")
        self.assertEqual(self._head(), sha_c, res.stdout + res.stderr)
        self.assertNotIn("50-evil.conf", self._dropins())
        self.assertIn("refusing symlinked drop-in '50-evil.conf'", res.stderr)
        self.assertNotIn("hunter2", "".join(self._dropins().values()))

    def test_dropin_sync_failure_reverts_without_restart(self) -> None:
        # sudo 不允许 install(sudoers 收紧过):服务没重启过,只退回代码 + drop-in,不重启。
        fake_sudo = self.bin / "fakesudo"
        fake_sudo.write_text('#!/usr/bin/env bash\n[ "$1" = install ] && exit 1\nexec "$@"\n')
        fake_sudo.chmod(0o755)
        before = self._dropins()
        res = self._deploy("green", "ok", AUTODEPLOY_SUDO=str(fake_sudo))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("drop-in sync FAILED", res.stdout)
        self.assertEqual(self._head(), self.sha_a)
        self.assertEqual(self._marker(), self.sha_b)
        self.assertEqual(self._dropins(), before)
        self.assertNotIn("restart lionpick", self._calls("systemctl"))

    def test_default_sudo_is_non_interactive(self) -> None:
        self.assertIn('SUDO="${AUTODEPLOY_SUDO-sudo -n}"', SCRIPT.read_text(encoding="utf-8"))

    # -- 自举:旧脚本部署了新代码,但没同步 drop-in ------------------------------
    def _bootstrap_old_script_deploy(self) -> None:
        """模拟旧版 autodeploy:只 reset 到 B,不碰 drop-in、不写受管清单。"""
        _run(["git", "fetch", "-q", "origin"], self.vm, self.git_env)
        _run(["git", "reset", "-q", "--hard", "origin/main"], self.vm, self.git_env)

    def test_up_to_date_warns_once_about_dropin_drift(self) -> None:
        self._bootstrap_old_script_deploy()
        res = self._deploy("green", "ok")
        self.assertEqual(res.returncode, 0)
        self.assertIn("installed drop-ins differ from the repo", res.stdout)
        self.assertIn("05-a.conf changed", res.stdout)
        self.assertIn("30-b.conf missing", res.stdout)
        self.assertIn("--resync-dropins", res.stdout)
        self.assertEqual(self._calls("systemctl"), [])  # 只提示,不动线上
        self.assertEqual(self._dropins()["05-a.conf"], "[Service]\nEnvironment=A=old\n")
        res = self._deploy("green", "ok")
        self.assertEqual(res.stdout, "")  # 同样的漂移不重复刷日志
        self.assertEqual([u for u in self._calls("curl") if "/check-runs" in u], [])

    def test_resync_dropins_applies_and_restarts(self) -> None:
        self._bootstrap_old_script_deploy()
        res = self._deploy("unreachable", "ok", "--resync-dropins")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("resync OK", res.stdout)
        drop = self._dropins()
        self.assertEqual(drop["05-a.conf"], "[Service]\nEnvironment=A=new\n")
        self.assertEqual(drop["30-b.conf"], "[Service]\nEnvironment=B=1\n")
        self.assertIn("s3cret", drop["jwt.conf"])
        self.assertEqual(self._calls("systemctl"), ["daemon-reload", "restart lionpick"])
        self.assertEqual(self._head(), self.sha_b)
        # 再跑一次:已一致,什么都不做
        res = self._deploy("green", "ok", "--resync-dropins")
        self.assertIn("nothing to do", res.stdout)
        self.assertEqual(self._calls("systemctl"), ["daemon-reload", "restart lionpick"])
        # 之后的 up-to-date 轮次不再提示
        self.assertEqual(self._deploy("green", "ok").stdout, "")

    def test_resync_dropins_restores_on_failed_ready(self) -> None:
        self._bootstrap_old_script_deploy()
        before = self._dropins()
        res = self._deploy("green", "fail", "--resync-dropins")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("resync FAILED", res.stdout)
        self.assertEqual(self._dropins(), before)
        self.assertEqual(self._calls("systemctl"),
                         ["daemon-reload", "restart lionpick", "daemon-reload", "restart lionpick"])
        self.assertIsNone(self._marker())  # 手工模式不写坏 SHA 标记
        self.assertEqual(self._head(), self.sha_b)

    def test_unknown_argument_is_rejected(self) -> None:
        res = self._deploy("green", "ok", "--bogus")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self._head(), self.sha_a)


class AutodeployConfigTests(unittest.TestCase):
    def test_required_checks_match_ci_workflow_job_names(self) -> None:
        yaml = __import__("yaml") if _has_yaml() else None
        if yaml is None:
            self.skipTest("PyYAML not installed")
        wf = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
        names = []
        for job in wf["jobs"].values():
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            if "python" in matrix:
                names += [job["name"].replace("${{ matrix.python }}", v) for v in matrix["python"]]
            else:
                names.append(job["name"])
        script = SCRIPT.read_text(encoding="utf-8")
        default = script.split('REQUIRED_CHECKS="${AUTODEPLOY_REQUIRED_CHECKS:-', 1)[1].split('}"', 1)[0]
        self.assertEqual(sorted(default.split(",")), sorted(names))
        self.assertEqual(sorted(names), sorted(REQUIRED))

    def test_ci_never_receives_llm_keys(self) -> None:
        text = CI_YML.read_text(encoding="utf-8")
        self.assertNotIn("secrets.", text)
        for var in ("TOKENROUTER_API_KEY", "ANTHROPIC_API_KEY", "DOUBAO_API_KEY", "OPENAI_API_KEY"):
            self.assertIn(f'{var}: ""', text)
        self.assertIn('RERANK_INPUT_CAP: "10"', text)
        self.assertIn('RERANK_MAX_LENGTH: "128"', text)

    def test_vector_store_dropin_has_exact_production_values(self) -> None:
        text = (REPO_ROOT / DROPIN_REL / "20-vector-store.conf").read_text(encoding="utf-8")
        env = [ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("Environment=")]
        self.assertEqual(env, [
            "RAG_STORE=milvus",
            "RAG_STORE_SHADOW=chroma",
            "RAG_STORE_FALLBACK=chroma",
            "RAG_STORE_FALLBACK_UNTIL=2026-10-21",
            "RAG_CHROMA_DIR=data/.chroma_v3",
        ])

    def test_bind_localhost_resets_execstart_first(self) -> None:
        text = (REPO_ROOT / DROPIN_REL / "30-bind-localhost.conf").read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.startswith("ExecStart")]
        self.assertEqual(lines, [
            "ExecStart=",
            "ExecStart=/home/yushengli/AAALion-/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000",
        ])

    def test_no_secret_dropin_in_repo(self) -> None:
        names = {p.name for p in (REPO_ROOT / DROPIN_REL).iterdir()}
        self.assertNotIn("jwt.conf", names)
        for p in (REPO_ROOT / DROPIN_REL).iterdir():
            self.assertNotIn("LIONPICK_JWT_SECRET=", p.read_text(encoding="utf-8"))

    def test_demo_mode_dropin(self) -> None:
        text = (REPO_ROOT / DROPIN_REL / "40-demo-mode.conf").read_text(encoding="utf-8")
        # 2026-10-07 机主决定生产关闭 dev_code
        self.assertIn("Environment=DEMO_MODE=0", text)
        self.assertNotIn("Environment=DEMO_MODE=1", text)
        self.assertIn("Environment=AUTH_ENFORCE_MODE=report", text)

    def test_image_dropin(self) -> None:
        text = (REPO_ROOT / DROPIN_REL / "50-image.conf").read_text(encoding="utf-8")
        # VM 实测文字路让只发照片的请求多出 1.5 s(p95 6.5 s),生产只在有描述性文字时跑文字路
        envs = [ln for ln in text.splitlines() if ln.startswith("Environment=")]
        self.assertEqual(envs, ["Environment=IMAGE_TEXT_PATH_PHOTO_ONLY=0"])


def _has_yaml() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()
