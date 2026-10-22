#!/usr/bin/env python3
"""herdr-thread: Delta-like thread/worktree/review orchestration on top of herdr."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = HOME / ".config" / "herdr-thread" / "config.toml"
DATA_DIR = HOME / ".local" / "share" / "herdr-thread"
DB_PATH = DATA_DIR / "threads.db"

DEFAULT_CONFIG = {
    "worktree_root": str(HOME / ".herdr-worktrees"),
    "default_agent": "codex",
    "review_plugin": "persiyanov.reviewr",
    "review_entrypoint": "pane",
    "land_strategy": "push",
    "notify": True,
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
    for col in ("reviewer_pane_id", "reviewer_agent"):
        if col not in cols:
            conn.execute(f"ALTER TABLE threads ADD COLUMN {col} TEXT")
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

def cmd_new(args):
    cfg = load_config()
    conn = db()
    repo = str(Path(args.repo).resolve())
    if not is_git_repo(repo):
        die(f"not a git repository: {repo}")
    if run(["git", "-C", repo, "rev-parse", "--verify", "HEAD"], check=False).returncode != 0:
        die(f"repository has no commits yet: {repo}")

    tid = new_id()
    slug = slugify(args.label)
    base_ref = args.base or "HEAD"
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

    profile = cfg["profiles"].get(args.profile, {}) if getattr(args, "profile", None) else {}
    agent_name = None
    agent_kind = None
    agent_args: list[str] = []
    if not args.no_agent:
        agent_kind = args.agent or profile.get("kind") or cfg["default_agent"]
        agent_args = list(profile.get("args") or [])
        agent_name = f"ht-{tid}"
        start_res = start_agent(agent_name, agent_kind, pane_id, workspace_id, wt, agent_args)
        if start_res:
            agent_name = start_res

    conn.execute(
        """INSERT INTO threads
           (id,parent_id,label,repo,worktree,branch,base_ref,workspace_id,pane_id,review_pane_id,
            agent_name,agent_kind,status,review_state,tags,bookmarked,archived,created_at,head_sha,meta)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            tid, args.parent, args.label, repo, wt, worktree.get("branch", branch), base_ref,
            workspace_id, pane_id, review_pane, agent_name, agent_kind,
            "working" if agent_name else "created", "", args.tag or "", 0, 0,
            now(), git(wt, "rev-parse", "--short", "HEAD", check=False), "{}",
        ),
    )
    conn.commit()
    event(conn, tid, "created", f"workspace={workspace_id} branch={branch}")

    print(f"thread {tid}: {args.label}")
    print(f"  repo      {repo}")
    print(f"  worktree  {wt}")
    print(f"  branch    {worktree.get('branch', branch)}")
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
        print("  prompt    sent")
    if getattr(args, "run_hooks", False):
        run_hooks(wt, conn, tid)
    if args.focus:
        herdr("workspace", "focus", workspace_id)
    return tid


def start_agent(name: str, kind: str, pane_id: str, workspace_id: str, cwd: str,
                agent_args: list[str] | None = None) -> str | None:
    last = ""
    extra = list(agent_args or [])
    for attempt in range(3):
        try:
            cmd = ["herdr", "agent", "start", name, "--kind", kind, "--pane", pane_id, "--timeout", "60000"]
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
        print(json.dumps({**dict(r), "git": info, "agent": st}, ensure_ascii=False, indent=2))
        return
    print(f"thread {r['id']}  {r['label']}")
    print(f"  status      {r['status']}  review={r['review_state'] or '-'}  agent={st.get('agent_status','-')}")
    print(f"  repo        {r['repo']}")
    print(f"  worktree    {r['worktree']}")
    print(f"  branch      {r['branch']}  base={r['base_ref']}")
    print(f"  workspace   {r['workspace_id']}  agent_pane={r['pane_id']}  reviewr={r['review_pane_id'] or '-'}")
    if r["reviewer_agent"]:
        print(f"  reviewer    {r['reviewer_agent']} ({r['reviewer_pane_id'] or '-'})")
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
        profile = cfg["profiles"].get(args.profile or "reviewer", {})
        kind = args.agent or profile.get("kind") or cfg["default_agent"]
        try:
            split = herdr("pane", "split", r["pane_id"], "--direction", "down",
                          "--cwd", r["worktree"], "--no-focus")
            rp = split.get("pane", {}).get("pane_id")
            if not rp:
                raise RuntimeError(f"unexpected split result: {split}")
            reviewer_agent = f"ht-{r['id']}-rev"
            start_agent(reviewer_agent, kind, rp, ws, r["worktree"], list(profile.get("args") or []))
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
    wt = r["worktree"]
    dirty = git(wt, "status", "--porcelain", check=False)
    if not dirty.strip():
        print("nothing to checkpoint")
        return
    msg = args.message or f"checkpoint: {r['label']}"
    run(["git", "-C", wt, "add", "-A"])
    run(["git", "-C", wt, "commit", "-m", msg])
    head = git(wt, "rev-parse", "--short", "HEAD", check=False)
    conn.execute("UPDATE threads SET head_sha=?, status='review' WHERE id=?", (head, r["id"]))
    conn.commit()
    event(conn, r["id"], "checkpoint", f"{head} {msg}")
    print(f"checkpoint {head}  {msg}")


def cmd_land(args):
    cfg = load_config()
    conn = db()
    r = thread_row(conn, args.id)
    if r["status"] == "landed":
        die(f"thread {r['id']} already landed")
    wt = r["worktree"]
    strategy = args.strategy or cfg["land_strategy"]
    branch = r["branch"]

    if strategy == "push":
        origin = git(wt, "remote", "get-url", "origin", check=False)
        if not origin:
            die("no origin remote. Add one (`git remote add origin <url>`) or use --strategy merge")
        p = run(["git", "-C", wt, "push", "-u", "origin", branch], check=False)
        if p.returncode != 0:
            die(f"push failed: {p.stderr.strip() or p.stdout.strip()}")
        print(f"pushed {branch} -> {origin}")
    elif strategy == "pr":
        origin = git(wt, "remote", "get-url", "origin", check=False)
        if not origin:
            die("no origin remote; cannot create PR")
        title = args.title or r["label"]
        body = args.body or f"Thread {r['id']} managed by herdr-thread."
        p = run(["gh", "pr", "create", "--head", branch, "--title", title, "--body", body], cwd=wt, check=False)
        if p.returncode != 0:
            die(f"gh pr create failed: {p.stderr.strip() or p.stdout.strip()}")
        print(p.stdout.strip())
    elif strategy == "merge":
        into = args.into
        if not into:
            die("--strategy merge requires --into <branch>")
        if git(r["repo"], "status", "--porcelain", check=False).strip():
            die("source repo has uncommitted changes; refusing to merge")
        run(["git", "-C", r["repo"], "checkout", into])
        p = run(["git", "-C", r["repo"], "merge", "--no-ff", branch], check=False)
        if p.returncode != 0:
            die(f"merge failed: {p.stderr.strip() or p.stdout.strip()}")
        print(f"merged {branch} into {into}")
    elif strategy == "hook":
        hook = HOME / ".config" / "herdr-thread" / "land.sh"
        if not hook.exists():
            die(f"land hook not found: {hook}")
        env = os.environ.copy()
        env.update({
            "THREAD_ID": r["id"], "THREAD_LABEL": r["label"],
            "REPO": r["repo"], "WORKTREE": wt, "BRANCH": branch,
        })
        p = subprocess.run([str(hook)], cwd=wt, env=env)
        if p.returncode != 0:
            die(f"land hook failed ({p.returncode})")
        print(f"land hook completed: {hook}")
    else:
        die(f"unknown land strategy: {strategy}")

    conn.execute("UPDATE threads SET status='landed', landed_at=? WHERE id=?", (now(), r["id"]))
    conn.commit()
    event(conn, r["id"], "landed", strategy)
    if cfg.get("notify", True):
        try:
            herdr("notification", "show", f"Landed: {r['label']}", "--body", f"{branch} ({strategy})", "--sound", "done")
        except Exception:
            pass
    if args.cleanup:
        cleanup_thread(conn, r, force=True)


def cleanup_thread(conn, r, force=False):
    ws = r["workspace_id"]
    ok = False
    if ws:
        try:
            herdr("worktree", "remove", "--workspace", ws, "--force")
            ok = True
        except Exception as e:
            print(f"warning: herdr worktree remove failed: {e}", file=sys.stderr)
    if not ok:
        p = run(["git", "-C", r["repo"], "worktree", "remove", "--force", r["worktree"]], check=False)
        ok = p.returncode == 0
    conn.execute("UPDATE threads SET status='cleaned', archived=1 WHERE id=?", (r["id"],))
    conn.commit()
    event(conn, r["id"], "cleaned", "worktree removed" if ok else "worktree remove failed")
    print(f"cleaned {r['id']}  {'(worktree removed)' if ok else '(worktree may remain)'}")


def cmd_cleanup(args):
    conn = db()
    r = thread_row(conn, args.id)
    cleanup_thread(conn, r, force=args.force)


def cmd_diff(args):
    conn = db()
    r = thread_row(conn, args.id)
    wt = r["worktree"]
    base = r["base_ref"] or "HEAD"
    print(f"# thread {r['id']} {r['label']}  ({r['branch']})")
    print(f"# worktree {wt}  base {base}\n")
    committed = git(wt, "diff", f"{base}..HEAD", check=False) if base != "HEAD" else git(wt, "diff", "HEAD", check=False)
    if committed:
        print("## committed (base..HEAD)\n" + committed)
    unstaged = git(wt, "diff", check=False)
    if unstaged:
        print("\n## uncommitted\n" + unstaged)
    if not committed and not unstaged:
        print("(no changes)")


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
    rows = conn.execute("SELECT * FROM threads WHERE archived=0").fetchall()
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
            p = run(["git", "-C", r["worktree"], "rebase", "--autostash", "@{u}"], check=False)
            if p.returncode != 0:
                print(f"{r['id']}: rebase failed: {p.stderr.strip()[:200]}", file=sys.stderr)
        if new_status != r["status"]:
            conn.execute("UPDATE threads SET status=? WHERE id=?", (new_status, r["id"]))
            event(conn, r["id"], "status", f"{r['status']} -> {new_status}")
            if cfg.get("notify", True) and new_status == "review":
                try:
                    herdr("notification", "show", f"Review ready: {r['label']}",
                          "--body", f"{r['branch']} · dirty={info['dirty']}", "--sound", "request")
                except Exception:
                    pass
        if args.json:
            pass
    conn.commit()
    print(f"synced {len(rows)} threads")


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
    print(f"forked {parent['id']} -> {tid}")


def cmd_subagent(args):
    cfg = load_config()
    conn = db()
    parent = thread_row(conn, args.id)
    profile = cfg["profiles"].get(args.profile, {})
    base = git(parent["worktree"], "rev-parse", "HEAD", check=False)
    prompt = args.prompt or profile.get("prompt") or f"Subagent profile: {args.profile}"
    ns = argparse.Namespace(
        label=args.label or f"{parent['label']} · {args.profile}",
        repo=parent["repo"], base=base, branch=None, worktree=None,
        agent=args.agent or profile.get("kind") or parent["agent_kind"],
        no_agent=False, prompt=prompt, focus=args.focus, tag=f"subagent,{args.profile}",
        parent=parent["id"], trust_repository=False, profile=args.profile, run_hooks=False,
    )
    tid = cmd_new(ns)
    print(f"subagent {args.profile}: {parent['id']} -> {tid}")


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


# ---------- main ----------

def build_parser():
    p = argparse.ArgumentParser(prog="herdr-thread", description="Delta-like threads on herdr")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create a thread (worktree + workspace + agent)")
    n.add_argument("label")
    n.add_argument("--repo", default=".")
    n.add_argument("--base")
    n.add_argument("--branch")
    n.add_argument("--worktree")
    n.add_argument("--agent", help="agent kind (codex/claude/pi/...)")
    n.add_argument("--profile", help="profile name from config.toml")
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

    rv = sub.add_parser("review", help="focus thread and open reviewr")
    rv.add_argument("id")
    rv.add_argument("--verdict", choices=["approved", "rejected"])
    rv.add_argument("--reviewer", action="store_true", help="start a reviewer sub-agent in a split pane")
    rv.add_argument("--profile", default="reviewer")
    rv.add_argument("--agent")
    rv.add_argument("--reviewer-prompt")
    rv.set_defaults(func=cmd_review)

    c = sub.add_parser("checkpoint", help="commit current worktree changes")
    c.add_argument("id")
    c.add_argument("-m", "--message")
    c.set_defaults(func=cmd_checkpoint)

    ld = sub.add_parser("land", help="push / open PR / merge")
    ld.add_argument("id")
    ld.add_argument("--strategy", choices=["push", "pr", "merge", "hook"])
    ld.add_argument("--into")
    ld.add_argument("--title")
    ld.add_argument("--body")
    ld.add_argument("--cleanup", action="store_true")
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

    sy = sub.add_parser("sync", help="refresh thread states, optionally rebase")
    sy.add_argument("--rebase", action="store_true")
    sy.add_argument("--json", action="store_true")
    sy.set_defaults(func=cmd_sync)

    f = sub.add_parser("fork", help="fork a thread from its current HEAD")
    f.add_argument("id")
    f.add_argument("label")
    f.add_argument("--agent")
    f.add_argument("--no-agent", action="store_true")
    f.add_argument("--prompt")
    f.add_argument("--focus", action="store_true")
    f.set_defaults(func=cmd_fork)

    sa = sub.add_parser("subagent", help="create a worker/scout/reviewer sub-thread")
    sa.add_argument("id")
    sa.add_argument("--profile", default="worker", choices=["worker", "scout", "reviewer"])
    sa.add_argument("--label")
    sa.add_argument("--agent")
    sa.add_argument("--prompt")
    sa.add_argument("--focus", action="store_true")
    sa.set_defaults(func=cmd_subagent)

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
