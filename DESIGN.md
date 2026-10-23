> English · [中文](DESIGN.zh-CN.md)

# herdr-change: rebuilding Delta's good parts on herdr

Goal: drop the Delta GUI and cover the workflows that were actually valuable in Delta with
**native herdr features + the reviewr plugin + one thin orchestration layer, `herdr-change`**.

Legend: ✅ provided by herdr · 🟡 provided by this project (MVP) · 🔵 scriptable/plugin work · ❌ explicitly out of scope

---

## 1. Delta feature list → herdr equivalent

### A. Unit of work and isolation

| # | Delta feature | Why it's good | herdr equivalent | Status |
|---|---|---|---|---|
| A1 | Change | Request, conversation, diff, and review live in one context | a `herdr-change` change record = workspace + agent + worktree + state | 🟡 |
| A2 | Delta worktree | one checkout per task, no cross-contamination | `herdr worktree create/list/open/remove`, workspace bound to the worktree | ✅ |
| A3 | Local checkout / attach | bring an existing directory into the workflow without publishing | `herdr worktree open` / `workspace create --cwd` | ✅ |
| A4 | Remote is mandatory | guarantees fetch/push works | not enforced by herdr; checked at `herdr-change land --push/--pr` | 🟡 |
| A5 | Restore/archive/tag/bookmark | many tasks without losing context | change DB `status/tags/bookmarked/archived`; workspace labels | 🟡 |
| A6 | Fork / child changes | try a second approach from the current diff | `herdr-change fork`: child worktree + agent from the parent's HEAD | 🟡 |
| A7 | Multi-repo / submodules | one task across repos | one repo per change record; use several changes or repeat `--repo` | 🔵 |

### B. Agents and parallelism

| # | Delta feature | Why it's good | herdr equivalent | Status |
|---|---|---|---|---|
| B1 | Parallel agents | one agent per task | `herdr agent start` in each workspace pane | ✅ |
| B2 | Sub-agents (Worker/Scout/Reviewer) | isolated subtasks, different models | `herdr-change subagent --profile worker/scout/reviewer`, own worktree + prompt | 🟡 |
| B3 | Agent state/read/wait | automation | `herdr agent list/get/read/wait/prompt/send-keys/focus` | ✅ |
| B4 | Model selection | different models per task | agent launch args/env; `herdr-change` injects profile config | 🔵 |
| B5 | Session restore | close it, come back, keep going | the agent's own session (codex/claude/pi) + herdr's persistent session | ✅ |

### C. Review and merge (Delta's core selling point)

| # | Delta feature | Why it's good | herdr equivalent | Status |
|---|---|---|---|---|
| C1 | Review change | implementation separated from review | `herdr-change review`: reviewr + optional reviewer agent + review prompt | 🟡 |
| C2 | Diff panel | see what the agent changed | **reviewr**: uncommitted/branch/last-turn/commits scopes | ✅ |
| C3 | Inline comments back to the agent | comments become agent input | **reviewr** line comments → send to agent | ✅ |
| C4 | Change guide | an explained diff | `review` generates a reviewer prompt asking for file/risk/verification walkthrough | 🟡 |
| C5 | Verdict | approve / reject | change DB `review_state` + `herdr-change review --verdict` | 🟡 |
| C6 | Land changes | one-key merge/push | `herdr-change land --push` / `--pr`; `--merge` uses `--into` | 🟡 |
| C7 | Merge selected worktrees | merge several changes | `land` one by one in dependency order; batch script later | 🔵 |
| C8 | Check remote / bring changes in | sync upstream | `git fetch` + `rebase` wrapped in `herdr-change sync --rebase` | 🟡 |
| C9 | PR view | see the PR without leaving the terminal | reviewr's built-in PR tab (gh/glab/az) | ✅ |

### D. Terminals

| # | Delta feature | Equivalent | Status |
|---|---|---|---|
| D1 | Interactive terminal | herdr pane / tab | ✅ |
| D2 | Background terminal | separate tab/pane, `herdr pane run` | ✅ |
| D3 | Agent-run commands | the agent's own terminal tool | ✅ |
| D4 | Terminal inside a shared change | herdr remote attach to the same session | 🟡 |

### E. Rules / project setup

| # | Delta feature | Equivalent | Status |
|---|---|---|---|
| E5 | `.agents/prepare` / setup | `new --run-hooks` or `setup <id>` | 🟡 |
| E6 | Project rules | `.agents/` + the agent's own rule files | ✅ |

### F. Providers / models

| # | Delta feature | Equivalent | Status |
|---|---|---|---|
| F1 | Custom providers | configured in codex/claude/pi; not managed here | ✅ |
| F2 | Model roles / fallback | profile `model` + launch args; fallback delegated to the agent | 🔵 |
| F3 | Context window / reasoning effort | agent config | ✅ |

### G. Collaboration

| # | Delta feature | Equivalent | Status |
|---|---|---|---|
| G1 | Shared change / real-time collaboration | `herdr --remote <ssh>` lets several people attach to one session | 🟡 |
| G2 | Comments/chat/mentions | reviewr comments + chat in the pane; no CRDT rich text | 🔵 |
| G3 | Permissions/invites/who pays | SSH account permissions; everyone pays for their own model usage | 🔵 |
| G4 | Web app / Inbox | not built; you can expose herdr with ttyd (security risk) | ❌ |
| G5 | Notifications | `herdr notification show --title ... --body ...` | ✅ |

### H. Cloud runners

| # | Delta feature | Equivalent | Status |
|---|---|---|---|
| H1 | Ephemeral cloud Linux runner | `herdr machine` / `--remote` to a fixed VM; ephemeral runners via scripts | 🔵 |
| H2 | `.agents/setup` / secrets | done in the remote shell/profile | 🔵 |
| H3 | Preview URL | remote service + port forwarding | 🔵 |
| H4 | Git push from the runner | the remote machine's git/gh credentials | 🔵 |

### I. UX

| # | Delta feature | Equivalent | Status |
|---|---|---|---|
| I1 | Command palette | built into herdr | ✅ |
| I2 | Keybindings | `~/.config/herdr/config.toml` | ✅ |
| I3 | Search / file view | reviewr search + file viewer; `rg` | ✅ |
| I4 | Attachments / mention files | supported by the agent itself (`@file`) | ✅ |
| I5 | Edit / revert messages | the agent session itself; code rollback via `herdr-change checkpoint` + `git reset` | 🟡 |
| I6 | Bookmarks / tags / filtering | change DB | 🟡 |
| I7 | Settings page | `~/.config/herdr-change/config.toml` | 🟡 |

### J. CLI / automation

| # | Delta CLI | Equivalent | Status |
|---|---|---|---|
| J1 | `delta open` | `herdr-change open <id>` / `herdr workspace focus` | 🟡 |
| J2 | `delta change` | `herdr-change list/show` | 🟡 |
| J3 | `delta worktree` | `herdr worktree *` + `herdr-change new/cleanup` | ✅ |
| J4 | `delta auth` | each agent's own login | ✅ |
| J6 | `delta integration` | herdr `integration` | ✅ |

---

## 2. herdr-change architecture

```
┌────────────────────────────────────────────┐
│ herdr-change (Python CLI)                  │
│  new/list/show/prompt/review/land/...      │
│  SQLite: ~/.local/share/herdr-change/changes.db │
└───────────────┬────────────────────────────┘
                │ herdr CLI (JSON)
┌───────────────▼────────────────────────────┐
│ herdr server                               │
│  workspace ↔ worktree                      │
│  pane: shell / agent / reviewr             │
│  agent: codex/claude/pi/...                │
└────────────────────────────────────────────┘
```

- **one change = one workspace + one worktree + one agent pane + one reviewr pane**.
- state never copies the agent's conversation; only orchestration metadata is stored, the
  conversation stays in the agent's own session.
- all writes go through the herdr CLI / git; herdr internals are never touched directly.

### Tables

```sql
changes(
  id, parent_id, label, repo, worktree, branch, base_ref,
  workspace_id, pane_id, review_pane_id,
  agent_name, agent_kind, status, review_state,
  tags, bookmarked, archived, created_at, head_sha, landed_at, meta
)
events(id, change_id, at, kind, detail)
```

### Commands

```
new        create worktree + workspace + agent + reviewr
list       status overview (agent state + dirty + ahead/behind + review)
show       details for one change
prompt     send a prompt to that change's agent
review     focus + open reviewr (optional reviewer agent / record verdict)
checkpoint git-commit the current diff (a rollback point)
land       push / PR / merge
sync       fetch + refresh state; --rebase pulls upstream
fork       child change from the current HEAD
subagent   worker/scout/reviewer child change
open       focus the workspace
tag        tag/bookmark/archive
cleanup    remove the worktree + close the workspace
```

### Config `~/.config/herdr-change/config.toml`

```toml
worktree_root = "~/.herdr-worktrees"
default_agent = "codex"
review_plugin = "persiyanov.reviewr"
land_strategy = "push"        # push | pr | merge
notify = true

[profiles.worker]
kind = "codex"
args = []
prompt = "You are an implementation sub-agent. Do only the assigned subtask and summarise the changes and verification when done."

[profiles.scout]
kind = "codex"
args = []
prompt = "You are a research sub-agent. Read code only, report conclusions, change nothing."

[profiles.reviewer]
kind = "codex"
args = []
prompt = "You are a review sub-agent. Read the diff only; list risks, missing tests and rollback advice per file."
```

---

## 3. Phases

- **P0 (done)**: `new/list/show/diff/prompt/review/checkpoint/land/cleanup/open` + DB + config.
- **P1 (done)**: `fork/subagent/sync --rebase/tag/archive`, state polling + notifications,
  `.agents/prepare|setup` hooks.
- **P2 (done)**: persisted review verdicts, `review --reviewer`, batch land, PR/CI polling.
- **P3 (done)**: `machines/remote/notify` passthrough, runner lifecycle/port forwarding, share,
  RUNNER.md; the hosted platform itself is out of scope.

---

## 4. Explicit trade-offs

- **Not copied**: DeltaDB/CRDT, per-turn incremental snapshots, the web app, rich-text
  multi-user collaboration, and the hosted ephemeral-runner experience.
- **Kept**: git as the single source of truth; the agent's own session config; reviewr's
  review experience.
- **Advantages**: native WSL, no GUI/DPI/cursor/portal problems; scriptable; swapping agents
  does not change the workflow.

---

## 5. Implementation status (v0.1, 2026-10)

Shipped commands:

```
new  list  show  setup  transcript  turn  search  capture  prompt  review
comment  attach  send  checkpoint  land  pr  cleanup  open  diff  tag  sync
recover  watch  fork  subagent  join  machines  remote  runner  share  notify
backup  prune  doctor
```

Verified:

- dummy repo: `new → reviewr → agent → prompt → sync → checkpoint → review --reviewer → land merge → cleanup`;
- batch land: `land --all --strategy push --yes --cleanup` pushed both branches to the bare remote;
- PR/CI: `land pr → sync --pr → MERGED` under a fake `gh`, with `pr_state/ci_state` migrating correctly;
- `land --strategy hook` environment (CHANGE_ID/BRANCH/WORKTREE) verified;
- `.agents/prepare` + `.agents/setup` run inside the new worktree;
- `QP-GD26-0901`: after `new --no-agent` create/cleanup, `git worktree list` has no leftovers.

Known boundaries:

- multi-repo changes: the primary repo gets the herdr workspace, other repos use
  `git worktree add`; the agent lives in the primary repo by default.
- remote change DBs live on the remote machine; local `remote` only passes commands through;
  runners are a scripted flow.
- reviewr is patched: comment changes are written to `~/.cache/herdr-reviewr/*.json`, and
  `comment --from-reviewr` / `review --sync-reviewr` write them back to SQLite.
- no automatic per-turn snapshots; `checkpoint` creates an explicit commit as a rollback point.
- turn edit/revert only changes metadata, it cannot rewrite context the agent already received.

---

## 6. v0.2 additions (2026-10)

New:

- safety gates: dirty / rejected / require_approval / pre-land script and commands;
  `land --allow-dirty/--force/--skip-checks`
- PR loop: `merge-pr`, auto-merge, `pr --watch`, `watch --daemon/--status/--stop`, CI rollup
- recovery: `checkpoint --list/--restore`, `recover --create/--start/--prune`, `prune`
- multi-repo: `change_repos`, per-repo PR/CI, checkpoint/diff/land/cleanup across all repos
- records: turns / comments / attachments / search / bookmark / fork context injection / send
- sub-agents: `subagent --in-place` + `join`; standalone child changes remain
- models: `roles.*`, `--role`, `fallback`
- environments: `runner add/up/down/bootstrap/forward/unforward`, `share`
- engineering: `backup`, `prune`, `HERDR_CHANGE_DATA/CONFIG`, `pyproject.toml`, `tests/smoke.sh`

Still out of scope: DeltaDB/CRDT, web app, rich-text collaboration, hosted cloud runner.

---

## 7. v0.3 additions (2026-10)

- change lifecycle: `children/resume/rename/delete/restore-change`, `mention/openfile`
- conversation editing: `turn --edit/--revert --reseed` rewrites the canonical context and
  realigns the agent
- review: `guide`, `share --review` HTML, `check-remote`
- land: `depend` + topological order, `local` strategy, `--skip-deps`
- sync: both `--rebase` and `--merge` fetch first
- sub-agents: `follow` to focus/read
- terminals: change-scoped list/new/run/close
- runners: env/push-env/preview/urls/snapshot/destroy
- web/inbox: `serve` local dashboard, POST comments/notes, token/TLS
- CLI: `config` (including nested keys), `delta` aliases, `restore-db`
- engineering: unit tests + GitHub Actions, SQLite `user_version=3`

Local equivalents: web/CRDT/hosted runner are replaced by `serve` + single-writer SQLite,
review HTML, and SSH scripts.

## 8. v0.4 wrap-up (2026-10)

- `serve` supports POST for comments/notes + token auth; `--daemon/--stop/--status`
- `comment --import/--export/--from-clipboard`, `comment --send --to` across changes
- `land --json` emits pure JSON (logs in the `log` field)
- `config set`, `delta auth login/logout`, `usage`, `restore-db`
- file lock `write.lock` for write commands
- `fork --clone-session` forks the pi/codex session; multi-repo fork/subagent clone extra
  repos; `HERDR_CHANGE_REPOS` injected into the agent env; `repos` command
- remote mirror: `mirror <machine>` / `remote-changes`
- PRs: GitHub/GitLab/Azure creation and state reads
- project renamed `herdr-thread` → `herdr-change`: tables/columns, env vars, paths, plugin id,
  plus an automatic one-time migration of the old config dir, database, and schema
- SQLite `user_version=4`; unit + smoke + GitHub Actions

## 9. emb-agent bridge (L1)

- `emb status`: read-only active task / gate / journal
- `emb adopt`: create/select a worktree via emb, then take it over with `herdr worktree open`
- change records carry `emb_task`; shown in `list/show`
- `cleanup` calls `emb-agent task worktree cleanup` first
- the `prefix+t` plugin adopts automatically in emb projects; CLI `new/task` refuses by default
- `prefix+alt+d` adds `--emb-finish` in emb projects
- `land` runs the `emb-agent start --json` gate first; `--skip-emb-check` overrides
- `--emb-finish` calls `emb-agent finish-work` after landing to write the journal back
- TODO: generate emb verification automatically (emb's own flow does it today)
