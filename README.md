# herdr-thread

Delta 式「线程 + worktree + Agent + 审查 + 落地」工作流，构建在 [herdr](https://herdr.dev) 之上。

不依赖 Delta GUI，也不碰 herdr 内部状态：全部通过 herdr CLI / git / SQLite 操作。

- 设计文档与 Delta 功能对照：[DESIGN.md](DESIGN.md)
- 远端/临时 runner：[RUNNER.md](RUNNER.md)
- 状态库：`~/.local/share/herdr-thread/threads.db`
- 配置：`~/.config/herdr-thread/config.toml`

## 依赖

- `herdr >= 0.7.5`，且 server 正在运行
- `reviewr` 插件（`herdr plugin install persiyanov/herdr-reviewr`）
- `git`
- 可选：`gh`（PR / CI）

本机已链接到 `~/.local/bin/herdr-thread`。

## 快速开始

```bash
# 1. 新建线程：worktree + workspace + agent + reviewr
herdr-thread new "IRQ rewrite" \
  --repo ~/emb/workspace/QP/QP-GD26-0901 \
  --agent codex \
  --prompt "把 IRQ 采集改成 forced-read 方案"

# 多仓库线程（同一 thread，多个 worktree）
herdr-thread new "跨仓改动" --repo ~/proj/fw --repo ~/proj/host --no-agent

# 2. 状态
herdr-thread list
herdr-thread show <id>
herdr-thread diff <id>

# 3. 给 Agent 指令 / 捕获回复
herdr-thread prompt <id> "补上单元测试" --wait
herdr-thread capture <id>          # 把当前 agent 输出存成 turn
herdr-thread transcript <id>       # 浏览/搜索线程记录

# 4. 审查
herdr-thread review <id> --reviewer --agent codex
herdr-thread comment <id> "这里要判空" --file main.c --line 42
herdr-thread comment <id> --send
herdr-thread review <id> --verdict approved

# 5. checkpoint / 回退
herdr-thread checkpoint <id> -m "wip: IRQ"
herdr-thread checkpoint <id> --list
herdr-thread checkpoint <id> --restore latest

# 6. 落地
herdr-thread land <id> --strategy push
herdr-thread land <id> --strategy pr
herdr-thread land <id> --strategy merge --into main
herdr-thread land <id> --strategy merge-pr --method squash
herdr-thread land <id> --strategy hook

# 批量
herdr-thread land --all --strategy push --dry-run
herdr-thread land --all --strategy push --yes --cleanup

# 7. PR / CI
herdr-thread pr <id> --watch
herdr-thread sync --pr
herdr-thread watch --daemon --interval 60
herdr-thread watch --status
herdr-thread watch --stop

# 8. 生命周期 / 审查包 / 本地 Web
herdr-thread children <id>
herdr-thread resume <id>
herdr-thread rename <id> "new label"
herdr-thread delete <id> --keep-worktree
herdr-thread guide <id>                 # review guide markdown
herdr-thread share <id> --review        # 独立 review HTML
herdr-thread serve --daemon             # http://127.0.0.1:8765

# 9. 清理 / 恢复
herdr-thread cleanup <id>
herdr-thread recover --create      # herdr 重启/workspace 丢失后重新绑定
herdr-thread prune --workspaces --dry-run
```

## 一条线程 = 什么

```
thread
├── 1..N 个 git worktree（多仓库）
├── herdr workspace（主仓）
├── agent pane
├── reviewr pane
├── checkpoints / turns / comments / attachments（SQLite）
└── PR / CI 状态（thread_repos）
```

对话本体仍归 Agent 自己的 session；`herdr-thread` 只保存可搜索的 turn 摘要、评论和编排元数据。

## 命令

| 命令 | 作用 |
|---|---|
| `new` | 建 worktree/workspace/agent/reviewr；`--repo` 可重复；`--profile/--role/--run-hooks` |
| `list` | 状态总览；`--tag --search --bookmarked --all --json` |
| `show` | 线程详情、repos、checkpoints、PR；`--json` |
| `diff` | 所有 repo 的 `base..HEAD` + 未提交 diff |
| `setup` | 在 worktree 跑 `.agents/prepare` / `.agents/setup` |
| `prompt` | 给 Agent 发指令；`--wait` |
| `capture` | 读取 agent 输出并写入 turns |
| `transcript` | 浏览 turns；`--search --bookmarked` |
| `turn` | `--add/--list/--bookmark/--edit/--revert/--reseed` |
| `search` | 跨线程搜索 turns |
| `review` | 打开 reviewr；`--reviewer` 起只读审查 agent；`--verdict` |
| `comment` | 行内评论：新增 / `--list` / `--send` / `--resolve` |
| `attach` | 文件或 `--text` 附件复制到 worktree `.herdr-thread/attachments` |
| `send` | 把文本/transcript 发给另一个线程的 agent |
| `children` | 子线程树 |
| `resume` | 重新绑定/聚焦线程，必要时启动 agent |
| `rename` / `delete` / `restore-thread` | 线程生命周期 |
| `mention` / `openfile` | 附件并提示 Agent / 解析 worktree 文件 |
| `guide` | 生成 review guide（commits/files/reviewer notes） |
| `share --review` | 生成独立 review HTML |
| `depend` | 线程依赖；`land` 会检查/拓扑排序 |
| `check-remote` | 对比本地 branch 与 origin |
| `subagent follow` | `follow <parent> <sub>` 聚焦并读子 Agent |
| `models` / `provider` | 角色、profile、fallback、provider 管理 |
| `terminal` | 线程级终端 list/new/run/close |
| `serve` | 本地 Web dashboard / inbox |
| `config` | 显示有效配置/路径 |
| `delta` | Delta 风格别名：open/thread/worktree |
| `restore-db` | 从 backup 恢复 threads.db |
| `checkpoint` | 提交 / `--list` / `--restore`，每个 repo 单独记录 |
| `land` | `push/pr/merge/merge-pr/local/hook`；批量 `--all/--tag`；dirty/verdict/pre-land/deps gate |
| `pr` | 查看 / `--watch` PR + CI |
| `sync` | 刷新 agent 状态；`--rebase`（先 fetch）；`--pr` |
| `watch` | 后台轮询；`--daemon/--status/--stop` |
| `fork` | 从当前 HEAD 建子线程；`--with-context/--from-turn` 注入父 transcript |
| `subagent` | 独立线程或 `--in-place` 同 worktree 子 Agent；`--list` |
| `join` | 收集子 Agent 输出并回传父 Agent |
| `open` | 聚焦 workspace |
| `tag` | 标签 / 收藏 / 归档 |
| `cleanup` | 移除所有 repo 的 worktree + workspace |
| `recover` | 重新绑定 workspace/pane/agent；`--create/--start/--prune` |
| `machines` | 列出 herdr SSH machines |
| `remote` | 透传远端命令：`remote mini agent list` |
| `runner` | add/up/down/destroy/bootstrap/forward/preview/unforward/urls/env/push-env/snapshot |
| `share` | 打印远程 attach / 协作说明 |
| `notify` | 桌面通知 |
| `backup` | 备份 threads.db |
| `prune` | 清理孤儿 thread/worktree/workspace |
| `doctor` | 检查 herdr/git/gh/reviewr/DB |

## 安全门

`land` 默认执行：

1. 所有 repo 的 dirty 检查（`--allow-dirty` 跳过）
2. rejected 审查阻断；`require_approval=true` 时要求 approved（`--force` 跳过）
3. pre-land 检查：
   - `.agents/pre-land`
   - `.herdr-thread/pre-land.sh`
   - `pre_land_script`
   - `pre_land_commands`（shell）
   - `--skip-checks` 跳过

## 多仓库

```bash
herdr-thread new "跨仓" --repo ~/fw --repo ~/host
```

- 第一个 repo 由 herdr 建 workspace；其余用 `git worktree add`
- `checkpoint/diff/land/cleanup` 遍历全部 repo
- `thread_repos` 保存每个 repo 的 branch、base、PR、CI
- `land --strategy pr` 每个 repo 各开一个 PR

## 子 Agent

```bash
# 同 worktree，split pane
herdr-thread subagent <id> --profile scout --in-place --prompt "只读调研"

# 独立 worktree/thread
herdr-thread subagent <id> --profile worker --prompt "实现 X"

herdr-thread subagent <id> --list
herdr-thread join <id> 1
```

`join` 等待子 Agent idle，读取输出，写到父 worktree `.herdr-thread/subagents/`，并把路径发给父 Agent。

## 配置

```toml
worktree_root = "~/.herdr-worktrees"
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
prompt = "你是审查子 Agent。只读 diff，按文件列风险、遗漏测试和回退建议。"
```

`land --strategy hook` 执行 `~/.config/herdr-thread/land.sh`，环境变量：

```
THREAD_ID THREAD_LABEL REPO WORKTREE BRANCH REPOS
```

`runner` 使用 `~/.config/herdr-thread/runner-up.sh` / `runner-down.sh`，环境变量
`RUNNER_NAME RUNNER_HOST RUNNER_REPO`。

## 本地等价物

- Web/Inbox：`herdr-thread serve` 本地 dashboard，读取同一 SQLite
- 共享 review：`share --review` 生成独立 HTML bundle
- Cloud Runner：`runner` + SSH/脚本 + env/preview/snapshot，见 [RUNNER.md](RUNNER.md)
- CRDT：不引入；以 SQLite 单写 + serve 轮询保证一致性
- 对话编辑/回退：`turn --edit/--revert --reseed` 重写 canonical context 文件并通知 Agent

## 开发

```bash
python3 -m py_compile herdr_thread.py
tests/smoke.sh
```

`tests/smoke.sh` 使用隔离 DB/config/worktree，覆盖批量 land、多仓、checkpoint、turns、PR、recover。
