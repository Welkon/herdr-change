# Remote / collab / cloud runner

Delta 的 Cloud Runner 本质是：临时机器 + checkout + setup 脚本 + Agent。herdr 侧用
`saved SSH machine` + 远端 herdr server + 同一套 `herdr-thread` 概念替代，不做托管平台。

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
herdr-thread machines
herdr machine status mini --json
```

## 2. 在远端建线程

`herdr --machine` 只接受已保存的 profile label/id；远端 worktree 路径必须是绝对路径、
`~` 或 `~/...`。

```bash
herdr --machine mini worktree create \
  --cwd ~/work/QP-GD26-0901 \
  --branch herdr-thread/irq-mini \
  --base HEAD \
  --path ~/worktrees/QP-GD26-0901/irq-mini \
  --label "IRQ on mini" \
  --no-focus

herdr --machine mini agent start ht-mini --kind codex --pane <REMOTE_PANE_ID>
herdr --machine mini agent prompt ht-mini "先跑 .agents/prepare，再改 IRQ 采集"
herdr --machine mini agent read ht-mini --lines 80 --source recent
```

本工具的透传：

```bash
herdr-thread remote mini agent list
herdr-thread remote mini agent prompt ht-mini "继续"
herdr-thread remote mini agent read ht-mini --lines 80
herdr-thread remote mini workspace list
```

> 注意：`herdr-thread` 的 thread DB / worktree 路径是本机的；远端线程用 `remote` 透传管理，
> 不要拿本地 thread id 去猜远端 pane id。需要远端编排时，在远端也安装 `herdr-thread`，
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

## 4. 协作 / 共享线程

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
herdr-thread notify "Review ready" --body "thread abc123 · QP-GD26-0901" --sound request
herdr-thread sync       # 状态变化时自动发通知
```

## 5. 与 Delta Cloud Runner 的差异

| Delta | 这里 |
|---|---|
| 托管 runner、自动快照 | 自己起 VM / Tailscale，快照自己管 |
| Web 端共享 thread | SSH attach / `--machine` 控制 |
| 云端 secrets 管理 | 远端环境变量 / `.agents/prepare` |
| runner 生命周期自动回收 | 脚本 / 云厂商 CLI 回收 |

保留的能力：setup 脚本、隔离 checkout、远端 Agent、状态读取、审查、land。

## 6. `herdr-thread runner` 封装

```bash
# 记录一台机器
herdr-thread runner add mini --host mini --repo git@github.com:you/repo.git

# 可选：自定义创建/销毁脚本
# ~/.config/herdr-thread/runner-up.sh
# ~/.config/herdr-thread/runner-down.sh
herdr-thread runner up mini
herdr-thread runner down mini

# 在远端 clone + 跑 .agents/prepare / setup + 启动 herdr server
herdr-thread runner bootstrap mini --repo git@github.com:you/repo.git --path '~/work/repo'

# 预览端口转发
herdr-thread runner forward mini --local 8080 --remote 3000
herdr-thread runner unforward mini
herdr-thread runner list
```

`runner bootstrap` 只依赖 SSH，不假设云厂商；创建/销毁临时机器仍由
Tailscale/云 CLI/本地 VM 负责。
