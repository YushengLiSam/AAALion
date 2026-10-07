#!/usr/bin/env bash
# cloud-autodeploy.sh — pull origin/main onto the prod VM and restart, with
# a CI gate in front and a ready-check rollback guard behind. Driven by
# lionpick-autodeploy.timer (every ~2 min). Safe to run by hand too.
#
# Behaviour:
#   1. git fetch. If origin/main == current HEAD → nothing to do, exit.
#   2. If origin/main is a KNOWN-BAD commit (CI failed, or a prior deploy
#      failed its ready-check) → skip, so we don't loop every 2 min.
#      Cleared automatically when origin/main advances past it (or by hand:
#      rm ~/.lionpick-autodeploy-bad-sha, e.g. after re-running a flaky CI).
#   3. CI 门禁(P0.6):查公开的 GitHub check-runs API(不需要 token,
#      curl --max-time),要求 CI workflow 的每个必需 job 都 completed + success:
#        success     → 继续部署
#        pending     → 本轮跳过(记日志),下一轮再查
#        failure     → 写 bad-SHA 标记并跳过
#        unreachable → 跳过并记日志 —— 永远不"盲部署"
#      紧急情况可用 AUTODEPLOY_REQUIRE_CI=0 跳过这一步(会记一条 WARNING)。
#   4. reset --hard origin/main;把仓库管理的 systemd drop-in
#      (deploy/systemd/lionpick.service.d/*.conf)同步进
#      /etc/systemd/system/lionpick.service.d/,有变化就 daemon-reload;
#      然后 restart lionpick,等 /ready 返回 2xx。
#      If NOT ready within ~150 s → roll back to the previous commit AND the
#      previous drop-ins, daemon-reload, restart, record the bad SHA.
#
# Drop-in 同步规则:
#   * 只处理仓库目录里存在的 *.conf;jwt.conf 等机器本地文件永远不碰
#     (jwt.conf 即使被误提交进仓库也拒绝安装)。
#   * 记录"由本脚本安装过的文件名"(STATE_DIR/.lionpick-autodeploy-dropins);
#     仓库里删掉的 drop-in,只有在这个清单里才会从 /etc 删除。
#   * 部署前给这些文件拍快照;回滚时按快照恢复(内容和"有/无"都恢复)。稳态下快照
#     就等于上一个 SHA 树里的 drop-in;首次接管手工装的文件时也不会误删它们。
#   * 符号链接一律拒绝:sudo install 会以 root 身份读链接目标(比如 mode 600 的
#     /etc/lionpick/milvus.secret.env),再以 644 装进 drop-in 目录 = 泄露机密。
#   * drop-in 同步失败(多半是 sudo 权限不够)时:服务还没重启、仍在跑旧版本,所以只把
#     代码和 drop-in 退回去、**不重启**,写坏 SHA 标记,退出码 1(timer 那次运行显示 failed)。
#
# 自举(第一次合并带本功能的提交时)要注意:timer 跑的是 VM 工作区里的这个脚本,
# 合并的那一轮还是**旧脚本**在跑——它不查 CI、不同步 drop-in;下一轮 HEAD 已是最新,
# 直接"up to date"退出。所以 drop-in 不会自动生效,直到 main 上再来一个新提交。
# 为此:
#   * HEAD 已是最新时也会比对仓库里的 drop-in 和已安装的(只读,不需要 sudo),
#     有差异就打一条 WARNING(内容不变时不重复打);
#   * `bash tools/cloud-autodeploy.sh --resync-dropins`:不动代码,只把当前 HEAD 的 drop-in
#     同步进去;有变化就 daemon-reload + restart + 等 /ready,不就绪就恢复旧 drop-in 并重启。
#     见 docs/RUNBOOK_OPS.md §7。
#
# Runs as the deploy user (git uses ~/.ssh/config github-lionpick alias +
# read-only deploy key). Needs passwordless sudo for `systemctl restart
# lionpick`, `systemctl daemon-reload`, and install/rm under the drop-in dir
# (see docs/RUNBOOK_OPS.md §3).
#
# 测试:server/tests/test_cloud_autodeploy.py 用临时 git 仓库 + 假 curl/systemctl
# 跑这个脚本(所有路径和命令都能用环境变量重定向,见下面的配置块)。
set -uo pipefail

# ---- 配置(环境变量可覆盖;测试靠这些把一切重定向到临时目录) ----------------
REPO="${LIONPICK_REPO:-$HOME/AAALion-}"
READY_URL="${AUTODEPLOY_READY_URL:-http://127.0.0.1:8000/ready}"
STATE_DIR="${AUTODEPLOY_STATE_DIR:-$HOME}"
BAD_MARKER="$STATE_DIR/.lionpick-autodeploy-bad-sha"
DRIFT_NOTE="$STATE_DIR/.lionpick-autodeploy-drift"
MANAGED_LIST="$STATE_DIR/.lionpick-autodeploy-dropins"
DROPIN_SRC_REL="deploy/systemd/lionpick.service.d"
DROPIN_DIR="${AUTODEPLOY_DROPIN_DIR:-/etc/systemd/system/lionpick.service.d}"
# -n:sudo 需要密码时立刻失败,而不是在没有终端的 timer 里等输入。
SUDO="${AUTODEPLOY_SUDO-sudo -n}"
PYTHON="${AUTODEPLOY_PYTHON:-python3}"
REQUIRE_CI="${AUTODEPLOY_REQUIRE_CI:-1}"
GITHUB_API="${AUTODEPLOY_GITHUB_API:-https://api.github.com}"
GITHUB_REPO="${AUTODEPLOY_GITHUB_REPO:-YushengLiSam/AAALion}"
CI_API_TIMEOUT="${AUTODEPLOY_CI_API_TIMEOUT:-10}"
# 必须与 .github/workflows/ci.yml 的 job 名一致(测试里有一致性检查)。
REQUIRED_CHECKS="${AUTODEPLOY_REQUIRED_CHECKS:-pytest (py3.10),pytest (py3.11),retrieval eval gate}"
# Ready-check: warmup loads BOTH cross-encoder rerankers + CLIP + embeddings;
# under CPU contention this can take ~60 s (measured; restart → /ready 200 is
# 21–40 s on Milvus), so allow a generous 150 s (75 × 2 s). A too-tight window
# FALSE-rolls-back a good-but-slow-warming deploy and marks it known-bad.
READY_POLLS="${AUTODEPLOY_READY_POLLS:-75}"
READY_INTERVAL="${AUTODEPLOY_READY_INTERVAL:-2}"
PROTECTED_DROPINS="jwt.conf"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# ---- CI 门禁 -------------------------------------------------------------------
# 输出一行:"<success|pending|failure|unreachable> <说明>"。
ci_status() {
  local sha="$1" body code
  body="$(mktemp)"
  code="$(curl -sS -o "$body" -w '%{http_code}' --max-time "$CI_API_TIMEOUT" \
            -H 'Accept: application/vnd.github+json' \
            -H 'X-GitHub-Api-Version: 2022-11-28' \
            "$GITHUB_API/repos/$GITHUB_REPO/commits/$sha/check-runs?per_page=100" 2>/dev/null)" || code="000"
  if [ "$code" != "200" ]; then
    rm -f "$body"
    echo "unreachable http=$code"
    return 0
  fi
  "$PYTHON" - "$body" "$REQUIRED_CHECKS" <<'PY'
import json, sys
path, required = sys.argv[1], [s.strip() for s in sys.argv[2].split(",") if s.strip()]
try:
    with open(path, encoding="utf-8") as fh:
        runs = json.load(fh).get("check_runs") or []
except Exception as exc:  # 响应不是预期的 JSON → 当作查不到
    print(f"unreachable bad-json: {type(exc).__name__}")
    sys.exit(0)
# 默认 filter=latest 已经是每个名字最新的一次;保险起见同名取 id 最大的。
latest = {}
for r in runs:
    name = r.get("name")
    if name in required and (name not in latest or (r.get("id") or 0) > (latest[name].get("id") or 0)):
        latest[name] = r
FAIL = {"failure", "timed_out", "action_required", "startup_failure", "neutral"}
pending, failed = [], []
for name in required:
    r = latest.get(name)
    if r is None:
        pending.append(f"{name}=missing")
    elif r.get("status") != "completed":
        pending.append(f"{name}={r.get('status')}")
    elif r.get("conclusion") == "success":
        continue
    elif r.get("conclusion") in FAIL:
        failed.append(f"{name}={r.get('conclusion')}")
    else:  # cancelled / skipped / stale:可能被重跑,先当 pending
        pending.append(f"{name}={r.get('conclusion')}")
if failed:
    print("failure " + " ".join(failed))
elif pending:
    print("pending " + " ".join(pending))
else:
    print("success " + " ".join(f"{n}=success" for n in required))
PY
  rm -f "$body"
}

# ---- drop-in 同步 --------------------------------------------------------------
_valid_dropin_name() {
  case "$1" in
    *[!A-Za-z0-9._-]*|"") return 1 ;;
  esac
  case "$1" in *.conf) ;; *) return 1 ;; esac
  for p in $PROTECTED_DROPINS; do [ "$1" = "$p" ] && return 1; done
  return 0
}

# 仓库工作区里(= 当前 HEAD 树)受管的 drop-in 文件名,每行一个。
repo_dropins() {
  local d="$REPO/$DROPIN_SRC_REL" f name
  [ -d "$d" ] || return 0
  for f in "$d"/*.conf; do
    if [ -L "$f" ]; then
      log "WARNING: refusing symlinked drop-in '$(basename "$f")'" >&2
      continue
    fi
    [ -f "$f" ] || continue
    name="$(basename "$f")"
    if _valid_dropin_name "$name"; then echo "$name"; else log "WARNING: refusing to manage drop-in '$name'" >&2; fi
  done
}

managed_dropins() {
  [ -f "$MANAGED_LIST" ] || return 0
  local name
  while IFS= read -r name; do
    _valid_dropin_name "$name" && echo "$name"
  done < "$MANAGED_LIST"
}

# 部署前拍快照:受管 + 仓库里的每个文件名 → 有就复制,没有就记 absent。
SNAP_DIR=""
snapshot_dropins() {
  SNAP_DIR="$(mktemp -d)"
  local name
  { repo_dropins; managed_dropins; } | sort -u > "$SNAP_DIR/.names"
  : > "$SNAP_DIR/.absent"
  while IFS= read -r name; do
    if [ -f "$DROPIN_DIR/$name" ]; then cp -p "$DROPIN_DIR/$name" "$SNAP_DIR/$name"
    else echo "$name" >> "$SNAP_DIR/.absent"; fi
  done < "$SNAP_DIR/.names"
  if [ -f "$MANAGED_LIST" ]; then cp -p "$MANAGED_LIST" "$SNAP_DIR/.managed"; fi
}

# 把当前工作区的 drop-in 同步到 DROPIN_DIR。改了东西就 daemon-reload。
# 返回非 0 表示安装失败(调用方按部署失败处理)。
sync_dropins() {
  local changed=0 name src new_list
  new_list="$(mktemp)"
  repo_dropins | sort -u > "$new_list"
  # 不用 install -D(BSD install 没有 -D;测试在 macOS 上跑),目录单独建。
  if [ -s "$new_list" ] && [ ! -d "$DROPIN_DIR" ]; then
    $SUDO mkdir -p "$DROPIN_DIR" || { log "cannot create $DROPIN_DIR"; rm -f "$new_list"; return 1; }
  fi
  while IFS= read -r name; do
    src="$REPO/$DROPIN_SRC_REL/$name"
    if ! cmp -s "$src" "$DROPIN_DIR/$name" 2>/dev/null; then
      $SUDO install -m 644 "$src" "$DROPIN_DIR/$name" || { log "drop-in install failed: $name"; rm -f "$new_list"; return 1; }
      log "drop-in installed: $name"
      changed=1
    fi
  done < "$new_list"
  # 仓库里删掉的、且曾由本脚本安装的 → 删除;其它(jwt.conf 等)不碰。
  while IFS= read -r name; do
    if ! grep -qxF "$name" "$new_list" && [ -f "$DROPIN_DIR/$name" ]; then
      $SUDO rm -f "$DROPIN_DIR/$name" || { log "drop-in remove failed: $name"; rm -f "$new_list"; return 1; }
      log "drop-in removed (no longer in repo): $name"
      changed=1
    fi
  done < <(managed_dropins)
  cp "$new_list" "$MANAGED_LIST"
  rm -f "$new_list"
  if [ "$changed" = "1" ]; then
    $SUDO systemctl daemon-reload || { log "daemon-reload failed"; return 1; }
    log "systemd daemon-reload (drop-ins changed)"
  fi
  return 0
}

# 按快照恢复 drop-in(回滚用)。
restore_dropins() {
  [ -n "$SNAP_DIR" ] && [ -d "$SNAP_DIR" ] || return 0
  local name changed=0
  while IFS= read -r name; do
    if [ -f "$SNAP_DIR/$name" ]; then
      if ! cmp -s "$SNAP_DIR/$name" "$DROPIN_DIR/$name" 2>/dev/null; then
        $SUDO install -m 644 "$SNAP_DIR/$name" "$DROPIN_DIR/$name" && changed=1
      fi
    elif grep -qxF "$name" "$SNAP_DIR/.absent" && [ -f "$DROPIN_DIR/$name" ]; then
      $SUDO rm -f "$DROPIN_DIR/$name" && changed=1
    fi
  done < "$SNAP_DIR/.names"
  if [ -f "$SNAP_DIR/.managed" ]; then cp -p "$SNAP_DIR/.managed" "$MANAGED_LIST"; else rm -f "$MANAGED_LIST"; fi
  if [ "$changed" = "1" ]; then
    $SUDO systemctl daemon-reload
    log "drop-ins restored from pre-deploy snapshot; daemon-reload"
  fi
}

# 只读比对:输出与仓库不一致的受管 drop-in(每行 "<name> <changed|missing|stale>")。
dropin_drift() {
  local name
  while IFS= read -r name; do
    if [ ! -f "$DROPIN_DIR/$name" ]; then echo "$name missing"
    elif ! cmp -s "$REPO/$DROPIN_SRC_REL/$name" "$DROPIN_DIR/$name" 2>/dev/null; then echo "$name changed"; fi
  done < <(repo_dropins 2>/dev/null | sort -u)
  while IFS= read -r name; do
    if [ -f "$DROPIN_DIR/$name" ] && [ ! -f "$REPO/$DROPIN_SRC_REL/$name" ]; then echo "$name stale"; fi
  done < <(managed_dropins)
}

# HEAD 已是最新时调用:有漂移就提示一次(同样的漂移不重复刷日志)。
warn_if_drift() {
  local drift
  drift="$(dropin_drift | tr '\n' ' ')"
  if [ -z "$drift" ]; then
    rm -f "$DRIFT_NOTE"
    return 0
  fi
  if [ ! -f "$DRIFT_NOTE" ] || [ "$(cat "$DRIFT_NOTE")" != "$drift" ]; then
    echo "$drift" > "$DRIFT_NOTE"
    log "WARNING: installed drop-ins differ from the repo ($drift); run: bash tools/cloud-autodeploy.sh --resync-dropins (docs/RUNBOOK_OPS.md §7)"
  fi
}

# 手工模式:代码不动,只把当前 HEAD 的 drop-in 同步进去并重启验证。
resync_dropins_main() {
  cd "$REPO" 2>/dev/null || { log "repo not found: $REPO"; exit 1; }
  if [ -z "$(dropin_drift)" ]; then
    log "drop-ins already match the repo at $(git rev-parse --short HEAD); nothing to do"
    rm -f "$DRIFT_NOTE"
    return 0
  fi
  log "resyncing drop-ins from $(git rev-parse --short HEAD): $(dropin_drift | tr '\n' ' ')"
  snapshot_dropins
  if ! sync_dropins; then
    log "drop-in sync failed; restoring previous drop-ins (no restart)"
    restore_dropins
    rm -rf "$SNAP_DIR"
    exit 1
  fi
  $SUDO systemctl restart lionpick
  if wait_ready; then
    log "resync OK — drop-ins match the repo, /ready 2xx"
    rm -f "$DRIFT_NOTE"
    rm -rf "$SNAP_DIR"
    return 0
  fi
  log "resync FAILED ready-check — restoring previous drop-ins and restarting"
  restore_dropins
  $SUDO systemctl restart lionpick
  rm -rf "$SNAP_DIR"
  exit 1
}

wait_ready() {
  local _
  for _ in $(seq 1 "$READY_POLLS"); do
    sleep "$READY_INTERVAL"
    # -f:非 2xx(包括门控 enforce 的 503)不输出 → grep 失败 → 继续等。
    if curl -sf --max-time 15 "$READY_URL" 2>/dev/null | grep -q 'ready'; then return 0; fi
  done
  return 1
}

main() {
  cd "$REPO" 2>/dev/null || { log "repo not found: $REPO"; exit 1; }

  # 1) Fetch. Network blip → skip this round quietly (timer retries soon).
  git fetch origin -q 2>/dev/null || { log "fetch failed (network?), skipping"; exit 0; }

  local local_sha remote_sha
  local_sha="$(git rev-parse HEAD)"
  remote_sha="$(git rev-parse origin/main)"

  # Up to date(顺便只读检查一下 drop-in 有没有和仓库漂移,见文件头"自举")。
  if [ "$local_sha" = "$remote_sha" ]; then
    warn_if_drift
    exit 0
  fi

  # 2) Known-bad guard.
  if [ -f "$BAD_MARKER" ] && [ "$(cat "$BAD_MARKER")" = "$remote_sha" ]; then
    log "origin/main $remote_sha is known-bad (CI failed or a prior deploy failed ready-check); skipping until it advances"
    exit 0
  fi

  # 3) CI gate.
  if [ "$REQUIRE_CI" = "0" ]; then
    log "WARNING: AUTODEPLOY_REQUIRE_CI=0 — deploying $remote_sha WITHOUT checking CI"
  else
    local status verdict
    status="$(ci_status "$remote_sha")"
    verdict="${status%% *}"
    case "$verdict" in
      success) log "CI green for $remote_sha: ${status#* }" ;;
      pending) log "CI pending for $remote_sha (${status#* }); skipping this tick"; exit 0 ;;
      failure)
        log "CI FAILED for $remote_sha (${status#* }); marking known-bad, not deploying"
        echo "$remote_sha" > "$BAD_MARKER"
        exit 0 ;;
      *) log "CI status unavailable for $remote_sha (${status#* }); NOT deploying blind, will retry"; exit 0 ;;
    esac
  fi

  # 4) Deploy: code + drop-ins + restart + ready-check.
  log "deploying $local_sha -> $remote_sha"
  if ! git reset --hard "$remote_sha" -q; then
    log "git reset --hard $remote_sha failed; nothing restarted"
    git reset --hard "$local_sha" -q
    exit 1
  fi
  # 快照放在 reset 之后:文件名集合 = 新树里的 drop-in ∪ 已受管清单,这样新版本
  # "新增"的文件在快照里记为 absent,回滚时会被删掉。
  snapshot_dropins
  if ! sync_dropins; then
    # 还没 restart:线上进程仍是旧代码 + 旧配置。退回代码和 drop-in 即可,不重启。
    log "drop-in sync FAILED (sudo rights? see docs/RUNBOOK_OPS.md §3) — reverting to $local_sha WITHOUT restart; marking $remote_sha known-bad"
    echo "$remote_sha" > "$BAD_MARKER"
    git reset --hard "$local_sha" -q
    restore_dropins
    rm -rf "$SNAP_DIR"
    exit 1
  fi
  local ok=0
  $SUDO systemctl restart lionpick
  wait_ready && ok=1

  if [ "$ok" = "1" ]; then
    log "deploy OK — now at $remote_sha"
    rm -f "$BAD_MARKER"
  else
    log "deploy FAILED ready-check — rolling back $remote_sha -> $local_sha"
    echo "$remote_sha" > "$BAD_MARKER"
    git reset --hard "$local_sha" -q
    restore_dropins
    $SUDO systemctl restart lionpick
    log "rolled back to $local_sha"
  fi
  rm -rf "$SNAP_DIR"
}

# 被 source 时(测试)只定义函数,不执行。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    --resync-dropins) resync_dropins_main ;;
    "") main ;;
    *) echo "usage: $0 [--resync-dropins]" >&2; exit 2 ;;
  esac
fi
