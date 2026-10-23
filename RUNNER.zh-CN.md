> 中文文档 · [English](RUNNER.md)

# Remote / collab / cloud runner

Delta 的 Cloud Runner 本质是：临时机器 + checkout + setup 脚本 + Agent。herdr 侧用
`saved SSH machine` + 远端 herdr server + 同一套 `herdr-change` 概念替代，不做托管平台。

## 1. 准备一台远端机器

要求：能 SSH、装好 git、有 herdr（>= 0.7.5），并且远端 herdr server 已启动。

```bash
ssh mini
# 远端
git clone <repo> ~/work/<repo>
cd ~/work/<repo>
herdr server start          # 或按远端环境用 systemd --user
```

本机保存连接：

```bash
herdr machine add            # 交互式，选择远端 session
herdr-change machines
herdr machine status mini --json
```

## 2. 在远端建改动

`herdr --machine` 只接受已保存的 profile label/id；远端 worktree 路径必须是绝对路径、
`~` 或 `~/...`。

```bash
herdr --machine mini worktree create \
  --cwd ~/work/QP-GD26-0901 \
  --branch herdr-change/irq-mini \
  --base HEAD \
  --path ~/worktrees/QP-GD26-0901/irq-mini \
  --label "IRQ on mini" \
  --no-focus

herdr --machine mini agent start hc-mini --kind codex --pane <REMOTE_PANE_ID>
herdr --machine mini agent prompt hc-mini "先跑 .agents/prepare，再改 IRQ 采集"
herdr --machine mini agent read hc-mini --lines 80 --source recent
```

本工具的透传：

```bash
herdr-change remote mini agent list
herdr-change remote mini agent prompt hc-mini "继续"
herdr-change remote mini agent read hc-mini --lines 80
herdr-change remote mini workspace list
```

> 注意：`herdr-change` 的 change DB / worktree 路径是本机的；远端改动用 `remote` 透传管理，
> 不要拿本地 change id 去猜远端 pane id。需要远端编排时，在远端也安装 `herdr-change`，
> 用同一套命令 + 远端 DB。

## 3. 临时 runner（一次性机器）

Delta 的 runner 是创建即用、归档即删。等价做法：

```bash
# 1) 起临时机器（Tailscale / 云厂商 CLI / 本地 VM 均可）
tailscale up --authkey "$TS_AUTHKEY" --hostname "runner-$RANDOM"

# 2) 在 runner 上跑 setup
ssh runner 'set -eu
  git clone --depth=1 -b main <repo> ~/work/repo
  cd ~/work/repo
  [ -x .agents/prepare ] && ./.agents/prepare
  [ -x .agents/setup ] && ./.agents/setup
  herdr server start'

# 3) 本机挂 machine、建 worktree、起 agent
herdr machine add
herdr --machine runner worktree create --cwd ~/work/repo ...
```

销毁：

```bash
herdr --machine runner workspace close <WS>
# 或直接销毁机器（Tailscale/云厂商）
```

## 4. 协作 / 共享改动

Delta 的 share link 在 herdr 里对应「同一台 server + 远程 attach」：

```bash
# 对方直接 attach 到你的 herdr server（需要 SSH 权限）
herdr --remote you@host

# 或对方有自己的 herdr，通过 saved machine 控制
herdr machine add
herdr --machine your-host agent list
```

通知用 herdr 原生：

```bash
herdr-change notify "Review ready" --body "change abc123 · QP-GD26-0901" --sound request
herdr-change sync       # 状态变化时自动发通知
```

## 5. 与 Delta Cloud Runner 的差异

| Delta | 这里 |
|---|---|
| 托管 runner、自动快照 | 自己起 VM / Tailscale，快照自己管 |
| Web 端共享 change | SSH attach / `--machine` 控制 |
| 云端 secrets 管理 | 远端环境变量 / `.agents/prepare` |
| runner 生命周期自动回收 | 脚本 / 云厂商 CLI 回收 |

保留的能力：setup 脚本、隔离 checkout、远端 Agent、状态读取、审查、land。

## 6. `herdr-change runner` 封装

```bash
# 记录一台机器
herdr-change runner add mini --host mini --repo git@github.com:you/repo.git

# 可选：自定义创建/销毁脚本
# ~/.config/herdr-change/runner-up.sh
# ~/.config/herdr-change/runner-down.sh
herdr-change runner up mini
herdr-change runner down mini

# 在远端 clone + 跑 .agents/prepare / setup + 启动 herdr server
herdr-change runner bootstrap mini --repo git@github.com:you/repo.git --path '~/work/repo'

# 预览端口转发
herdr-change runner forward mini --local 8080 --remote 3000
herdr-change runner unforward mini
herdr-change runner list
```

`runner bootstrap` 只依赖 SSH，不假设云厂商；创建/销毁临时机器仍由
Tailscale/云 CLI/本地 VM 负责。

## 7. Env / preview / snapshot

```bash
herdr-change runner env mini --set OPENAI_API_KEY=... --set DATABASE_URL=...
herdr-change runner env mini --list-env
herdr-change runner push-env mini          # scp 到远端 ~/.herdr-change-mini.env
herdr-change runner bootstrap mini --repo ... # 自动 source 该 env

herdr-change runner preview mini --local 8080 --remote 3000
herdr-change runner urls
herdr-change runner snapshot mini          # 调 runner-snapshot.sh
```

环境文件在 `~/.config/herdr-change/runners/<name>.env`，权限 600。
`runner-snapshot.sh` 由用户提供（云厂商快照、VM image、或 rsync 都行）。
