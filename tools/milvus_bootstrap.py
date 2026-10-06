#!/usr/bin/env python3
"""Milvus Standalone 首次初始化:轮换 root 密码 + 建最小权限的应用账号与运维账号(P1)。

    # 在 VM 上,deploy/milvus/.env 已填好三个密码(权限 600,不进 git)
    python tools/milvus_bootstrap.py --env-file deploy/milvus/.env
    python tools/milvus_bootstrap.py --env-file deploy/milvus/.env --check   # 只核对,不改

读取的变量(环境变量优先,其次 --env-file):

* ``MILVUS_ROOT_PASSWORD`` —— root 的新密码;
* ``MILVUS_OLD_ROOT_PASSWORD`` —— 旧密码,默认 Milvus 出厂值 ``Milvus``;
* ``LIONPICK_MILVUS_APP_PASSWORD`` —— ``lionpick_app``(API 进程用,只读);
* ``LIONPICK_MILVUS_ADMIN_PASSWORD`` —— ``lionpick_admin``(入库 / 迁移 / 切别名用)。

权限设计(名字按 Milvus 2.5+/3.0 文档的内置权限组核对过,2026-10-06,
https://milvus.io/docs/privilege_group.md 与 https://milvus.io/docs/grant_privileges.md):

* ``lionpick_app_role``:``CollectionReadOnly``(Query / Search / DescribeCollection /
  DescribeAlias / ListAliases / GetLoadState / GetStatistics …)+ ``Load``——
  ``MilvusStore._ensure_loaded`` 在每个进程第一次查询时会调 load_collection,
  这个权限不在只读组里;
* ``lionpick_admin_role``:``CollectionAdmin``(读写 + CreateAlias / DropAlias)+
  ``DatabaseAdmin``(CreateCollection / DropCollection / ShowCollections)+
  ``RenameCollection``(首次启用别名时把老集合改名为 legacy)。

范围都是 ``default`` 库(或 ``--db``)下的全部集合(``*``)。权限组是 Milvus 服务端的
概念,本脚本**没有在真实 Standalone 上跑过**(本地只有 milvus-lite,不做鉴权);
首次在 VM 上运行后请按 docs/RUNBOOK_MILVUS.md 的"权限自检"一节验证:
用 lionpick_app 写入应被拒绝,用 lionpick_admin 跑一次入库应成功。

脚本可重复执行:用户 / 角色已存在就跳过,授权重复授予是幂等的。从不打印密码。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

APP_USER = "lionpick_app"
ADMIN_USER = "lionpick_admin"
APP_ROLE = "lionpick_app_role"
ADMIN_ROLE = "lionpick_admin_role"
DEFAULT_ROOT_PASSWORD = "Milvus"  # Milvus 出厂值(milvus.yaml common.security.defaultRootPassword)

# (权限或权限组, 集合)。权限组名见模块文档里的链接。
APP_GRANTS: tuple[tuple[str, str], ...] = (
    ("CollectionReadOnly", "*"),
    ("Load", "*"),
)
ADMIN_GRANTS: tuple[tuple[str, str], ...] = (
    ("CollectionAdmin", "*"),
    ("DatabaseAdmin", "*"),
    ("RenameCollection", "*"),
)
MIN_PASSWORD_LEN = 12  # Milvus 本身要求 6–72 字节;这里对自家账号要求更严


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _setting(name: str, file_env: dict[str, str], default: str | None = None) -> str | None:
    return os.environ.get(name) or file_env.get(name) or default


def _users(client) -> set[str]:
    res = client.list_users()
    return set(res or [])


def _roles(client) -> set[str]:
    return set(client.list_roles() or [])


def connect_root(factory: Callable[..., object], uri: str, new_pw: str, old_pw: str, log=print):
    """先用新密码连;连不上再用旧密码连,并把 root 密码改成新的。返回已认证的客户端。"""
    try:
        client = factory(uri=uri, token=f"root:{new_pw}")
        client.list_users()  # 真正发一次需要鉴权的请求
        log("root: already using the configured password")
        return client
    except Exception:
        pass
    client = factory(uri=uri, token=f"root:{old_pw}")
    client.list_users()
    client.update_password("root", old_pw, new_pw, reset_connection=True)
    log("root: password rotated")
    return factory(uri=uri, token=f"root:{new_pw}")


def ensure_account(client, user: str, password: str, role: str, grants, db: str, *, recreate: bool, log=print) -> None:
    users = _users(client)
    if user in users and recreate:
        client.drop_user(user)
        users.discard(user)
        log(f"{user}: dropped (recreate)")
    if user not in users:
        client.create_user(user, password)
        log(f"{user}: created")
    else:
        log(f"{user}: exists (password unchanged; use --recreate-users to reset it)")
    if role not in _roles(client):
        client.create_role(role)
        log(f"{role}: created")
    for privilege, collection in grants:
        client.grant_privilege_v2(role, privilege, collection, db_name=db)
        log(f"{role}: granted {privilege} on {db}.{collection}")
    client.grant_role(user, role)
    log(f"{user}: bound to {role}")


def check(client, db: str, log=print) -> int:
    """只读核对:用户、角色绑定、授权清单。返回问题条数。"""
    problems = 0
    users = _users(client)
    for user, role, grants in ((APP_USER, APP_ROLE, APP_GRANTS), (ADMIN_USER, ADMIN_ROLE, ADMIN_GRANTS)):
        if user not in users:
            log(f"MISSING user {user}")
            problems += 1
            continue
        roles = set(client.describe_user(user).get("roles") or [])
        if role not in roles:
            log(f"MISSING role binding {user} -> {role} (has {sorted(roles)})")
            problems += 1
        desc = client.describe_role(role, db_name=db) if role in _roles(client) else {"privileges": []}
        granted = {str(p.get("privilege")) for p in desc.get("privileges", [])}
        for privilege, _ in grants:
            if privilege not in granted:
                log(f"MISSING grant {role}: {privilege} (granted: {sorted(granted)})")
                problems += 1
        log(f"{user}: roles={sorted(roles)} grants={sorted(granted)}")
    return problems


def main(argv: list[str] | None = None, factory: Callable[..., object] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default=os.environ.get("RAG_MILVUS_URI", "http://127.0.0.1:19530"))
    ap.add_argument("--env-file", type=Path, default=None, help="e.g. deploy/milvus/.env (mode 600, not in git)")
    ap.add_argument("--db", default="default")
    ap.add_argument("--check", action="store_true", help="verify users / roles / grants only, change nothing")
    ap.add_argument("--recreate-users", action="store_true", help="drop and recreate the two app users (resets passwords)")
    args = ap.parse_args(argv)

    if "://" not in args.uri:
        print("this tool is for a Milvus server (http://host:19530); Milvus Lite has no authentication", file=sys.stderr)
        return 2
    file_env: dict[str, str] = {}
    if args.env_file is not None:
        if not args.env_file.exists():
            print(f"env file not found: {args.env_file}", file=sys.stderr)
            return 2
        mode = args.env_file.stat().st_mode & 0o077
        if mode:
            print(f"warning: {args.env_file} is readable by group/others; chmod 600 it", file=sys.stderr)
        file_env = read_env_file(args.env_file)

    root_pw = _setting("MILVUS_ROOT_PASSWORD", file_env)
    old_pw = _setting("MILVUS_OLD_ROOT_PASSWORD", file_env, DEFAULT_ROOT_PASSWORD)
    app_pw = _setting("LIONPICK_MILVUS_APP_PASSWORD", file_env)
    admin_pw = _setting("LIONPICK_MILVUS_ADMIN_PASSWORD", file_env)
    missing = [n for n, v in (("MILVUS_ROOT_PASSWORD", root_pw), ("LIONPICK_MILVUS_APP_PASSWORD", app_pw),
                              ("LIONPICK_MILVUS_ADMIN_PASSWORD", admin_pw)) if not v]
    if missing:
        print(f"missing settings: {missing}", file=sys.stderr)
        return 2
    weak = [n for n, v in (("MILVUS_ROOT_PASSWORD", root_pw), ("LIONPICK_MILVUS_APP_PASSWORD", app_pw),
                           ("LIONPICK_MILVUS_ADMIN_PASSWORD", admin_pw))
            if len(v) < MIN_PASSWORD_LEN or len(v.encode()) > 72 or v == DEFAULT_ROOT_PASSWORD]
    if weak:
        print(f"passwords must be {MIN_PASSWORD_LEN}–72 bytes and not the factory default: {weak}", file=sys.stderr)
        return 2
    if len({root_pw, app_pw, admin_pw}) < 3:
        print("use three different passwords", file=sys.stderr)
        return 2

    if factory is None:
        from pymilvus import MilvusClient

        factory = MilvusClient

    try:
        client = connect_root(factory, args.uri, root_pw, old_pw)
    except Exception as exc:
        print(f"cannot authenticate as root with the new or the old password: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    if args.check:
        problems = check(client, args.db)
        print("OK" if not problems else f"{problems} problem(s)")
        return 0 if not problems else 1

    ensure_account(client, APP_USER, app_pw, APP_ROLE, APP_GRANTS, args.db, recreate=args.recreate_users)
    ensure_account(client, ADMIN_USER, admin_pw, ADMIN_ROLE, ADMIN_GRANTS, args.db, recreate=args.recreate_users)
    problems = check(client, args.db)
    print()
    print("Next: put the app credential in /etc/lionpick/milvus.secret.env (mode 600, owner root):")
    print(f"    RAG_MILVUS_TOKEN={APP_USER}:<LIONPICK_MILVUS_APP_PASSWORD>")
    print(f"and use {ADMIN_USER} only for ingest / migrate / alias switching (export RAG_MILVUS_TOKEN in that shell).")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
