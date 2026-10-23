> English · [中文](README.zh-CN.md)

# herdr-change

A Delta-style workflow — **change + worktree + agent + review + land** — built on
[herdr](https://herdr.dev).

No Delta GUI, and no poking at herdr internals: everything goes through the herdr CLI,
`git`, and a single SQLite file.

- Design notes and the Delta feature comparison: [DESIGN.md](DESIGN.md)
- Remote / ephemeral runners: [RUNNER.md](RUNNER.md)
- State database: `~/.local/share/herdr-change/changes.db`
- Config: `~/.config/herdr-change/config.toml`

## Why "change"

One change is the unit that starts as a request and ends up landed: the prompt, one or more
git worktrees, the agent pane, the review pane, the comments, and the PR. The word is
borrowed from Gerrit, where a change is exactly "the thing you review and submit", which is
also why the commands read naturally: `herdr-change review`, `herdr-change land`.

## Requirements

- `herdr >= 0.7.5` with the server running
- the `reviewr` plugin (`herdr plugin install persiyanov/herdr-reviewr`)
- `git`
- optional: `gh` (PRs / CI), `glab`, `az`

The CLI is a single Python file (`herdr_change.py`, 3.11+). Put it on `$PATH`:

```bash
install -m 755 herdr_change.py ~/.local/bin/herdr-change
```

## Herdr keybindings (recommended)

```text
prefix+t        new task (plugin popup, title "new task")
prefix+alt+r    review the change for the current worktree
prefix+alt+d    finish the current change (plugin popup, title "finish change")
prefix+alt+c    close the current task: window only / clean up the worktree
```

Install or repair them:

```bash
herdr-change install-keys
herdr server reload-config
```

## The three commands you actually need

```bash
herdr-change task "switch IRQ sampling to forced-read"  # worktree + agent + task + focus
herdr-change review <id>                                # diff / reviewr comments
herdr-change done <id>                                  # checkpoint + push
# or: herdr-change done <id> --pr                       # checkpoint + push + open PR
```

Everything else is optional.

## Full usage

```bash
# 1. create a change: worktree + workspace + agent + reviewr
herdr-change task "switch IRQ sampling to forced-read" --repo ~/emb/workspace/QP/QP-GD26-0901 --agent codex

# multi-repo change (one change, several worktrees)
herdr-change new "cross-repo change" --repo ~/proj/fw --repo ~/proj/host --no-agent

# 2. state
herdr-change list
herdr-change show <id>
herdr-change diff <id>

# 3. prompt the agent / capture replies
herdr-change prompt <id> "add unit tests" --wait
herdr-change capture <id>          # store current agent output as a turn
herdr-change transcript <id>       # browse/search the change's turns

# 4. review
herdr-change review <id> --reviewer --agent codex
herdr-change comment <id> "null-check this" --file main.c --line 42
herdr-change comment <id> --send
herdr-change review <id> --verdict approved

# 5. checkpoint / roll back
herdr-change checkpoint <id> -m "wip: IRQ"
herdr-change checkpoint <id> --list
herdr-change checkpoint <id> --restore latest

# 6. land
herdr-change land <id> --strategy push
herdr-change land <id> --strategy pr
herdr-change land <id> --strategy merge --into main
herdr-change land <id> --strategy merge-pr --method squash
herdr-change land <id> --strategy hook

# batch
herdr-change land --all --strategy push --dry-run
herdr-change land --all --strategy push --yes --cleanup

# 7. PR / CI
herdr-change pr <id> --watch
herdr-change sync --pr
herdr-change watch --daemon --interval 60
herdr-change watch --status
herdr-change watch --stop

# 8. lifecycle / review bundle / local web
herdr-change children <id>
herdr-change resume <id>
herdr-change rename <id> "new label"
herdr-change delete <id> --keep-worktree
herdr-change guide <id>                 # review guide markdown
herdr-change share <id> --review        # standalone review HTML
herdr-change serve --daemon --token secret              # http://127.0.0.1:8765
herdr-change serve --tls-cert cert.pem --tls-key key.pem # https

# 9. clean up / recover
herdr-change cleanup <id>
herdr-change recover --create      # rebind after a herdr restart or lost workspace
herdr-change prune --workspaces --dry-run
```

## What one change is

```
change
├── 1..N git worktrees (multi-repo)
├── herdr workspace (primary repo)
├── agent pane
├── reviewr pane
├── checkpoints / turns / comments / attachments (SQLite)
└── PR / CI state (change_repos)
```

The conversation itself stays in the agent's own session. `herdr-change` stores only
searchable turn summaries, review comments, and orchestration metadata.

## Commands

| Command | Purpose |
|---|---|
| `new` | create worktree/workspace/agent/reviewr; repeatable `--repo`; `--profile/--role/--run-hooks` |
| `list` | status overview; `--tag --search --bookmarked --all --json` |
| `show` / `repos` | change details / multi-repo paths; `--json` |
| `diff` | `base..HEAD` plus uncommitted diff for every repo |
| `setup` | run `.agents/prepare` / `.agents/setup` in the worktree |
| `prompt` | send a prompt to the change's agent; `--wait` |
| `capture` | read agent output into turns |
| `transcript` | browse turns; `--search --bookmarked` |
| `turn` | `--add/--list/--bookmark/--edit/--revert/--reseed` |
| `search` | search turns across changes |
| `review` | open reviewr; `--reviewer` starts a read-only review agent; `--verdict`; `--sync-reviewr` |
| `comment` | add / `--list` / `--send [--to]` / `--resolve` / `--import` / `--export` / `--from-clipboard` / `--from-reviewr` |
| `attach` | copy a file or `--text` into `<worktree>/.herdr-change/attachments` |
| `send` | send text/transcript to another change's agent |
| `children` | child-change tree |
| `resume` | rebind/focus a change, starting its agent if needed |
| `rename` / `delete` / `restore-change` | change lifecycle |
| `mention` / `openfile` | attach a file and prompt the agent / resolve a file in the worktree |
| `guide` | generate a review guide (commits/files/reviewer notes) |
| `share --review` | generate a standalone review HTML bundle |
| `depend` | change dependencies; `land` checks and topologically sorts them |
| `check-remote` | compare the local branch with origin |
| `subagent follow` | `follow <parent> <sub>` focus and read a sub-agent |
| `terminal` | change-scoped terminals: list/new/run/close |
| `serve` | local web dashboard / inbox; POST comments/notes; `--token` / `--tls-cert` / `--tls-key`; `user` manages tokens |
| `config` | show/path/set, including nested keys such as `roles.foo.kind` |
| `delta` | aliases: open/change/worktree/auth(login/logout/status)/integration |
| `restore-db` | restore `changes.db` from a backup |
| `checkpoint` | commit / `--list` / `--restore`, recorded per repo |
| `land` | push/pr/merge/merge-pr/local/hook; batch; dirty/verdict/pre-land/deps/emb gates; `--emb-finish` |
| `pr` | inspect / `--watch` PR + CI; GitHub/GitLab/Azure (needs gh/glab/az) |
| `sync` | refresh agent state; `--rebase` (fetches first); `--pr` |
| `watch` | background polling; `--daemon/--status/--stop` |
| `fork` | fork a change from its current HEAD; `--with-context/--from-turn` inject the transcript; `--clone-session` forks the pi/codex session |
| `subagent` | separate change, or `--in-place` sub-agent in the same worktree; `--list` |
| `join` | collect a sub-agent's output and hand it back to the parent |
| `open` | focus the workspace |
| `tag` | tag / bookmark / archive |
| `cleanup` / `close-here` | remove all worktrees and the workspace; `--window` closes the window only |
| `recover` | rebind workspace/pane/agent; `--create/--start/--prune` |
| `emb` | emb-agent project bridge: `status` / `adopt` |
| `adopt` | take over an existing worktree (creates nothing) |
| `machines` | list saved herdr SSH machines |
| `remote` | pass a command through: `remote mini agent list` |
| `mirror` / `remote-changes` | pull remote herdr-change lists into the local DB and show them |
| `user` | multi-user tokens for `serve`: add/list/remove |
| `runner` | add/up/down/destroy/bootstrap/forward/preview/unforward/urls/env/push-env/snapshot |
| `share` | print remote attach / collaboration instructions |
| `notify` | desktop notification |
| `backup` | back up `changes.db` |
| `prune` | find orphaned changes/worktrees/workspaces |
| `doctor` | check herdr/git/gh/reviewr/DB |

## Safety gates

`land` runs these by default:

1. dirty check on every repo (`--allow-dirty` skips it)
2. a rejected review blocks landing; with `require_approval=true` an approved verdict is
   required (`--force` overrides)
3. pre-land checks:
   - `.agents/pre-land`
   - `.herdr-change/pre-land.sh`
   - `pre_land_script`
   - `pre_land_commands` (shell)
   - `--skip-checks` skips all of the above

## Multiple repositories

```bash
herdr-change new "cross-repo" --repo ~/fw --repo ~/host
```

- the first repo gets a herdr workspace; the rest are plain `git worktree add`
- `checkpoint/diff/land/cleanup` walk every repo
- `change_repos` stores each repo's branch, base, PR, and CI state
- `land --strategy pr` opens one PR per repo (gh/glab/az picked from origin)

## Sub-agents

```bash
# same worktree, split pane
herdr-change subagent <id> --profile scout --in-place --prompt "read-only investigation"

# separate worktree/change
herdr-change subagent <id> --profile worker --prompt "implement X"

herdr-change subagent <id> --list
herdr-change join <id> 1
```

`join` waits for the sub-agent to go idle, reads its output, writes it to
`<parent worktree>/.herdr-change/subagents/`, and sends that path to the parent agent.

## Configuration

```toml
worktree_root = "~/.herdr/worktrees"
default_agent = "codex"
land_strategy = "push"       # push | pr | merge | merge-pr | hook
notify = true
require_approval = false
fallback = []                 # e.g. ["codex", "claude"]
pre_land_script = ""
pre_land_commands = []

[roles.implementer]
kind = "codex"
args = ["--model", "gpt-5"]

[profiles.reviewer]
kind = "codex"
args = []
prompt = "You are a review sub-agent. Read the diff only; list risks, missing tests and rollback advice per file."
```

`land --strategy hook` runs `~/.config/herdr-change/land.sh` with:

```
CHANGE_ID CHANGE_LABEL REPO WORKTREE BRANCH REPOS
```

`runner` uses `~/.config/herdr-change/runner-up.sh` / `runner-down.sh` with
`RUNNER_NAME RUNNER_HOST RUNNER_REPO`.

## reviewr bridge

The local reviewr build is patched: every comment add/edit/delete/send writes the comment to
`~/.cache/herdr-reviewr/<worktree-hash>.json`. Sync it back with:

```bash
herdr-change comment <id> --from-reviewr
herdr-change review <id> --sync-reviewr
```

The patch lives on a local branch of the reviewr plugin (`1746108`), and
`bin/herdr-reviewr` is the rebuilt binary.

## emb-agent projects

Inside a `.emb-agent` project:

- `prefix+t` adopts the current emb task instead of showing the new-task prompt
- `herdr-change task` still refuses, so you cannot create a second worktree stack by accident
- `prefix+alt+d` adds `--emb-finish` automatically

Manual bridge:

```bash
cd ~/emb/workspace/QP/QP-GD26-0901
herdr-change emb status
herdr-change emb adopt              # adopt the active task's emb worktree
herdr-change emb adopt --no-agent   # reserve only, don't start an agent
```

`emb adopt` reads the active task, calls `emb-agent task activate --worktree` when there is no
worktree yet, takes the worktree over with `herdr worktree open`, and records `emb_task` in the
herdr-change DB.

`cleanup` calls `emb-agent task worktree cleanup` first instead of bypassing emb.

Before `land`, the emb gate is checked; if the active task is still in `verify`, landing is
blocked:

```text
emb verification pending ... run emb verify (or --skip-emb-check)
```

Once verification passes:

```bash
herdr-change land <id> --emb-finish --emb-disposition no-update --emb-summary "..."
```

`--emb-finish` calls `emb-agent finish-work` after landing to write the journal back.

## Local equivalents

- Web/Inbox: `herdr-change serve`, a local dashboard over the same SQLite file
- Shared review: `share --review` produces a standalone HTML bundle
- Cloud runner: `runner` + SSH/scripts + env/preview/snapshot, see [RUNNER.md](RUNNER.md)
- CRDT: intentionally not used; single-writer SQLite plus `serve` polling keeps things consistent
- Conversation editing/rollback: `turn --edit/--revert --reseed` rewrites the canonical context
  file and notifies the agent

## Upgrading from `herdr-thread`

Version 0.4 renamed the project to `herdr-change`. On first run the CLI migrates
`~/.config/herdr-thread` → `~/.config/herdr-change`, `threads.db` → `changes.db`, and renames
the legacy `threads`/`thread_repos`/`thread_deps`/`remote_threads` tables and `thread_id`
columns in place. `HERDR_THREAD_CONFIG` / `HERDR_THREAD_DATA` / `HERDR_THREAD_SERVE_TOKEN` are
still read as fallbacks, and `herdr-thread` remains as a thin alias binary.

## Development

```bash
python3 -m py_compile herdr_change.py
python3 -m unittest discover -s tests -p 'test_*.py'
tests/smoke.sh
```

`tests/smoke.sh` uses an isolated DB/config/worktree root and covers batch land, multi-repo,
checkpoints, turns, PRs, and recover.
