#!/usr/bin/env python3
"""herdr-thread: Delta-like thread/worktree/review orchestration on top of herdr."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = Path(os.environ.get("HERDR_THREAD_CONFIG",
                                  str(HOME / ".config" / "herdr-thread" / "config.toml")))
DATA_DIR = Path(os.environ.get("HERDR_THREAD_DATA",
                               str(HOME / ".local" / "share" / "herdr-thread")))
DB_PATH = DATA_DIR / "threads.db"

DEFAULT_CONFIG = {
    "worktree_root": str(HOME / ".herdr-worktrees"),
    "default_agent": "codex",
    "review_plugin": "persiyanov.reviewr",
    "review_entrypoint": "pane",
    "land_strategy": "push",
    "notify": True,
    "require_approval": False,
    "pre_land_commands": [],
    "pre_land_script": "",
    "fallback": [],
    "roles": {},
    "profiles": {
        "worker": {
            "kind": "codex",
            "args": [],
            "prompt": "你是实现子 Agent。只做被指派的子任务，完成后总结改动文件和验证方式。",
        },
        "scout": {
            "kind": "codex",
            "args": [],
            "prompt": "你是调研子 Agent。只读代码并给出结论，不修改任何文件。",
        },
        "reviewer": {
            "kind": "codex",
            "args": [],
            "prompt": "你是审查子 Agent。只读 diff，按文件列出风险、遗漏测试和回退建议，不改文件。",
        },
    },
}


# ---------- infra ----------

def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def die(msg: str, code: int = 1):
    print(f"herdr-thread: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            user = tomllib.load(f)
        for k, v in user.items():
            if k == "profiles" and isinstance(v, dict):
                cfg["profiles"].update(v)
            else:
                cfg[k] = v
    cfg["worktree_root"] = str(Path(os.path.expanduser(cfg["worktree_root"])))
    return cfg


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            label TEXT NOT NULL,
            repo TEXT NOT NULL,
            worktree TEXT NOT NULL,
            branch TEXT NOT NULL,
            base_ref TEXT,
            base_sha TEXT,
            workspace_id TEXT,
            pane_id TEXT,
            review_pane_id TEXT,
            reviewer_pane_id TEXT,
            reviewer_agent TEXT,
            agent_name TEXT,
            agent_kind TEXT,
            status TEXT NOT NULL DEFAULT 'created',
            review_state TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            bookmarked INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            head_sha TEXT,
            landed_at TEXT,
            meta TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
    for col in ("reviewer_pane_id", "reviewer_agent", "pr_url", "pr_state", "ci_state",
                "mergeable", "last_sync_at", "base_sha"):
        if col not in cols:
            conn.execute(f"ALTER TABLE threads ADD COLUMN {col} TEXT")
    if "pr_number" not in cols:
        conn.execute("ALTER TABLE threads ADD COLUMN pr_number INTEGER")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            repo TEXT NOT NULL,
            worktree TEXT NOT NULL,
            branch TEXT NOT NULL,
            base_ref TEXT,
            base_sha TEXT,
            primary_repo INTEGER NOT NULL DEFAULT 0,
            pr_url TEXT,
            pr_number INTEGER,
            pr_state TEXT,
            ci_state TEXT,
            mergeable TEXT,
            last_sync_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            bookmarked INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            meta TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            file TEXT NOT NULL DEFAULT '',
            line INTEGER NOT NULL DEFAULT 0,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            sent_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            source TEXT NOT NULL,
            stored TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subagents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id TEXT NOT NULL,
            child_thread_id TEXT,
            profile TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT '',
            pane_id TEXT,
            agent_name TEXT,
            status TEXT NOT NULL DEFAULT 'working',
            created_at TEXT NOT NULL,
            result TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            host TEXT NOT NULL,
            repo TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'unknown',
            pid INTEGER,
            local_port INTEGER,
            remote_port INTEGER,
            created_at TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            sha TEXT NOT NULL,
            short_sha TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            repo TEXT NOT NULL DEFAULT '',
            worktree TEXT NOT NULL DEFAULT '',
            meta TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    ckcols = {row[1] for row in conn.execute("PRAGMA table_info(checkpoints)")}
    for col in ("repo", "worktree"):
        if col not in ckcols:
            conn.execute(f"ALTER TABLE checkpoints ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()
    return conn


def event(conn, tid: str, kind: str, detail: str = ""):
    conn.execute(
        "INSERT INTO events(thread_id, at, kind, detail) VALUES(?,?,?,?)",
        (tid, now(), kind, detail),
    )
    conn.commit()


def run(args: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip()
        raise RuntimeError(f"{' '.join(args)} failed ({p.returncode}): {detail}")
    return p


def herdr(*args: str) -> dict:
    p = subprocess.run(["herdr", *args], text=True, capture_output=True)
    raw = (p.stdout or "").strip()
    if not raw:
        raw = (p.stderr or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"herdr {' '.join(args)}: non-JSON output: {raw[:500]}")
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        raise RuntimeError(f"herdr {' '.join(args)}: {err.get('code')}: {err.get('message')}")
    return data.get("result", data)


def git(repo: str, *args: str, check: bool = True) -> str:
    p = run(["git", "-C", repo, *args], check=check)
    return p.stdout.strip()


def slugify(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "thread")[:limit].strip("-")


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def is_git_repo(path: str) -> bool:
    if not Path(path).exists():
        return False
    return run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"], check=False).returncode == 0


def agent_states() -> dict[str, dict]:
    try:
        data = herdr("agent", "list")
    except Exception:
        return {}
    out = {}
    for a in data.get("agents", []):
        out[a.get("pane_id", "")] = a
    return out


def pane_list(workspace_id: str) -> list[dict]:
    try:
        return herdr("pane", "list", "--workspace", workspace_id).get("panes", [])
    except Exception:
        return []


def find_review_pane(workspace_id: str) -> str | None:
    for p in pane_list(workspace_id):
        if (p.get("label") or "") == "reviewr":
            return p.get("pane_id")
    return None


def git_info(path: str) -> dict:
    info = {"dirty": 0, "head": "", "branch": "", "ahead": "", "behind": "", "diff_stat": ""}
    if not Path(path).exists():
        return info
    try:
        info["head"] = git(path, "rev-parse", "--short", "HEAD", check=False)
        info["branch"] = git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
        status = git(path, "status", "--porcelain", check=False)
        info["dirty"] = len([l for l in status.splitlines() if l.strip()])
        ab = git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD", check=False)
        if ab and "\t" in ab:
            behind, ahead = ab.split("\t")[:2]
            info["ahead"], info["behind"] = ahead, behind
        info["diff_stat"] = git(path, "diff", "--stat", check=False)[-600:]
    except Exception:
        pass
    return info


def thread_row(conn, tid_prefix: str):
    rows = conn.execute(
        "SELECT * FROM threads WHERE id LIKE ? ORDER BY created_at DESC", (tid_prefix + "%",)
    ).fetchall()
    if not rows:
        die(f"thread not found: {tid_prefix}")
    if len(rows) > 1:
        die(f"ambiguous thread id prefix {tid_prefix!r}: {[r['id'] for r in rows]}")
    return rows[0]


def status_line(row, states: dict, info: dict) -> str:
    a = states.get(row["pane_id"] or "", {})
    astate = a.get("agent_status", "-")
    flags = []
    if info["dirty"]:
        flags.append(f"dirty:{info['dirty']}")
    if info["ahead"] and info["ahead"] != "0":
        flags.append(f"ahead:{info['ahead']}")
    if info["behind"] and info["behind"] != "0":
        flags.append(f"behind:{info['behind']}")
    rev = f" review:{row['review_state']}" if row["review_state"] else ""
    if row["pr_url"]:
        flags.append(f"pr:{row['pr_state'] or '?'}/{row['ci_state'] or '-'}")
    flags_s = " ".join(flags)
    return f"{row['id']}  {row['label'][:24]:24}  {astate:8}  {row['status']:8}{rev:14} {flags_s}"


# ---------- hooks ----------

def run_hooks(wt: str, conn, tid: str):
    """Run .agents/prepare then .agents/setup (.delta/setup wins)."""
    hooks = [".agents/prepare", ".agents/setup"]
    if Path(wt, ".delta", "setup").exists():
        hooks[-1] = ".delta/setup"
    for rel in hooks:
        path = Path(wt) / rel
        if not path.exists():
            continue
        if not os.access(path, os.X_OK):
            print(f"  hook      {rel} (not executable, skipped)", file=sys.stderr)
            continue
        print(f"  hook      {rel}")
        r = subprocess.run([str(path)], cwd=wt, text=True, capture_output=True)
        status = "ok" if r.returncode == 0 else f"failed({r.returncode})"
        event(conn, tid, "hook", f"{rel} {status}")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-5:]
            print(f"  hook      {rel} {status}: " + " | ".join(tail), file=sys.stderr)
        else:
            print(f"  hook      {rel} ok")


def cmd_setup(args):
    conn = db()
    r = thread_row(conn, args.id)
    run_hooks(r["worktree"], conn, r["id"])


# ---------- commands ----------

def repo_rows(conn, r):
    rows = conn.execute(
        "SELECT * FROM thread_repos WHERE thread_id=? ORDER BY primary_repo DESC, id", (r["id"],)
    ).fetchall()
    if rows:
        return rows
    conn.execute(
        "INSERT INTO thread_repos(thread_id,repo,worktree,branch,base_ref,base_sha,primary_repo,"
        "pr_url,pr_number,pr_state,ci_state,mergeable,last_sync_at) VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?)",
        (r["id"], r["repo"], r["worktree"], r["branch"], r["base_ref"], r["base_sha"],
         r["pr_url"], r["pr_number"], r["pr_state"], r["ci_state"], r["mergeable"], r["last_sync_at"]),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM thread_repos WHERE thread_id=? ORDER BY primary_repo DESC, id", (r["id"],)
    ).fetchall()


def create_extra_worktree(repo: str, branch: str, base_ref: str, path: str) -> str:
    base_sha = git(repo, "rev-parse", base_ref, check=False)
    if not base_sha:
        raise RuntimeError(f"cannot resolve {base_ref} in {repo}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    p = run(["git", "-C", repo, "worktree", "add", "-b", branch, path, base_sha], check=False)
    if p.returncode != 0:
        raise RuntimeError(f"worktree add failed for {repo}: {p.stderr.strip() or p.stdout.strip()}")
    return base_sha


def cmd_new(args):
    cfg = load_config()
    conn = db()
    raw_repos = args.repo
    if isinstance(raw_repos, (list, tuple)):
        repo_list = list(raw_repos)
    elif raw_repos:
        repo_list = [raw_repos]
    else:
        repo_list = ["."]
    repos = [str(Path(x).resolve()) for x in repo_list]
    repo = repos[0]
    for x in repos:
        if not is_git_repo(x):
            die(f"not a git repository: {x}")
        if run(["git", "-C", x, "rev-parse", "--verify", "HEAD"], check=False).returncode != 0:
            die(f"repository has no commits yet: {x}")

    tid = new_id()
    slug = slugify(args.label)
    base_ref = args.base or "HEAD"
    base_sha = git(repo, "rev-parse", base_ref, check=False)
    branch = args.branch or f"herdr-thread/{slug}-{tid}"

    repo_name = Path(repo).name
    wt = args.worktree or str(Path(cfg["worktree_root"]) / repo_name / f"{slug}-{tid}")
    Path(wt).parent.mkdir(parents=True, exist_ok=True)

    create_args = [
        "worktree", "create",
        "--cwd", repo,
        "--branch", branch,
        "--base", base_ref,
        "--path", wt,
        "--label", args.label,
    ]
    create_args.append("--focus" if args.focus else "--no-focus")
    if args.trust_repository:
        create_args.append("--trust-repository")

    res = herdr(*create_args)
    ws = res.get("workspace", {})
    root = res.get("root_pane", {})
    worktree = res.get("worktree", {})
    workspace_id = ws.get("workspace_id") or root.get("workspace_id")
    pane_id = root.get("pane_id")
    if not workspace_id or not pane_id:
        die(f"unexpected herdr worktree create result: {json.dumps(res)[:500]}")

    review_pane = find_review_pane(workspace_id)
    for _ in range(8):
        if review_pane:
            break
        time.sleep(0.4)
        review_pane = find_review_pane(workspace_id)

    extra_repos = []
    try:
        for xrepo in repos[1:]:
            xname = Path(xrepo).name
            xwt = str(Path(cfg["worktree_root"]) / xname / f"{slug}-{tid}")
            xbase = create_extra_worktree(xrepo, branch, base_ref, xwt)
            extra_repos.append({"repo": xrepo, "worktree": xwt, "branch": branch,
                                "base_ref": base_ref, "base_sha": xbase})
    except Exception as e:
        for x in extra_repos:
            run(["git", "-C", x["repo"], "worktree", "remove", "--force", x["worktree"]], check=False)
        herdr("worktree", "remove", "--workspace", workspace_id, "--force")
        die(str(e))

    profile = cfg["roles"].get(args.role, {}) if getattr(args, "role", None) else {}
    if not profile:
        profile = cfg["profiles"].get(args.profile, {}) if getattr(args, "profile", None) else {}
    agent_name = None
    agent_kind = None
    agent_args: list[str] = []
    if not args.no_agent:
        agent_kind = args.agent or profile.get("kind") or cfg["default_agent"]
        agent_args = list(profile.get("args") or [])
        agent_name = f"ht-{tid}"
        start_res = start_agent(agent_name, agent_kind, pane_id, workspace_id, wt, agent_args,
                                cfg.get("fallback") or [])
        if start_res:
            agent_name = start_res

    conn.execute(
        """INSERT INTO threads
           (id,parent_id,label,repo,worktree,branch,base_ref,base_sha,workspace_id,pane_id,review_pane_id,
            agent_name,agent_kind,status,review_state,tags,bookmarked,archived,created_at,head_sha,meta)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            tid, args.parent, args.label, repo, wt, worktree.get("branch", branch), base_ref, base_sha,
            workspace_id, pane_id, review_pane, agent_name, agent_kind,
            "working" if agent_name else "created", "", args.tag or "", 0, 0,
            now(), git(wt, "rev-parse", "--short", "HEAD", check=False), "{}",
        ),
    )
    conn.execute(
        "INSERT INTO thread_repos(thread_id,repo,worktree,branch,base_ref,base_sha,primary_repo) "
        "VALUES(?,?,?,?,?,?,1)",
        (tid, repo, wt, worktree.get("branch", branch), base_ref, base_sha),
    )
    for x in extra_repos:
        conn.execute(
            "INSERT INTO thread_repos(thread_id,repo,worktree,branch,base_ref,base_sha,primary_repo) "
            "VALUES(?,?,?,?,?,?,0)",
            (tid, x["repo"], x["worktree"], x["branch"], x["base_ref"], x["base_sha"]),
        )
    conn.commit()
    event(conn, tid, "created", f"workspace={workspace_id} branch={branch}")

    print(f"thread {tid}: {args.label}")
    print(f"  repo      {repo}")
    print(f"  worktree  {wt}")
    print(f"  branch    {worktree.get('branch', branch)}")
    for x in extra_repos:
        print(f"  + repo    {x['repo']}")
        print(f"    worktree {x['worktree']}")
    print(f"  workspace {workspace_id}  pane {pane_id}")
    if review_pane:
        print(f"  reviewr   {review_pane}")
    if agent_name:
        print(f"  agent     {agent_name} ({agent_kind})")
    initial_prompt = args.prompt
    if initial_prompt is None and args.profile and profile.get("prompt"):
        initial_prompt = profile["prompt"]
    if initial_prompt and agent_name:
        herdr("agent", "prompt", agent_name, initial_prompt)
        record_turn(conn, tid, "user", initial_prompt)
        print("  prompt    sent")
    if getattr(args, "run_hooks", False):
        run_hooks(wt, conn, tid)
    if args.focus:
        herdr("workspace", "focus", workspace_id)
    return tid


def start_agent(name: str, kind: str, pane_id: str, workspace_id: str, cwd: str,
                agent_args: list[str] | None = None, fallbacks: list[str] | None = None) -> str | None:
    last = ""
    kinds = [kind] + [x for x in (fallbacks or []) if x and x != kind]
    for k in kinds:
        extra = list(agent_args or []) if k == kind else []
        for attempt in range(3):
            try:
                cmd = ["herdr", "agent", "start", name, "--kind", k, "--pane", pane_id, "--timeout", "60000"]
                if extra:
                    cmd += ["--", *extra]
                p = subprocess.run(cmd, text=True, capture_output=True)
                if p.returncode != 0:
                    raw = (p.stderr or p.stdout or "").strip()
                    raise RuntimeError(raw[:500])
                return name
            except Exception as e:
                last = str(e)
                time.sleep(1.5)
        print(f"herdr-thread: agent kind {k} failed: {last}", file=sys.stderr)
    print(f"herdr-thread: warning: agent start failed: {last}", file=sys.stderr)
    return None


def cmd_list(args):
    cfg = load_config()
    conn = db()
    states = agent_states()
    conds, params = [], []
    if not args.all:
        conds.append("archived = 0")
    if args.tag:
        conds.append("tags LIKE ?")
        params.append(f"%{args.tag}%")
    if args.bookmarked:
        conds.append("bookmarked = 1")
    if args.search:
        conds.append("(label LIKE ? OR branch LIKE ? OR repo LIKE ?)")
        params += [f"%{args.search}%"] * 3
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(f"SELECT * FROM threads {where} ORDER BY created_at DESC", params).fetchall()
    out = []
    for r in rows:
        info = git_info(r["worktree"])
        out.append((r, info))
    if args.json:
        print(json.dumps([{**dict(r), **info} for r, info in out], ensure_ascii=False, indent=2))
        return
    if not out:
        print("no threads")
        return
    print(f"{'ID':8}  {'LABEL':24}  {'AGENT':8}  {'STATUS':8} {'REVIEW':14} FLAGS")
    for r, info in out:
        print(status_line(r, states, info))


def cmd_show(args):
    conn = db()
    r = thread_row(conn, args.id)
    info = git_info(r["worktree"])
    st = agent_states().get(r["pane_id"] or "", {})
    if args.json:
        print(json.dumps({**dict(r), "git": info, "agent": st,
                          "repos": [dict(x) for x in repo_rows(conn, r)]},
                         ensure_ascii=False, indent=2))
        return
    print(f"thread {r['id']}  {r['label']}")
    print(f"  status      {r['status']}  review={r['review_state'] or '-'}  agent={st.get('agent_status','-')}")
    repos = repo_rows(conn, r)
    print(f"  repo        {r['repo']}" + (f"  (+{len(repos)-1} more)" if len(repos) > 1 else ""))
    for rr in repos:
        pr = f"  pr={rr['pr_state'] or '-'}" if rr["pr_url"] else ""
        print(f"    - {Path(rr['repo']).name:18} {rr['branch']}  {rr['worktree']}{pr}")
    print(f"  base        {r['base_sha'] or r['base_ref'] or '-'}")
    print(f"  workspace   {r['workspace_id']}  agent_pane={r['pane_id']}  reviewr={r['review_pane_id'] or '-'}")
    if r["reviewer_agent"]:
        print(f"  reviewer    {r['reviewer_agent']} ({r['reviewer_pane_id'] or '-'})")
    if r["pr_url"]:
        print(f"  pr          {r['pr_url']}  state={r['pr_state'] or '-'} ci={r['ci_state'] or '-'} mergeable={r['mergeable'] or '-'}")
    print(f"  git         head={info['head']} dirty={info['dirty']} ahead={info['ahead'] or '-'} behind={info['behind'] or '-'}")
    if r["tags"]:
        print(f"  tags        {r['tags']}")
    if info["diff_stat"]:
        print("  uncommitted:")
        for line in info["diff_stat"].splitlines():
            print("   ", line)
    log = git(r["worktree"], "log", "--oneline", "-5", check=False)
    if log:
        print("  recent commits:")
        for line in log.splitlines():
            print("   ", line)
    cks = conn.execute(
        "SELECT short_sha, message FROM checkpoints WHERE thread_id=? ORDER BY id DESC LIMIT 3",
        (r["id"],),
    ).fetchall()
    if cks:
        print("  checkpoints:")
        for x in cks:
            print(f"    {x['short_sha']}  {x['message']}")


PR_FIELDS = "state,isDraft,mergeable,reviewDecision,statusCheckRollup,url,number,title,headRefName,baseRefName"


def gh_pr_view(pr_url: str, cwd: str) -> dict:
    p = run(["gh", "pr", "view", pr_url, "--json", PR_FIELDS], cwd=cwd, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {p.stderr.strip() or p.stdout.strip()}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"gh pr view returned non-JSON: {p.stdout[:200]}")


def ci_state(rollup) -> str:
    if not rollup:
        return "none"
    pending_states = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}
    fail_states = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
    failed = pending = False
    for c in rollup:
        if c.get("__typename") == "CheckRun" or "conclusion" in c:
            status = (c.get("status") or "").upper()
            concl = (c.get("conclusion") or "").upper()
            if concl in fail_states:
                failed = True
            elif status in pending_states or concl in pending_states or not concl:
                pending = True
        else:
            state = (c.get("state") or "").upper()
            if state in fail_states:
                failed = True
            elif state in pending_states:
                pending = True
    if failed:
        return "failure"
    if pending:
        return "pending"
    return "success"


def refresh_thread_pr(conn, r):
    rows = repo_rows(conn, r)
    primary = rows[0]
    status = r["status"]
    landed_at = r["landed_at"]
    if rows and all(x["pr_url"] and x["pr_state"] == "MERGED" for x in rows):
        status = "landed"
        landed_at = landed_at or now()
    elif status != "landed":
        open_prs = [x for x in rows if x["pr_url"]]
        if open_prs and all(x["pr_state"] == "CLOSED" for x in open_prs):
            status = "closed"
        elif open_prs:
            status = "pr_open"
    conn.execute(
        "UPDATE threads SET status=?, landed_at=?, pr_url=?, pr_number=?, pr_state=?, ci_state=?, mergeable=? WHERE id=?",
        (status, landed_at, primary["pr_url"], primary["pr_number"], primary["pr_state"],
         primary["ci_state"], primary["mergeable"], r["id"]),
    )
    conn.commit()


def update_pr_repo(conn, cfg, r, rr, data: dict) -> bool:
    old_state, old_ci = rr["pr_state"], rr["ci_state"]
    state = data.get("state") or ""
    ci = ci_state(data.get("statusCheckRollup"))
    mergeable = data.get("mergeable") or ""
    conn.execute(
        "UPDATE thread_repos SET pr_state=?, ci_state=?, mergeable=?, last_sync_at=? WHERE id=?",
        (state, ci, mergeable, now(), rr["id"]),
    )
    conn.commit()
    refresh_thread_pr(conn, r)
    changed = (old_state != state) or (old_ci != ci)
    if changed:
        repo = Path(rr["repo"]).name
        event(conn, r["id"], "pr", f"{repo}: {old_state or '-'}->{state or '-'} ci {old_ci or '-'}->{ci}")
        if cfg.get("notify", True):
            try:
                sound = "done" if state == "MERGED" or ci == "success" else "request"
                herdr("notification", "show", f"PR {state or '?'}: {r['label']}",
                      "--body", f"{repo} · CI {ci} · {rr['pr_url']}", "--sound", sound)
            except Exception:
                pass
    return changed


def update_pr(conn, cfg, r, data: dict) -> bool:
    return update_pr_repo(conn, cfg, r, repo_rows(conn, r)[0], data)


def cmd_pr(args):
    cfg = load_config()
    conn = db()
    r = thread_row(conn, args.id)
    rows = [x for x in repo_rows(conn, r) if x["pr_url"]]
    if not rows:
        die(f"thread {r['id']} has no PR; use land --strategy pr")
    deadline = time.time() + args.timeout
    last_states = {}
    json_rows = []
    while True:
        all_terminal = True
        for rr in repo_rows(conn, r):
            if not rr["pr_url"]:
                continue
            data = gh_pr_view(rr["pr_url"], rr["repo"])
            update_pr_repo(conn, cfg, r, rr, data)
            state = data.get("state")
            last_states[rr["id"]] = state
            if state not in ("MERGED", "CLOSED"):
                all_terminal = False
            summary = {
                "id": r["id"], "repo": rr["repo"], "url": rr["pr_url"], "state": state,
                "ci": ci_state(data.get("statusCheckRollup")),
                "mergeable": data.get("mergeable"), "review": data.get("reviewDecision"),
                "draft": data.get("isDraft"), "title": data.get("title"),
                "checks": len(data.get("statusCheckRollup") or []),
            }
            if args.json:
                json_rows.append(summary)
            else:
                print(f"{r['id']}  {Path(rr['repo']).name:16}  {state}  ci={summary['ci']}  "
                      f"mergeable={summary['mergeable'] or '-'} review={summary['review'] or '-'} "
                      f"checks={summary['checks']}")
                print(f"  {rr['pr_url']}")
        if not args.watch or all_terminal or time.time() >= deadline:
            break
        time.sleep(args.interval)
    if args.json:
        print(json.dumps(json_rows, ensure_ascii=False, indent=2))
    if args.watch and not all_terminal:
        print("watch timed out", file=sys.stderr)
        raise SystemExit(2)


def record_turn(conn, tid: str, role: str, content: str, meta: str = "{}") -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM turns WHERE thread_id=?", (tid,)).fetchone()
    seq = row[0]
    cur = conn.execute(
        "INSERT INTO turns(thread_id,seq,role,content,created_at,meta) VALUES(?,?,?,?,?,?)",
        (tid, seq, role, content, now(), meta),
    )
    conn.commit()
    return cur.lastrowid


def cmd_transcript(args):
    conn = db()
    r = thread_row(conn, args.id)
    conds = ["thread_id=?"]
    params = [r["id"]]
    if args.search:
        conds.append("content LIKE ?")
        params.append(f"%{args.search}%")
    if args.bookmarked:
        conds.append("bookmarked=1")
    if not args.all:
        conds.append("active=1")
    q = "SELECT * FROM turns WHERE " + " AND ".join(conds) + " ORDER BY seq DESC LIMIT ?"
    params.append(args.limit)
    rows = list(reversed(conn.execute(q, params).fetchall()))
    if args.json:
        print(json.dumps([dict(x) for x in rows], ensure_ascii=False, indent=2))
        return
    print(f"transcript {r['id']}  {r['label']}  ({len(rows)} turns)")
    for x in rows:
        mark = "*" if x["bookmarked"] else " "
        inactive = "" if x["active"] else " [inactive]"
        print(f"\n[{x['id']}]{mark} {x['role']} {x['created_at']}{inactive}")
        print(x["content"])


def cmd_turn(args):
    conn = db()
    r = thread_row(conn, args.id)
    if args.add is not None:
        tid = record_turn(conn, r["id"], args.role, args.add)
        print(f"turn {tid} added ({args.role})")
        return
    if args.list:
        cmd_transcript(argparse.Namespace(id=r["id"], search=None, bookmarked=False,
                                         all=True, limit=args.limit, json=args.json))
        return
    if args.bookmark is not None:
        row = conn.execute("SELECT id FROM turns WHERE id=? AND thread_id=?", (args.bookmark, r["id"])).fetchone()
        if not row:
            die(f"turn not found: {args.bookmark}")
        conn.execute("UPDATE turns SET bookmarked=? WHERE id=?", (0 if args.unbookmark else 1, args.bookmark))
        conn.commit()
        print(f"turn {args.bookmark} bookmarked={not args.unbookmark}")
        return
    if args.edit is not None:
        if args.text is None:
            die("--edit requires --text")
        conn.execute("UPDATE turns SET content=? WHERE id=? AND thread_id=?", (args.text, args.edit, r["id"]))
        conn.commit()
        event(conn, r["id"], "turn-edit", str(args.edit))
        print(f"turn {args.edit} edited (metadata only; agent context unchanged)")
        return
    if args.revert is not None:
        row = conn.execute("SELECT seq FROM turns WHERE id=? AND thread_id=?", (args.revert, r["id"])).fetchone()
        if not row:
            die(f"turn not found: {args.revert}")
        conn.execute("UPDATE turns SET active=0 WHERE thread_id=? AND seq>=?", (r["id"], row["seq"]))
        conn.commit()
        event(conn, r["id"], "turn-revert", f"from {args.revert}")
        print(f"turns from {args.revert} marked inactive (metadata only; use checkpoint --restore for files)")
        return
    die("nothing to do; use --add/--list/--bookmark/--edit/--revert")


def cmd_search(args):
    conn = db()
    conds = ["t.content LIKE ?"]
    params = [f"%{args.query}%"]
    if not args.all:
        conds.append("t.active=1")
    q = ("SELECT t.*, th.label FROM turns t JOIN threads th ON th.id=t.thread_id "
         "WHERE " + " AND ".join(conds) + " ORDER BY t.id DESC LIMIT ?")
    params.append(args.limit)
    rows = conn.execute(q, params).fetchall()
    if args.json:
        print(json.dumps([dict(x) for x in rows], ensure_ascii=False, indent=2))
        return
    for x in rows:
        snippet = " ".join(x["content"].split())[:160]
        print(f"{x['thread_id']}  [{x['id']}] {x['role']}  {x['label'][:20]}  {snippet}")
    print(f"{len(rows)} matches")


def cmd_capture(args):
    conn = db()
    r = thread_row(conn, args.id)
    target = r["agent_name"] or r["pane_id"]
    if not target:
        die("thread has no agent")
    p = run(["herdr", "agent", "read", target, "--source", args.source, "--lines", str(args.lines),
             "--format", "text"], check=False)
    if p.returncode != 0:
        die(f"agent read failed: {p.stderr.strip() or p.stdout.strip()}")
    text = p.stdout.strip()
    if not text:
        die("agent output is empty")
    tid = record_turn(conn, r["id"], args.role, text)
    print(f"captured turn {tid} ({len(text)} chars)")


def cmd_comment(args):
    conn = db()
    r = thread_row(conn, args.id)
    if args.list:
        rows = conn.execute(
            "SELECT * FROM comments WHERE thread_id=? ORDER BY id", (r["id"],)
        ).fetchall()
        if args.json:
            print(json.dumps([dict(x) for x in rows], ensure_ascii=False, indent=2))
            return
        for x in rows:
            loc = f"{x['file']}:{x['line']}" if x["file"] else "-"
            print(f"[{x['id']}] {x['status']:5} {loc}  {x['body']}")
        if not rows:
            print("no comments")
        return
    if args.resolve is not None:
        conn.execute("UPDATE comments SET status='resolved' WHERE id=? AND thread_id=?", (args.resolve, r["id"]))
        conn.commit()
        print(f"comment {args.resolve} resolved")
        return
    if args.send:
        rows = conn.execute(
            "SELECT * FROM comments WHERE thread_id=? AND status='open' ORDER BY id", (r["id"],)
        ).fetchall()
        if not rows:
            print("no open comments")
            return
        body = "\n".join(
            f"- {x['file']}:{x['line']}: {x['body']}" if x["file"] else f"- {x['body']}"
            for x in rows
        )
        text = f"Review comments for thread {r['label']} ({r['id']}):\n{body}\nAddress them and report the changes."
        target = r["agent_name"] or r["pane_id"]
        if not target:
            die("thread has no agent")
        herdr("agent", "prompt", target, text)
        conn.execute("UPDATE comments SET status='sent', sent_at=? WHERE thread_id=? AND status='open'",
                     (now(), r["id"]))
        conn.commit()
        record_turn(conn, r["id"], "user", text, '{"kind":"comments"}')
        print(f"sent {len(rows)} comments to {target}")
        return
    if not args.text:
        die("comment text required (or --list/--send/--resolve)")
    cur = conn.execute(
        "INSERT INTO comments(thread_id,file,line,body,created_at) VALUES(?,?,?,?,?)",
        (r["id"], args.file or "", args.line or 0, args.text, now()),
    )
    conn.commit()
    print(f"comment {cur.lastrowid} added")


def cmd_attach(args):
    conn = db()
    r = thread_row(conn, args.id)
    if args.list:
        rows = conn.execute("SELECT * FROM attachments WHERE thread_id=? ORDER BY id", (r["id"],)).fetchall()
        for x in rows:
            print(f"[{x['id']}] {x['source']} -> {x['stored']}")
        if not rows:
            print("no attachments")
        return
    if not args.file:
        die("attach requires a file")
    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        die(f"file not found: {src}")
    dst_dir = Path(r["worktree"]) / ".herdr-thread" / "attachments"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        dst = dst_dir / f"{src.stem}-{int(time.time())}{src.suffix}"
    shutil.copy2(src, dst)
    conn.execute("INSERT INTO attachments(thread_id,source,stored,created_at) VALUES(?,?,?,?)",
                 (r["id"], str(src), str(dst), now()))
    conn.commit()
    print(f"attached {src} -> {dst}")


def cmd_send(args):
    conn = db()
    src = thread_row(conn, args.src)
    dst = thread_row(conn, args.dst)
    target = dst["agent_name"] or dst["pane_id"]
    if not target:
        die("target thread has no agent")
    text = args.text
    if args.with_transcript:
        rows = conn.execute(
            "SELECT role, content FROM turns WHERE thread_id=? AND active=1 ORDER BY seq DESC LIMIT ?",
            (src["id"], args.limit),
        ).fetchall()
        block = "\n\n".join(f"[{x['role']}] {x['content']}" for x in reversed(rows))
        text = f"{text}\n\nContext from thread {src['id']} ({src['label']}):\n{block}"
    herdr("agent", "prompt", target, text)
    record_turn(conn, src["id"], "system", f"sent to {dst['id']}: {text}")
    record_turn(conn, dst["id"], "user", f"from {src['id']}: {text}")
    print(f"sent {src['id']} -> {dst['id']} ({target})")


def cmd_prompt(args):
    conn = db()
    r = thread_row(conn, args.id)
    target = r["agent_name"] or r["pane_id"]
    if not target:
        die("thread has no agent; use --no-agent only with pane-level commands")
    hargs = ["agent", "prompt", target, args.text]
    if args.wait:
        hargs += ["--wait", "--until", args.until or "idle", "--until", "blocked", "--until", "done",
                  "--timeout", str(args.timeout)]
    herdr(*hargs)
    conn.execute("UPDATE threads SET status='working' WHERE id=?", (r["id"],))
    conn.commit()
    event(conn, r["id"], "prompt", args.text[:300])
    record_turn(conn, r["id"], "user", args.text)
    print(f"prompt sent to {target}")


def cmd_review(args):
    cfg = load_config()
    conn = db()
    r = thread_row(conn, args.id)
    ws = r["workspace_id"]
    if ws:
        herdr("workspace", "focus", ws)
    review_pane = r["review_pane_id"] or find_review_pane(ws)
    if not review_pane:
        try:
            herdr("plugin", "pane", "open",
                  "--plugin", cfg["review_plugin"], "--entrypoint", cfg["review_entrypoint"],
                  "--workspace", ws, "--placement", "split", "--direction", "right",
                  "--cwd", r["worktree"], "--focus")
            review_pane = find_review_pane(ws)
        except Exception as e:
            print(f"warning: could not open reviewr: {e}", file=sys.stderr)
    conn.execute("UPDATE threads SET status='review', review_state=? , review_pane_id=? WHERE id=?",
                 (args.verdict or r["review_state"] or "pending", review_pane, r["id"]))
    conn.commit()
    event(conn, r["id"], "review", args.verdict or "opened")

    reviewer_agent = r["reviewer_agent"]
    reviewer_pane = r["reviewer_pane_id"]
    if args.reviewer and not reviewer_agent:
        profile = cfg["roles"].get(args.role, {}) if getattr(args, "role", None) else {}
        if not profile:
            profile = cfg["profiles"].get(args.profile or "reviewer", {})
        kind = args.agent or profile.get("kind") or cfg["default_agent"]
        try:
            split = herdr("pane", "split", r["pane_id"], "--direction", "down",
                          "--cwd", r["worktree"], "--no-focus")
            rp = split.get("pane", {}).get("pane_id")
            if not rp:
                raise RuntimeError(f"unexpected split result: {split}")
            reviewer_agent = f"ht-{r['id']}-rev"
            start_agent(reviewer_agent, kind, rp, ws, r["worktree"], list(profile.get("args") or []),
                        cfg.get("fallback") or [])
            base_prompt = args.reviewer_prompt or profile.get("prompt") or "Review the current changes."
            prompt = (
                f"{base_prompt}\n\nThread: {r['label']} ({r['id']})\n"
                f"Worktree: {r['worktree']}\nBranch: {r['branch']}\n"
                f"Run `herdr-thread diff {r['id']}` for the full diff. "
                "Report findings as file:line, severity, and a concrete fix. Do not modify files."
            )
            herdr("agent", "prompt", reviewer_agent, prompt)
            reviewer_pane = rp
            conn.execute("UPDATE threads SET reviewer_pane_id=?, reviewer_agent=? WHERE id=?",
                         (rp, reviewer_agent, r["id"]))
            conn.commit()
        except Exception as e:
            print(f"warning: could not start reviewer: {e}", file=sys.stderr)
            reviewer_agent = None

    print(f"review thread {r['id']}  worktree={r['worktree']}")
    if review_pane:
        print(f"  reviewr pane {review_pane} (diff scopes: uncommitted/branch/last-turn/commits)")
    if reviewer_agent:
        print(f"  reviewer     {reviewer_agent} ({reviewer_pane or '-'})")
    print("  verdict: herdr-thread review %s --verdict approved|rejected" % r["id"])
    if args.verdict:
        print(f"  review_state = {args.verdict}")
        if cfg.get("notify", True):
            try:
                herdr("notification", "show", f"Review {args.verdict}: {r['label']}",
                      "--body", r["branch"], "--sound", "done" if args.verdict == "approved" else "request")
            except Exception:
                pass


def cmd_checkpoint(args):
    conn = db()
    r = thread_row(conn, args.id)
    repos = repo_rows(conn, r)
    if args.list:
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE thread_id=? ORDER BY id DESC", (r["id"],)
        ).fetchall()
        if args.json:
            print(json.dumps([dict(x) for x in rows], ensure_ascii=False, indent=2))
            return
        if not rows:
            print("no checkpoints")
            return
        for x in rows:
            repo = Path(x["repo"]).name if x["repo"] else "primary"
            print(f"{x['short_sha']}  {repo:16}  {x['created_at']}  {x['message']}")
        return
    if args.restore is not None:
        target = args.restore
        if target in ("", "latest"):
            todo = []
            for rr in repos:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE thread_id=? AND repo=? ORDER BY id DESC LIMIT 1",
                    (r["id"], rr["repo"]),
                ).fetchone()
                if row:
                    todo.append((rr, row))
            if not todo:
                # legacy: primary head only
                if r["head_sha"]:
                    p = run(["git", "-C", r["worktree"], "reset", "--hard", r["head_sha"]], check=False)
                    if p.returncode != 0:
                        die(f"restore failed: {p.stderr.strip() or p.stdout.strip()}")
                    print(f"restored {r['id']} to {r['head_sha']}")
                    return
                die("no checkpoint to restore")
        else:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE thread_id=? AND (sha LIKE ? OR short_sha LIKE ?) ORDER BY id DESC",
                (r["id"], target + "%", target + "%"),
            ).fetchall()
            if not rows:
                die(f"checkpoint not found: {target}")
            todo = []
            for row in rows:
                rr = next((x for x in repos if x["repo"] == row["repo"]), None)
                if not rr and not row["repo"]:
                    rr = repos[0]
                if rr:
                    todo.append((rr, row))
        for rr, row in todo:
            dirty = git(rr["worktree"], "status", "--porcelain", check=False)
            p = run(["git", "-C", rr["worktree"], "reset", "--hard", row["sha"]], check=False)
            if p.returncode != 0:
                die(f"restore failed in {rr['repo']}: {p.stderr.strip() or p.stdout.strip()}")
            if args.clean:
                run(["git", "-C", rr["worktree"], "clean", "-fd"], check=False)
            elif dirty.strip():
                print(f"warning: {Path(rr['repo']).name}: uncommitted tracked changes discarded")
            print(f"restored {Path(rr['repo']).name} to {row['short_sha']}")
        head = git(r["worktree"], "rev-parse", "--short", "HEAD", check=False)
        conn.execute("UPDATE threads SET head_sha=?, status='review' WHERE id=?", (head, r["id"]))
        conn.commit()
        event(conn, r["id"], "restore", target)
        return

    msg = args.message or f"checkpoint: {r['label']}"
    made = []
    for rr in repos:
        wt = rr["worktree"]
        dirty = git(wt, "status", "--porcelain", check=False)
        if not dirty.strip():
            continue
        run(["git", "-C", wt, "add", "-A"])
        p = run(["git", "-C", wt, "commit", "-m", msg], check=False)
        if p.returncode != 0:
            die(f"checkpoint failed in {rr['repo']}: {p.stderr.strip() or p.stdout.strip()}")
        sha = git(wt, "rev-parse", "HEAD", check=False)
        short = sha[:7]
        conn.execute(
            "INSERT INTO checkpoints(thread_id,sha,short_sha,message,created_at,repo,worktree) "
            "VALUES(?,?,?,?,?,?,?)",
            (r["id"], sha, short, msg, now(), rr["repo"], wt),
        )
        made.append((Path(rr["repo"]).name, short))
    if not made:
        print("nothing to checkpoint")
        return
    head = git(r["worktree"], "rev-parse", "--short", "HEAD", check=False)
    conn.execute("UPDATE threads SET head_sha=?, status='review' WHERE id=?", (head, r["id"]))
    conn.commit()
    event(conn, r["id"], "checkpoint", ", ".join(f"{n}:{s}" for n, s in made))
    for n, short in made:
        print(f"checkpoint {short}  {n}")
    print(f"  message: {msg}")


def run_pre_land(cfg, r):
    wt = r["worktree"]
    checks = []
    script = cfg.get("pre_land_script") or ""
    if script:
        checks.append(Path(script).expanduser())
    for rel in (".agents/pre-land", ".herdr-thread/pre-land.sh"):
        checks.append(Path(wt) / rel)
    for path in checks:
        if not path.exists():
            continue
        if not os.access(path, os.X_OK):
            raise RuntimeError(f"pre-land check is not executable: {path}")
        print(f"pre-land: {path}")
        p = subprocess.run([str(path)], cwd=wt, text=True, capture_output=True)
        if p.returncode != 0:
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-15:]
            raise RuntimeError(f"pre-land check failed ({path}): " + " | ".join(tail))
        if p.stdout.strip():
            print(p.stdout.strip()[-2000:])
    for cmd in cfg.get("pre_land_commands") or []:
        print(f"pre-land: {cmd}")
        p = subprocess.run(cmd, shell=True, cwd=wt, text=True, capture_output=True)
        if p.returncode != 0:
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-15:]
            raise RuntimeError(f"pre-land command failed ({cmd}): " + " | ".join(tail))
        if p.stdout.strip():
            print(p.stdout.strip()[-2000:])


def _land_one(cfg, conn, r, args) -> str:
    if r["status"] == "landed":
        raise RuntimeError(f"thread {r['id']} already landed")
    if r["status"] == "cleaned":
        raise RuntimeError(f"thread {r['id']} already cleaned")
    repos = repo_rows(conn, r)
    strategy = args.strategy or cfg["land_strategy"]
    title = args.title or r["label"]
    body = args.body or f"Thread {r['id']} managed by herdr-thread."
    dirty = [(Path(x["repo"]).name, git(x["worktree"], "status", "--porcelain", check=False)) for x in repos]
    dirty = [(n, d) for n, d in dirty if d.strip()]
    if dirty and not args.allow_dirty:
        names = ", ".join(f"{n}({len(d.splitlines())})" for n, d in dirty)
        raise RuntimeError(f"uncommitted changes in {names}; run checkpoint or pass --allow-dirty")
    if r["review_state"] == "rejected" and not args.force:
        raise RuntimeError("review is rejected; pass --force to override")
    if cfg.get("require_approval") and r["review_state"] != "approved" and not args.force:
        raise RuntimeError(f"review_state is {r['review_state'] or 'empty'}; approval required (or --force)")
    if not args.skip_checks:
        run_pre_land(cfg, r)

    messages = []
    if strategy == "push":
        for rr in repos:
            origin = git(rr["worktree"], "remote", "get-url", "origin", check=False)
            if not origin:
                raise RuntimeError(f"{Path(rr['repo']).name}: no origin remote")
            p = run(["git", "-C", rr["worktree"], "push", "-u", "origin", rr["branch"]], check=False)
            if p.returncode != 0:
                raise RuntimeError(f"{Path(rr['repo']).name}: push failed: {p.stderr.strip() or p.stdout.strip()}")
            messages.append(f"{Path(rr['repo']).name}: pushed {rr['branch']}")
        conn.execute("UPDATE threads SET status='landed', landed_at=? WHERE id=?", (now(), r["id"]))
    elif strategy == "pr":
        for rr in repos:
            if rr["pr_url"]:
                messages.append(f"{Path(rr['repo']).name}: existing PR {rr['pr_url']}")
                continue
            origin = git(rr["worktree"], "remote", "get-url", "origin", check=False)
            if not origin:
                raise RuntimeError(f"{Path(rr['repo']).name}: no origin remote")
            p = run(["git", "-C", rr["worktree"], "push", "-u", "origin", rr["branch"]], check=False)
            if p.returncode != 0:
                raise RuntimeError(f"{Path(rr['repo']).name}: push failed: {p.stderr.strip() or p.stdout.strip()}")
            p = run(["gh", "pr", "create", "--head", rr["branch"], "--title", title, "--body", body],
                    cwd=rr["worktree"], check=False)
            if p.returncode != 0:
                raise RuntimeError(f"{Path(rr['repo']).name}: gh pr create failed: {p.stderr.strip() or p.stdout.strip()}")
            out = p.stdout.strip()
            m = re.search(r"https://\S+/pull/(\d+)", out)
            url = m.group(0) if m else (out.splitlines()[-1] if out else "")
            number = int(m.group(1)) if m else None
            conn.execute(
                "UPDATE thread_repos SET pr_url=?, pr_number=?, pr_state='OPEN' WHERE id=?",
                (url, number, rr["id"]),
            )
            messages.append(f"{Path(rr['repo']).name}: PR {url or 'created'}")
        primary = repos[0]
        conn.execute(
            "UPDATE threads SET status='pr_open', pr_url=?, pr_number=?, pr_state='OPEN', landed_at=NULL WHERE id=?",
            (primary["pr_url"], primary["pr_number"], r["id"]),
        )
        # refresh primary pr_url after potential update
        prow = conn.execute("SELECT * FROM thread_repos WHERE thread_id=? ORDER BY primary_repo DESC, id LIMIT 1",
                            (r["id"],)).fetchone()
        if prow:
            conn.execute("UPDATE threads SET pr_url=?, pr_number=?, pr_state=? WHERE id=?",
                         (prow["pr_url"], prow["pr_number"], prow["pr_state"], r["id"]))
    elif strategy == "merge":
        into = args.into
        if not into:
            raise RuntimeError("--strategy merge requires --into <branch>")
        for rr in repos:
            if git(rr["repo"], "status", "--porcelain", check=False).strip():
                raise RuntimeError(f"{Path(rr['repo']).name}: source repo has uncommitted changes")
            p = run(["git", "-C", rr["repo"], "checkout", into], check=False)
            if p.returncode != 0:
                raise RuntimeError(f"{Path(rr['repo']).name}: checkout {into} failed")
            p = run(["git", "-C", rr["repo"], "merge", "--no-ff", rr["branch"]], check=False)
            if p.returncode != 0:
                run(["git", "-C", rr["repo"], "merge", "--abort"], check=False)
                raise RuntimeError(f"{Path(rr['repo']).name}: merge failed: {p.stderr.strip() or p.stdout.strip()}")
            messages.append(f"{Path(rr['repo']).name}: merged into {into}")
        conn.execute("UPDATE threads SET status='landed', landed_at=? WHERE id=?", (now(), r["id"]))
    elif strategy == "merge-pr":
        method = args.method or "squash"
        candidates = [rr for rr in repos if rr["pr_url"]]
        if not candidates:
            raise RuntimeError("no PR; use land --strategy pr first")
        for rr in candidates:
            cmd = ["gh", "pr", "merge", rr["pr_url"], f"--{method}"]
            if args.delete_branch:
                cmd.append("--delete-branch")
            if args.auto:
                cmd.append("--auto")
            p = run(cmd, cwd=rr["worktree"], check=False)
            if p.returncode != 0:
                raise RuntimeError(f"{Path(rr['repo']).name}: gh pr merge failed: {p.stderr.strip() or p.stdout.strip()}")
            if not args.auto:
                conn.execute("UPDATE thread_repos SET pr_state='MERGED' WHERE id=?", (rr["id"],))
            messages.append(f"{Path(rr['repo']).name}: PR {'auto-merge enabled' if args.auto else 'merged'}")
        if args.auto:
            conn.execute("UPDATE threads SET status='pr_open' WHERE id=?", (r["id"],))
        else:
            conn.execute("UPDATE threads SET status='landed', pr_state='MERGED', landed_at=? WHERE id=?",
                         (now(), r["id"]))
    elif strategy == "hook":
        hook = HOME / ".config" / "herdr-thread" / "land.sh"
        if not hook.exists():
            raise RuntimeError(f"land hook not found: {hook}")
        env = os.environ.copy()
        env.update({
            "THREAD_ID": r["id"], "THREAD_LABEL": r["label"],
            "REPO": r["repo"], "WORKTREE": r["worktree"], "BRANCH": r["branch"],
            "REPOS": json.dumps([dict(x) for x in repos], ensure_ascii=False),
        })
        p = subprocess.run([str(hook)], cwd=r["worktree"], env=env)
        if p.returncode != 0:
            raise RuntimeError(f"land hook failed ({p.returncode})")
        conn.execute("UPDATE threads SET status='landed', landed_at=? WHERE id=?", (now(), r["id"]))
        messages.append(f"land hook completed: {hook}")
    else:
        raise RuntimeError(f"unknown land strategy: {strategy}")

    conn.commit()
    msg = "; ".join(messages)
    event(conn, r["id"], "landed", f"{strategy}: {msg}")
    if cfg.get("notify", True):
        try:
            herdr("notification", "show", f"Landed: {r['label']}",
                  "--body", f"{len(repos)} repo(s) ({strategy})", "--sound", "done")
        except Exception:
            pass
    if args.cleanup:
        r = conn.execute("SELECT * FROM threads WHERE id=?", (r["id"],)).fetchone()
        cleanup_thread(conn, r, force=True)
    return msg


def _land_rows(conn, args):
    if getattr(args, "id", None):
        return [thread_row(conn, x) for x in args.id]
    conds = ["archived = 0", "status NOT IN ('landed','cleaned')"]
    params = []
    if getattr(args, "tag", None):
        conds.append("tags LIKE ?")
        params.append(f"%{args.tag}%")
    if not getattr(args, "all", False) and not getattr(args, "tag", None):
        die("land needs thread id(s), --all, or --tag")
    rows = conn.execute(
        "SELECT * FROM threads WHERE " + " AND ".join(conds) + " ORDER BY created_at",
        params,
    ).fetchall()
    return rows


def cmd_land(args):
    cfg = load_config()
    conn = db()
    rows = _land_rows(conn, args)
    if not rows:
        print("no threads to land")
        return
    strategy = args.strategy or cfg["land_strategy"]
    if args.into is None and strategy == "merge":
        die("--strategy merge requires --into <branch>")
    if len(rows) > 1 and not args.yes and not args.dry_run:
        print("batch land plan:")
        for r in rows:
            print(f"  {r['id']}  {r['label'][:30]:30}  {r['branch']}  via {strategy}")
        die("refusing batch land without --yes (or use --dry-run)")

    ok, failed = 0, 0
    results = []
    for r in rows:
        if args.dry_run:
            print(f"would land {r['id']}  {r['branch']}  via {strategy}")
            results.append({"id": r["id"], "ok": None, "dry_run": True})
            continue
        try:
            msg = _land_one(cfg, conn, r, args)
            print(f"{r['id']}: {msg}")
            results.append({"id": r["id"], "ok": True, "message": msg})
            ok += 1
        except Exception as e:
            failed += 1
            results.append({"id": r["id"], "ok": False, "error": str(e)})
            print(f"{r['id']}: FAILED {e}", file=sys.stderr)
            if not args.continue_on_error:
                print(f"stopped after failure; {len(rows) - ok - failed} not attempted", file=sys.stderr)
                break
    if args.json:
        print(json.dumps({"ok": ok, "failed": failed, "results": results}, ensure_ascii=False, indent=2))
    elif not args.dry_run:
        print(f"land summary: ok={ok} failed={failed}")
    if failed:
        raise SystemExit(1)


def cleanup_thread(conn, r, force=False):
    ws = r["workspace_id"]
    ok = True
    repos = repo_rows(conn, r)
    if ws:
        try:
            herdr("worktree", "remove", "--workspace", ws, "--force")
        except Exception as e:
            if "not_linked_worktree" not in str(e):
                print(f"warning: herdr worktree remove failed: {e}", file=sys.stderr)
            try:
                herdr("workspace", "close", ws)
            except Exception as ce:
                ok = False
                print(f"warning: workspace close failed for {ws}: {ce}", file=sys.stderr)
    for rr in repos:
        if not Path(rr["worktree"]).exists():
            continue
        p = run(["git", "-C", rr["repo"], "worktree", "remove", "--force", rr["worktree"]], check=False)
        if p.returncode != 0:
            ok = False
            print(f"warning: git worktree remove failed for {rr['repo']}: {p.stderr.strip()[:200]}", file=sys.stderr)
    keep = r["status"] if r["status"] in ("landed", "pr_open", "closed") else "cleaned"
    conn.execute("UPDATE threads SET status=?, archived=1 WHERE id=?", (keep, r["id"]))
    conn.commit()
    event(conn, r["id"], "cleaned", "worktrees removed" if ok else "some worktrees may remain")
    print(f"cleaned {r['id']}  {'(worktrees removed)' if ok else '(some worktrees may remain)'}")


def cmd_cleanup(args):
    conn = db()
    r = thread_row(conn, args.id)
    cleanup_thread(conn, r, force=args.force)


def cmd_diff(args):
    conn = db()
    r = thread_row(conn, args.id)
    repos = repo_rows(conn, r)
    print(f"# thread {r['id']} {r['label']}  ({len(repos)} repo(s))")
    if r["base_sha"]:
        print(f"# base {r['base_sha'][:7]}")
    print()
    for rr in repos:
        wt = rr["worktree"]
        base = rr["base_sha"] or rr["base_ref"] or "HEAD"
        name = Path(rr["repo"]).name
        print(f"## {name}  ({rr['branch']})  {wt}")
        committed = git(wt, "diff", f"{base}..HEAD", check=False)
        if committed:
            print(committed)
        unstaged = git(wt, "diff", check=False)
        if unstaged:
            print("\n# uncommitted\n" + unstaged)
        if not committed and not unstaged:
            print("(no changes)")
        print()


def cmd_open(args):
    conn = db()
    r = thread_row(conn, args.id)
    if not r["workspace_id"]:
        die("thread has no workspace")
    herdr("workspace", "focus", r["workspace_id"])
    print(f"focused {r['workspace_id']}  ({r['label']})")


def cmd_tag(args):
    conn = db()
    r = thread_row(conn, args.id)
    tags = set(filter(None, (r["tags"] or "").split(",")))
    if args.add:
        tags.update(args.add.split(","))
    if args.remove:
        tags -= set(args.remove.split(","))
    conn.execute("UPDATE threads SET tags=? WHERE id=?", (",".join(sorted(tags)), r["id"]))
    if args.bookmark is not None:
        conn.execute("UPDATE threads SET bookmarked=? WHERE id=?", (1 if args.bookmark else 0, r["id"]))
    if args.archive is not None:
        conn.execute("UPDATE threads SET archived=? WHERE id=?", (1 if args.archive else 0, r["id"]))
    conn.commit()
    print(f"{r['id']} tags={','.join(sorted(tags)) or '-'}")


def cmd_sync(args):
    cfg = load_config()
    conn = db()
    states = agent_states()
    rows = conn.execute(
        "SELECT * FROM threads WHERE archived=0 "
        "OR (pr_url IS NOT NULL AND COALESCE(pr_state,'') NOT IN ('MERGED','CLOSED'))"
    ).fetchall()
    results = []
    polled = 0
    for r in rows:
        a = states.get(r["pane_id"] or "", {})
        astate = a.get("agent_status")
        info = git_info(r["worktree"])
        if astate == "working" and r["status"] != "review":
            new_status = "working"
        elif astate in ("idle", "done") and r["status"] == "working":
            new_status = "review"
        else:
            new_status = r["status"]
        if args.rebase and Path(r["worktree"]).exists():
            run(["git", "-C", r["worktree"], "fetch", "--all", "--prune"], check=False)
            p = run(["git", "-C", r["worktree"], "rebase", "--autostash", "@{u}"], check=False)
            if p.returncode != 0:
                run(["git", "-C", r["worktree"], "rebase", "--abort"], check=False)
                print(f"{r['id']}: rebase failed (aborted): {p.stderr.strip()[:200]}", file=sys.stderr)
        if new_status != r["status"]:
            conn.execute("UPDATE threads SET status=? WHERE id=?", (new_status, r["id"]))
            event(conn, r["id"], "status", f"{r['status']} -> {new_status}")
            if cfg.get("notify", True) and new_status == "review":
                try:
                    herdr("notification", "show", f"Review ready: {r['label']}",
                          "--body", f"{r['branch']} · dirty={info['dirty']}", "--sound", "request")
                except Exception:
                    pass
        if args.pr:
            for rr in repo_rows(conn, r):
                if not rr["pr_url"]:
                    continue
                try:
                    if update_pr_repo(conn, cfg, r, rr, gh_pr_view(rr["pr_url"], rr["repo"])):
                        polled += 1
                except Exception as e:
                    print(f"{r['id']} {Path(rr['repo']).name}: PR poll failed: {e}", file=sys.stderr)
        r2 = conn.execute("SELECT * FROM threads WHERE id=?", (r["id"],)).fetchone()
        results.append({**dict(r2), "git": info, "agent_state": astate})
    conn.commit()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    print(f"synced {len(rows)} threads" + (f", PR changes={polled}" if args.pr else ""))


def cmd_recover(args):
    conn = db()
    rows = conn.execute("SELECT * FROM threads WHERE archived=0").fetchall()
    workspaces = herdr("workspace", "list").get("workspaces", [])
    agents = agent_states()
    report = []
    for r in rows:
        wt = Path(r["worktree"])
        item = {"id": r["id"], "label": r["label"], "worktree": str(wt), "action": ""}
        missing = [rr for rr in repo_rows(conn, r) if not Path(rr["worktree"]).exists()]
        if missing:
            item["action"] = "orphaned (worktree missing: " + ", ".join(Path(x["repo"]).name for x in missing) + ")"
            conn.execute("UPDATE threads SET status='orphaned' WHERE id=?", (r["id"],))
            if args.prune:
                conn.execute("UPDATE threads SET archived=1 WHERE id=?", (r["id"],))
                item["action"] += " + archived"
            report.append(item)
            continue
        ws = None
        for w in workspaces:
            wpath = (w.get("worktree") or {}).get("checkout_path")
            if wpath and Path(wpath).resolve() == wt.resolve():
                ws = w
                break
        if ws is None and args.create:
            res = herdr("workspace", "create", "--cwd", str(wt), "--label", r["label"], "--no-focus")
            ws = res.get("workspace", {})
            root = res.get("root_pane", {})
            ws_id = ws.get("workspace_id") or root.get("workspace_id")
            pane_id = root.get("pane_id")
            if not ws_id:
                item["action"] = "workspace create failed"
                report.append(item)
                continue
            workspaces = herdr("workspace", "list").get("workspaces", [])
            item["action"] = f"created workspace {ws_id}"
        elif ws is None:
            item["action"] = "unbound (workspace missing; use --create)"
            report.append(item)
            continue
        else:
            ws_id = ws.get("workspace_id")
            item["action"] = f"bound workspace {ws_id}"
        ws_id = ws.get("workspace_id") or ws_id
        panes = pane_list(ws_id)
        pane_ids = {p.get("pane_id") for p in panes}
        pane_id = r["pane_id"] if r["pane_id"] in pane_ids else None
        if not pane_id:
            for pid, a in agents.items():
                if Path(a.get("cwd") or "").resolve() == wt.resolve() and pid in pane_ids:
                    pane_id = pid
                    break
        if not pane_id and panes:
            pane_id = panes[0].get("pane_id")
        agent_name = r["agent_name"]
        if pane_id:
            a = agents.get(pane_id)
            if a:
                agent_name = agent_name or f"ht-{r['id']}"
                item["action"] += f", agent {agent_name} ({a.get('agent_status')})"
            elif args.start and r["agent_kind"]:
                agent_name = f"ht-{r['id']}"
                start_agent(agent_name, r["agent_kind"], pane_id, ws_id, str(wt))
                item["action"] += f", started {agent_name}"
            else:
                agent_name = agent_name if r["agent_name"] else None
                item["action"] += ", no agent"
        review_pane = find_review_pane(ws_id)
        conn.execute(
            "UPDATE threads SET workspace_id=?, pane_id=?, agent_name=?, review_pane_id=? WHERE id=?",
            (ws_id, pane_id, agent_name, review_pane, r["id"]),
        )
        conn.commit()
        event(conn, r["id"], "recover", item["action"])
        report.append(item)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for x in report:
        print(f"{x['id']}  {x['label'][:24]:24}  {x['action']}")
    print(f"recovered {len(report)} active threads")


def cmd_watch(args):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    pidfile = DATA_DIR / "watch.pid"
    logfile = DATA_DIR / "watch.log"
    if args.stop:
        if not pidfile.exists():
            print("watch is not running")
            return
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"stopped watch pid={pid}")
        except ProcessLookupError:
            print("watch process not found; removing stale pid file")
        except ValueError:
            print("invalid pid file; removing")
        pidfile.unlink(missing_ok=True)
        return
    if args.status:
        if not pidfile.exists():
            print("watch: stopped")
            return
        pid = int(pidfile.read_text().strip())
        try:
            os.kill(pid, 0)
            print(f"watch: running pid={pid} log={logfile}")
        except ProcessLookupError:
            print("watch: stale pid file")
        return
    if args.daemon:
        log = logfile.open("a")
        child = [sys.executable, str(Path(__file__).resolve()), "watch",
                 "--interval", str(args.interval)]
        p = subprocess.Popen(child, stdout=log, stderr=log, start_new_session=True)
        pidfile.write_text(str(p.pid))
        print(f"watch started pid={p.pid} interval={args.interval}s log={logfile}")
        return
    ns = argparse.Namespace(rebase=False, pr=True, json=False)
    while True:
        try:
            cmd_sync(ns)
        except Exception as e:
            print(f"watch: sync failed: {e}", file=sys.stderr)
        if args.once:
            break
        time.sleep(args.interval)


def clone_extra_repos(conn, parent, child, cfg):
    made = []
    for rr in repo_rows(conn, parent)[1:]:
        base = git(rr["worktree"], "rev-parse", "HEAD", check=False)
        if not base:
            raise RuntimeError(f"cannot read HEAD in {rr['worktree']}")
        name = Path(rr["repo"]).name
        wt = str(Path(cfg["worktree_root"]) / name / f"{slugify(child['label'])}-{child['id']}")
        create_extra_worktree(rr["repo"], child["branch"], base, wt)
        conn.execute(
            "INSERT INTO thread_repos(thread_id,repo,worktree,branch,base_ref,base_sha,primary_repo) "
            "VALUES(?,?,?,?,?,?,0)",
            (child["id"], rr["repo"], wt, child["branch"], rr["base_ref"], base),
        )
        made.append(wt)
    conn.commit()
    return made


def cmd_fork(args):
    conn = db()
    parent = thread_row(conn, args.id)
    base = git(parent["worktree"], "rev-parse", "HEAD", check=False)
    ns = argparse.Namespace(
        label=args.label, repo=parent["repo"], base=base, branch=None, worktree=None,
        agent=args.agent or parent["agent_kind"], no_agent=args.no_agent,
        prompt=args.prompt, focus=args.focus, tag=None, parent=parent["id"],
        trust_repository=False, profile=None, run_hooks=False,
    )
    tid = cmd_new(ns)
    cfg = load_config()
    child = thread_row(conn, tid)
    try:
        extra_wts = clone_extra_repos(conn, parent, child, cfg)
    except Exception as e:
        cleanup_thread(conn, child, force=True)
        die(f"fork failed while cloning extra repos: {e}")
    for wt in extra_wts:
        print(f"  + worktree {wt}")
    if args.with_context:
        child = thread_row(conn, tid)
        q = "SELECT * FROM turns WHERE thread_id=? AND active=1"
        params = [parent["id"]]
        if args.from_turn is not None:
            trow = conn.execute("SELECT seq FROM turns WHERE id=? AND thread_id=?",
                                (args.from_turn, parent["id"])).fetchone()
            if not trow:
                die(f"turn not found: {args.from_turn}")
            q += " AND seq<=?"
            params.append(trow["seq"])
        q += " ORDER BY seq"
        rows = conn.execute(q, params).fetchall()
        text = "\n\n".join(f"[{x['role']}] {x['content']}" for x in rows)
        ctx = Path(child["worktree"]) / ".herdr-thread" / "fork-context.md"
        ctx.parent.mkdir(parents=True, exist_ok=True)
        ctx.write_text(f"# Fork context from {parent['id']} ({parent['label']})\n\n{text}\n")
        record_turn(conn, tid, "system", f"fork context copied from {parent['id']}: {ctx}")
        if child["agent_name"]:
            herdr("agent", "prompt", child["agent_name"],
                  f"Read {ctx} for context from the parent thread, then continue the task.")
            record_turn(conn, tid, "user", f"Read {ctx} for context from the parent thread, then continue.")
        print(f"  context   {ctx} ({len(rows)} turns)")
    print(f"forked {parent['id']} -> {tid}" + (f" ({len(extra_wts)} extra repo(s))" if extra_wts else ""))


def cmd_subagent(args):
    cfg = load_config()
    conn = db()
    parent = thread_row(conn, args.id)
    if args.list:
        rows = conn.execute("SELECT * FROM subagents WHERE parent_id=? ORDER BY id", (parent["id"],)).fetchall()
        if args.json:
            print(json.dumps([dict(x) for x in rows], ensure_ascii=False, indent=2))
            return
        for x in rows:
            print(f"[{x['id']}] {x['profile']:8} {x['kind']:8} {x['status']:8} "
                  f"{x['agent_name'] or x['child_thread_id'] or '-'}  {x['result'] or ''}")
        if not rows:
            print("no subagents")
        return
    profile = cfg["roles"].get(args.role, {}) if args.role else cfg["profiles"].get(args.profile, {})
    prompt = args.prompt or profile.get("prompt") or f"Subagent profile: {args.profile}"
    kind = args.agent or profile.get("kind") or parent["agent_kind"] or cfg["default_agent"]
    if args.in_place:
        split = herdr("pane", "split", parent["pane_id"], "--direction", "down",
                      "--cwd", parent["worktree"], "--no-focus")
        rp = split.get("pane", {}).get("pane_id")
        if not rp:
            die(f"unexpected split result: {split}")
        name = args.name or f"ht-{parent['id']}-{args.profile}-{uuid.uuid4().hex[:4]}"
        start_agent(name, kind, rp, parent["workspace_id"], parent["worktree"],
                    list(profile.get("args") or []), cfg.get("fallback") or [])
        herdr("agent", "prompt", name, prompt)
        cur = conn.execute(
            "INSERT INTO subagents(parent_id,profile,kind,pane_id,agent_name,status,created_at) "
            "VALUES(?,?,?,?,?,'working',?)",
            (parent["id"], args.profile, kind, rp, name, now()),
        )
        conn.commit()
        record_turn(conn, parent["id"], "system", f"subagent {cur.lastrowid} started in {rp}: {prompt}")
        print(f"subagent {cur.lastrowid}  {args.profile}  {kind}  pane={rp}  agent={name}")
        return
    ns = argparse.Namespace(
        label=args.label or f"{parent['label']} · {args.profile}",
        repo=parent["repo"], base=git(parent["worktree"], "rev-parse", "HEAD", check=False),
        branch=None, worktree=None,
        agent=kind, no_agent=False, prompt=prompt, focus=args.focus,
        tag=f"subagent,{args.profile}", parent=parent["id"], trust_repository=False,
        profile=None, run_hooks=False,
    )
    tid = cmd_new(ns)
    child = thread_row(conn, tid)
    cur = conn.execute(
        "INSERT INTO subagents(parent_id,child_thread_id,profile,kind,agent_name,status,created_at) "
        "VALUES(?,?,?,?,?,'working',?)",
        (parent["id"], tid, args.profile, kind, child["agent_name"], now()),
    )
    conn.commit()
    print(f"subagent {cur.lastrowid}  {args.profile}  thread={tid}")


def cmd_join(args):
    conn = db()
    parent = thread_row(conn, args.parent)
    row = conn.execute(
        "SELECT * FROM subagents WHERE parent_id=? AND (CAST(id AS TEXT)=? OR child_thread_id LIKE ? OR agent_name LIKE ?) "
        "ORDER BY id DESC LIMIT 1",
        (parent["id"], args.sub, args.sub + "%", args.sub + "%"),
    ).fetchone()
    if not row:
        die(f"subagent not found: {args.sub}")
    target = row["agent_name"]
    if row["child_thread_id"]:
        child = thread_row(conn, row["child_thread_id"])
        target = child["agent_name"]
    if not target:
        die("subagent has no agent")
    p = run(["herdr", "agent", "wait", target, "--until", "idle", "--until", "blocked",
             "--until", "done", "--timeout", str(args.timeout)], check=False)
    if p.returncode != 0:
        print(f"warning: wait failed/timed out: {p.stderr.strip()[:200]}", file=sys.stderr)
    p = run(["herdr", "agent", "read", target, "--source", "recent", "--lines", str(args.lines),
             "--format", "text"], check=False)
    if p.returncode != 0:
        die(f"agent read failed: {p.stderr.strip() or p.stdout.strip()}")
    text = p.stdout.strip()
    outdir = Path(parent["worktree"]) / ".herdr-thread" / "subagents"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"sub-{row['id']}.md"
    out.write_text(f"# Subagent {row['id']} ({row['profile']}/{row['kind']})\n\n{text}\n")
    conn.execute("UPDATE subagents SET status='joined', result=? WHERE id=?", (str(out), row["id"]))
    conn.commit()
    event(conn, parent["id"], "join", f"subagent {row['id']} -> {out}")
    print(f"joined subagent {row['id']} -> {out} ({len(text)} chars)")
    if not args.no_send and (parent["agent_name"] or parent["pane_id"]):
        target_parent = parent["agent_name"] or parent["pane_id"]
        msg = f"Subagent {row['id']} ({row['profile']}) finished. Read {out} and use its findings."
        herdr("agent", "prompt", target_parent, msg)
        record_turn(conn, parent["id"], "user", msg)
        print(f"sent result to {target_parent}")


def cmd_runner(args):
    conn = db()
    if args.action not in ("list",) and not args.name:
        die(f"runner {args.action} requires a name")
    if args.action == "list":
        rows = conn.execute("SELECT * FROM runners ORDER BY id").fetchall()
        for x in rows:
            fwd = f"  -L {x['local_port']}->{x['remote_port']}" if x["local_port"] else ""
            print(f"{x['name']:12} {x['status']:8} {x['host']}{fwd}  {x['repo']}")
        if not rows:
            print("no runners")
        return
    if args.action == "add":
        conn.execute(
            "INSERT OR REPLACE INTO runners(name,host,repo,status,created_at) VALUES(?,?,?,?,?)",
            (args.name, args.host, args.repo or "", "unknown", now()),
        )
        conn.commit()
        print(f"runner {args.name} -> {args.host}")
        return
    if args.action in ("up", "down"):
        row = conn.execute("SELECT * FROM runners WHERE name=?", (args.name,)).fetchone()
        if not row and args.host:
            conn.execute("INSERT INTO runners(name,host,repo,status,created_at) VALUES(?,?,?,?,?)",
                         (args.name, args.host, args.repo or "", "unknown", now()))
            conn.commit()
            row = conn.execute("SELECT * FROM runners WHERE name=?", (args.name,)).fetchone()
        if not row:
            die(f"runner not found: {args.name} (use --host or runner add)")
        script = HOME / ".config" / "herdr-thread" / f"runner-{args.action}.sh"
        if not script.exists():
            conn.execute("UPDATE runners SET status=? WHERE name=?", ("up" if args.action == "up" else "down", args.name))
            conn.commit()
            print(f"runner {args.name} marked {args.action} (no {script})")
            return
        env = os.environ.copy()
        env.update({"RUNNER_NAME": row["name"], "RUNNER_HOST": row["host"], "RUNNER_REPO": row["repo"]})
        p = subprocess.run([str(script)], env=env)
        if p.returncode != 0:
            die(f"runner {args.action} script failed ({p.returncode})")
        conn.execute("UPDATE runners SET status=? WHERE name=?", (args.action, args.name))
        conn.commit()
        print(f"runner {args.name} {args.action} ok")
        return
    if args.action == "bootstrap":
        row = conn.execute("SELECT * FROM runners WHERE name=?", (args.name,)).fetchone()
        if not row:
            die(f"runner not found: {args.name}")
        path = args.path or f"~/work/{Path(row['repo']).stem or 'repo'}"
        repo = args.repo or row["repo"]
        if not repo:
            die("bootstrap needs --repo URL (or runner add --repo)")
        remote = (
            f"set -eu; mkdir -p '{path}'; "
            f"if [ ! -d '{path}/.git' ]; then git clone '{repo}' '{path}'; fi; "
            f"cd '{path}'; "
            f"if [ -x .agents/prepare ]; then ./.agents/prepare; fi; "
            f"if [ -x .agents/setup ]; then ./.agents/setup; fi; "
            f"herdr server start >/dev/null 2>&1 || true; echo BOOTSTRAP_OK"
        )
        p = subprocess.run(["ssh", row["host"], remote], text=True)
        if p.returncode != 0:
            die(f"bootstrap failed on {row['host']}")
        conn.execute("UPDATE runners SET status='ready', repo=? WHERE name=?", (repo, row["name"]))
        conn.commit()
        print(f"runner {args.name} bootstrapped at {path}")
        return
    if args.action == "forward":
        row = conn.execute("SELECT * FROM runners WHERE name=?", (args.name,)).fetchone()
        if not row:
            die(f"runner not found: {args.name}")
        cmd = ["ssh", "-N", "-L", f"127.0.0.1:{args.local}:127.0.0.1:{args.remote}", row["host"]]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        conn.execute("UPDATE runners SET pid=?, local_port=?, remote_port=? WHERE name=?",
                     (p.pid, args.local, args.remote, row["name"]))
        conn.commit()
        print(f"forward {args.name}: 127.0.0.1:{args.local} -> {row['host']}:{args.remote} (pid {p.pid})")
        return
    if args.action == "unforward":
        row = conn.execute("SELECT * FROM runners WHERE name=?", (args.name,)).fetchone()
        if not row:
            die(f"runner not found: {args.name}")
        if row["pid"]:
            try:
                os.kill(row["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        conn.execute("UPDATE runners SET pid=NULL, local_port=NULL, remote_port=NULL WHERE name=?", (row["name"],))
        conn.commit()
        print(f"forward stopped for {args.name}")
        return


def cmd_share(args):
    conn = db()
    r = thread_row(conn, args.id)
    host = args.host or f"{os.environ.get('USER', 'user')}@{os.uname().nodename}"
    print(f"# thread {r['id']} {r['label']}")
    print(f"# worktree {r['worktree']}")
    print(f"# branch   {r['branch']}")
    print()
    print("Attach to this herdr server from another machine:")
    print(f"  herdr --remote {host}")
    print()
    print("Or control it through a saved machine:")
    print(f"  herdr machine add   # add {host}")
    print(f"  herdr --machine <label> agent list")
    print()
    print(f"Review locally with: herdr-thread review {r['id']}")
    if args.write:
        out = Path(r["worktree"]) / ".herdr-thread" / "share.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"# {r['label']}\n\n- thread: {r['id']}\n- worktree: {r['worktree']}\n"
                       f"- branch: {r['branch']}\n- attach: `herdr --remote {host}`\n")
        print(f"wrote {out}")


def cmd_machines(args):
    p = subprocess.run(["herdr", "machine", "list", "--json"], text=True, capture_output=True)
    raw = (p.stdout or "").strip()
    if not raw:
        die((p.stderr or "herdr machine list failed").strip())
    machines = json.loads(raw)
    if args.json:
        print(json.dumps(machines, ensure_ascii=False, indent=2))
        return
    if not machines:
        print("no saved machines (add one with: herdr machine add)")
        return
    for m in machines:
        print(f"{m.get('id','?'):12}  {m.get('label',''):20}  {m.get('host','')}  enabled={m.get('enabled')}")


def cmd_remote(args):
    if not args.rest:
        die("usage: herdr-thread remote <machine> <herdr args...>  (e.g. agent list)")
    cmd = ["herdr"]
    if args.machine:
        cmd += ["--machine", args.machine]
    cmd += args.rest
    p = subprocess.run(cmd, text=True)
    raise SystemExit(p.returncode)


def cmd_notify(args):
    hargs = ["notification", "show", args.title]
    if args.body:
        hargs += ["--body", args.body]
    if args.sound:
        hargs += ["--sound", args.sound]
    herdr(*hargs)


def cmd_prune(args):
    conn = db()
    threads = conn.execute("SELECT * FROM threads WHERE archived=0").fetchall()
    reports = []
    for r in threads:
        missing = [rr for rr in repo_rows(conn, r) if not Path(rr["worktree"]).exists()]
        if missing:
            names = ", ".join(Path(x["repo"]).name for x in missing)
            reports.append(f"{r['id']}  {r['label'][:24]:24}  missing: {names}")
            if args.threads:
                conn.execute("UPDATE threads SET status='orphaned', archived=? WHERE id=?",
                             (1 if args.yes else r["archived"], r["id"]))
    conn.commit()
    if args.worktrees:
        for r in threads:
            for rr in repo_rows(conn, r):
                run(["git", "-C", rr["repo"], "worktree", "prune"], check=False)
        print("pruned git worktree metadata")
    if args.workspaces:
        workspaces = herdr("workspace", "list").get("workspaces", [])
        stale = []
        for w in workspaces:
            path = (w.get("worktree") or {}).get("checkout_path")
            if path and not Path(path).exists():
                stale.append(w)
        if stale and not args.yes and not args.dry_run:
            for w in stale:
                print(f"would close {w.get('workspace_id')}  {w.get('label')}  {w.get('worktree', {}).get('checkout_path')}")
            die("refusing to close stale workspaces without --yes (or use --dry-run)")
        for w in stale:
            if args.dry_run:
                print(f"would close {w.get('workspace_id')}  {w.get('label')}")
            else:
                try:
                    herdr("workspace", "close", w["workspace_id"])
                    print(f"closed {w['workspace_id']}  {w.get('label')}")
                except Exception as e:
                    print(f"warning: close {w['workspace_id']} failed: {e}", file=sys.stderr)
    for line in reports:
        print(line)
    if not reports and not args.workspaces:
        print("nothing to prune")


def cmd_backup(args):
    db()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backups = DATA_DIR / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = backups / f"threads-{stamp}.db"
    shutil.copy2(DB_PATH, dst)
    print(dst)


def cmd_doctor(args):
    cfg = load_config()
    print(f"herdr:        {shutil.which('herdr') or 'MISSING'}")
    print(f"git:          {shutil.which('git') or 'MISSING'}")
    print(f"gh:           {shutil.which('gh') or 'MISSING'}")
    print(f"db:           {DB_PATH}")
    print(f"worktree_root {cfg['worktree_root']}")
    p = subprocess.run(["herdr", "status"], text=True, capture_output=True)
    out = (p.stdout or p.stderr).strip()
    print("herdr status:", "running" if "status: running" in out else ("stopped" if out else "ERROR"))
    plugins = subprocess.run(["herdr", "plugin", "list"], text=True, capture_output=True)
    print("reviewr:     ", "installed" if "persiyanov.reviewr" in (plugins.stdout or "") else "MISSING")


# ---------- main ----------

def build_parser():
    p = argparse.ArgumentParser(prog="herdr-thread", description="Delta-like threads on herdr")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create a thread (worktree + workspace + agent)")
    n.add_argument("label")
    n.add_argument("--repo", action="append", help="repo path; repeat for multi-repo")
    n.add_argument("--base")
    n.add_argument("--branch")
    n.add_argument("--worktree")
    n.add_argument("--agent", help="agent kind (codex/claude/pi/...)")
    n.add_argument("--profile", help="profile name from config.toml")
    n.add_argument("--role", help="model role from config.toml [roles.*]")
    n.add_argument("--no-agent", action="store_true")
    n.add_argument("--prompt")
    n.add_argument("--tag")
    n.add_argument("--parent")
    n.add_argument("--run-hooks", action="store_true", help="run .agents/prepare and .agents/setup")
    n.add_argument("--focus", action="store_true")
    n.add_argument("--no-focus", action="store_true")
    n.add_argument("--trust-repository", action="store_true")
    n.set_defaults(func=cmd_new)

    l = sub.add_parser("list", help="list threads")
    l.add_argument("--all", action="store_true")
    l.add_argument("--tag")
    l.add_argument("--bookmarked", action="store_true")
    l.add_argument("--search")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="show a thread")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_show)

    su = sub.add_parser("setup", help="run .agents/prepare and .agents/setup in the worktree")
    su.add_argument("id")
    su.set_defaults(func=cmd_setup)

    pr = sub.add_parser("prompt", help="send a prompt to the thread's agent")
    pr.add_argument("id")
    pr.add_argument("text")
    pr.add_argument("--wait", action="store_true")
    pr.add_argument("--until")
    pr.add_argument("--timeout", type=int, default=600000)
    pr.set_defaults(func=cmd_prompt)

    tr = sub.add_parser("transcript", help="show stored turns")
    tr.add_argument("id")
    tr.add_argument("--search")
    tr.add_argument("--bookmarked", action="store_true")
    tr.add_argument("--all", action="store_true")
    tr.add_argument("--limit", type=int, default=50)
    tr.add_argument("--json", action="store_true")
    tr.set_defaults(func=cmd_transcript)

    tu = sub.add_parser("turn", help="add/list/bookmark/edit/revert turns")
    tu.add_argument("id")
    tu.add_argument("--add")
    tu.add_argument("--role", default="note")
    tu.add_argument("--list", action="store_true")
    tu.add_argument("--bookmark", type=int)
    tu.add_argument("--unbookmark", action="store_true")
    tu.add_argument("--edit", type=int)
    tu.add_argument("--text")
    tu.add_argument("--revert", type=int)
    tu.add_argument("--limit", type=int, default=50)
    tu.add_argument("--json", action="store_true")
    tu.set_defaults(func=cmd_turn)

    se = sub.add_parser("search", help="search stored turns")
    se.add_argument("query")
    se.add_argument("--all", action="store_true")
    se.add_argument("--limit", type=int, default=30)
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=cmd_search)

    cap = sub.add_parser("capture", help="capture current agent output as a turn")
    cap.add_argument("id")
    cap.add_argument("--lines", type=int, default=80)
    cap.add_argument("--source", default="recent")
    cap.add_argument("--role", default="assistant")
    cap.set_defaults(func=cmd_capture)

    cm = sub.add_parser("comment", help="review comments: add/list/send/resolve")
    cm.add_argument("id")
    cm.add_argument("text", nargs="?")
    cm.add_argument("--file")
    cm.add_argument("--line", type=int)
    cm.add_argument("--list", action="store_true")
    cm.add_argument("--send", action="store_true")
    cm.add_argument("--resolve", type=int)
    cm.add_argument("--json", action="store_true")
    cm.set_defaults(func=cmd_comment)

    at = sub.add_parser("attach", help="attach a file to a thread worktree")
    at.add_argument("id")
    at.add_argument("file", nargs="?")
    at.add_argument("--list", action="store_true")
    at.set_defaults(func=cmd_attach)

    sd = sub.add_parser("send", help="send text/transcript from one thread to another")
    sd.add_argument("src")
    sd.add_argument("dst")
    sd.add_argument("text")
    sd.add_argument("--with-transcript", action="store_true")
    sd.add_argument("--limit", type=int, default=10)
    sd.set_defaults(func=cmd_send)

    rv = sub.add_parser("review", help="focus thread and open reviewr")
    rv.add_argument("id")
    rv.add_argument("--verdict", choices=["approved", "rejected"])
    rv.add_argument("--reviewer", action="store_true", help="start a reviewer sub-agent in a split pane")
    rv.add_argument("--profile", default="reviewer")
    rv.add_argument("--role")
    rv.add_argument("--agent")
    rv.add_argument("--reviewer-prompt")
    rv.set_defaults(func=cmd_review)

    c = sub.add_parser("checkpoint", help="commit / list / restore checkpoints")
    c.add_argument("id")
    c.add_argument("-m", "--message")
    c.add_argument("--list", action="store_true")
    c.add_argument("--restore", nargs="?", const="latest")
    c.add_argument("--clean", action="store_true")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_checkpoint)

    ld = sub.add_parser("land", help="push / open PR / merge (supports batch)")
    ld.add_argument("id", nargs="*")
    ld.add_argument("--all", action="store_true")
    ld.add_argument("--tag")
    ld.add_argument("--strategy", choices=["push", "pr", "merge", "merge-pr", "hook"])
    ld.add_argument("--into")
    ld.add_argument("--title")
    ld.add_argument("--body")
    ld.add_argument("--cleanup", action="store_true")
    ld.add_argument("--yes", action="store_true", help="confirm batch land")
    ld.add_argument("--dry-run", action="store_true")
    ld.add_argument("--continue-on-error", action="store_true")
    ld.add_argument("--allow-dirty", action="store_true")
    ld.add_argument("--force", action="store_true", help="override rejected/approval gate")
    ld.add_argument("--skip-checks", action="store_true", help="skip pre-land hooks")
    ld.add_argument("--method", choices=["merge", "squash", "rebase"], default="squash")
    ld.add_argument("--delete-branch", action="store_true")
    ld.add_argument("--auto", action="store_true", help="enable gh auto-merge instead of merging now")
    ld.add_argument("--json", action="store_true")
    ld.set_defaults(func=cmd_land)

    cl = sub.add_parser("cleanup", help="remove the worktree / workspace")
    cl.add_argument("id")
    cl.add_argument("--force", action="store_true")
    cl.set_defaults(func=cmd_cleanup)

    o = sub.add_parser("open", help="focus the thread's workspace")
    o.add_argument("id")
    o.set_defaults(func=cmd_open)

    df = sub.add_parser("diff", help="print committed + uncommitted diff")
    df.add_argument("id")
    df.set_defaults(func=cmd_diff)

    t = sub.add_parser("tag", help="tag/bookmark/archive")
    t.add_argument("id")
    t.add_argument("--add")
    t.add_argument("--remove")
    t.add_argument("--bookmark", type=lambda x: x.lower() != "false", nargs="?", const=True)
    t.add_argument("--archive", type=lambda x: x.lower() != "false", nargs="?", const=True)
    t.set_defaults(func=cmd_tag)

    sy = sub.add_parser("sync", help="refresh thread states, optionally rebase / poll PRs")
    sy.add_argument("--rebase", action="store_true")
    sy.add_argument("--pr", action="store_true", help="poll GitHub PR/CI state")
    sy.add_argument("--json", action="store_true")
    sy.set_defaults(func=cmd_sync)

    rec = sub.add_parser("recover", help="rebind workspaces/agents after herdr restart")
    rec.add_argument("--create", action="store_true", help="create a workspace if missing")
    rec.add_argument("--start", action="store_true", help="start agent if pane has none")
    rec.add_argument("--prune", action="store_true", help="archive threads whose worktree is missing")
    rec.add_argument("--json", action="store_true")
    rec.set_defaults(func=cmd_recover)

    w = sub.add_parser("watch", help="background sync loop (agent state + PR/CI)")
    w.add_argument("--interval", type=int, default=60)
    w.add_argument("--once", action="store_true")
    w.add_argument("--daemon", action="store_true")
    w.add_argument("--stop", action="store_true")
    w.add_argument("--status", action="store_true")
    w.set_defaults(func=cmd_watch)

    prp = sub.add_parser("pr", help="show / watch PR and CI state")
    prp.add_argument("id")
    prp.add_argument("--watch", action="store_true")
    prp.add_argument("--interval", type=int, default=30)
    prp.add_argument("--timeout", type=int, default=3600)
    prp.add_argument("--json", action="store_true")
    prp.set_defaults(func=cmd_pr)

    f = sub.add_parser("fork", help="fork a thread from its current HEAD")
    f.add_argument("id")
    f.add_argument("label")
    f.add_argument("--agent")
    f.add_argument("--no-agent", action="store_true")
    f.add_argument("--prompt")
    f.add_argument("--with-context", action="store_true")
    f.add_argument("--from-turn", type=int)
    f.add_argument("--focus", action="store_true")
    f.set_defaults(func=cmd_fork)

    sa = sub.add_parser("subagent", help="create a subagent (separate thread or in-place)")
    sa.add_argument("id")
    sa.add_argument("--profile", default="worker", choices=["worker", "scout", "reviewer"])
    sa.add_argument("--role")
    sa.add_argument("--label")
    sa.add_argument("--agent")
    sa.add_argument("--prompt")
    sa.add_argument("--in-place", action="store_true", help="same worktree, split pane")
    sa.add_argument("--name")
    sa.add_argument("--list", action="store_true")
    sa.add_argument("--json", action="store_true")
    sa.add_argument("--focus", action="store_true")
    sa.set_defaults(func=cmd_subagent)

    jn = sub.add_parser("join", help="collect a subagent result and send it to the parent")
    jn.add_argument("parent")
    jn.add_argument("sub")
    jn.add_argument("--lines", type=int, default=120)
    jn.add_argument("--timeout", type=int, default=600000)
    jn.add_argument("--no-send", action="store_true")
    jn.set_defaults(func=cmd_join)

    rn = sub.add_parser("runner", help="remote/cloud runner lifecycle")
    rn.add_argument("action", choices=["list", "add", "up", "down", "bootstrap", "forward", "unforward"])
    rn.add_argument("name", nargs="?")
    rn.add_argument("--host")
    rn.add_argument("--repo")
    rn.add_argument("--path")
    rn.add_argument("--local", type=int, default=8080)
    rn.add_argument("--remote", type=int, default=3000)
    rn.set_defaults(func=cmd_runner)

    sh = sub.add_parser("share", help="print remote attach / collaboration instructions")
    sh.add_argument("id")
    sh.add_argument("--host")
    sh.add_argument("--write", action="store_true")
    sh.set_defaults(func=cmd_share)

    m = sub.add_parser("machines", help="list saved herdr SSH machines")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_machines)

    rm = sub.add_parser("remote", help="pass a command to a saved herdr machine")
    rm.add_argument("machine")
    rm.add_argument("rest", nargs=argparse.REMAINDER,
                    help="e.g. agent list | agent prompt NAME TEXT | agent read NAME")
    rm.set_defaults(func=cmd_remote)

    nt = sub.add_parser("notify", help="desktop notification")
    nt.add_argument("title")
    nt.add_argument("--body", default="")
    nt.add_argument("--sound", default="none")
    nt.set_defaults(func=cmd_notify)

    pn = sub.add_parser("prune", help="find/clean orphaned threads, worktrees, workspaces")
    pn.add_argument("--threads", action="store_true", help="mark missing-worktree threads orphaned")
    pn.add_argument("--worktrees", action="store_true", help="run git worktree prune")
    pn.add_argument("--workspaces", action="store_true", help="close workspaces whose checkout is gone")
    pn.add_argument("--dry-run", action="store_true")
    pn.add_argument("--yes", action="store_true")
    pn.set_defaults(func=cmd_prune)

    bk = sub.add_parser("backup", help="back up the thread database")
    bk.set_defaults(func=cmd_backup)

    d = sub.add_parser("doctor", help="check prerequisites")
    d.set_defaults(func=cmd_doctor)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except RuntimeError as e:
        die(str(e))
    except KeyboardInterrupt:
        die("interrupted", 130)


if __name__ == "__main__":
    main()
