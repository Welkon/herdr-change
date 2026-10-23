> English · [中文](RUNNER.zh-CN.md)

# Remote / collaboration / cloud runners

Delta's Cloud Runner is really: an ephemeral machine + checkout + setup scripts + agent. On the
herdr side, use a **saved SSH machine + a remote herdr server + the same `herdr-change`
concepts**. No hosted platform.

## 1. Prepare a remote machine

Requirements: SSH access, git installed, herdr (>= 0.7.5) with its server running.

```bash
ssh mini
# on the remote
git clone <repo> ~/work/<repo>
cd ~/work/<repo>
herdr server start          # or systemd --user, depending on the remote environment
```

Save the connection locally:

```bash
herdr machine add            # interactive: pick the remote session
herdr-change machines
herdr machine status mini --json
```

## 2. Create a change on the remote

`herdr --machine` only accepts saved profile labels/ids; remote worktree paths must be
absolute, `~`, or `~/...`.

```bash
herdr --machine mini worktree create \
  --cwd ~/work/QP-GD26-0901 \
  --branch herdr-change/irq-mini \
  --base HEAD \
  --path ~/worktrees/QP-GD26-0901/irq-mini \
  --label "IRQ on mini" \
  --no-focus

herdr --machine mini agent start hc-mini --kind codex --pane <REMOTE_PANE_ID>
herdr --machine mini agent prompt hc-mini "run .agents/prepare first, then change IRQ sampling"
herdr --machine mini agent read hc-mini --lines 80 --source recent
```

Passthrough from this tool:

```bash
herdr-change remote mini agent list
herdr-change remote mini agent prompt hc-mini "continue"
herdr-change remote mini agent read hc-mini --lines 80
herdr-change remote mini workspace list
```

> Note: `herdr-change`'s change DB and worktree paths are local. Manage remote changes through
> the `remote` passthrough; never guess a remote pane id from a local change id. For real remote
> orchestration, install `herdr-change` on the remote too and use the same commands against the
> remote DB.

## 3. Ephemeral runners (throwaway machines)

Delta's runner is create-on-demand, delete-on-archive. The equivalent:

```bash
# 1) boot a throwaway machine (Tailscale / cloud CLI / a local VM all work)
tailscale up --authkey "$TS_AUTHKEY" --hostname "runner-$RANDOM"

# 2) run setup on the runner
ssh runner 'set -eu
  git clone --depth=1 -b main <repo> ~/work/repo
  cd ~/work/repo
  [ -x .agents/prepare ] && ./.agents/prepare
  [ -x .agents/setup ] && ./.agents/setup
  herdr server start'

# 3) save the machine locally, create a worktree, start an agent
herdr machine add
herdr --machine runner worktree create --cwd ~/work/repo ...
```

Teardown:

```bash
herdr --machine runner workspace close <WS>
# or destroy the machine outright (Tailscale / cloud CLI)
```

## 4. Collaboration / sharing a change

Delta's share link maps to "same server + a remote attach":

```bash
# the other person attaches to your herdr server (needs SSH access)
herdr --remote you@host

# or they run their own herdr and drive yours through a saved machine
herdr machine add
herdr --machine your-host agent list
```

Notifications use herdr natively:

```bash
herdr-change notify "Review ready" --body "change abc123 · QP-GD26-0901" --sound request
herdr-change sync       # notifies automatically on state changes
```

## 5. Differences from Delta Cloud Runner

| Delta | Here |
|---|---|
| hosted runners, automatic snapshots | you boot VMs / Tailscale; you manage snapshots |
| web-shared change | SSH attach / `--machine` control |
| cloud secret management | remote env vars / `.agents/prepare` |
| automatic runner GC | scripts / cloud CLI cleanup |

What is preserved: setup scripts, an isolated checkout, a remote agent, state reads, review, landing.

## 6. The `herdr-change runner` wrapper

```bash
# register a machine
herdr-change runner add mini --host mini --repo git@github.com:you/repo.git

# optional: custom create/destroy scripts
# ~/.config/herdr-change/runner-up.sh
# ~/.config/herdr-change/runner-down.sh
herdr-change runner up mini
herdr-change runner down mini

# clone remotely + run .agents/prepare / setup + start the herdr server
herdr-change runner bootstrap mini --repo git@github.com:you/repo.git --path '~/work/repo'

# preview port forwarding
herdr-change runner forward mini --local 8080 --remote 3000
herdr-change runner unforward mini
herdr-change runner list
```

`runner bootstrap` depends on SSH only and assumes no particular cloud; creating/destroying
ephemeral machines is still Tailscale/cloud CLI/local VM territory.

## 7. Env / preview / snapshot

```bash
herdr-change runner env mini --set OPENAI_API_KEY=... --set DATABASE_URL=...
herdr-change runner env mini --list-env
herdr-change runner push-env mini          # scp to remote ~/.herdr-change-mini.env
herdr-change runner bootstrap mini --repo ... # sources that env automatically

herdr-change runner preview mini --local 8080 --remote 3000
herdr-change runner urls
herdr-change runner snapshot mini          # calls runner-snapshot.sh
```

Env files live in `~/.config/herdr-change/runners/<name>.env`, mode 600.
`runner-snapshot.sh` is yours to provide (cloud snapshot, VM image, or plain rsync).
