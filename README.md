# herdr-thread

Delta 式「线程 + worktree + Agent + 审查 + 落地」工作流，构建在 [herdr](https://herdr.dev) 之上。

不依赖 Delta GUI，也不碰 herdr 内部状态：全部通过 herdr CLI / git 操作。

- 设计文档与 Delta 功能对照：[DESIGN.md](DESIGN.md)
- 状态库：`~/.local/share/herdr-thread/threads.db`（SQLite）
- 配置：`~/.config/herdr-thread/config.toml`

## 依赖

- `herdr >= 0.7.5`（需要 `herdr` 在 PATH 上，且 server 正在运行）
- `reviewr` 插件（`herdr plugin install persiyanov/herdr-reviewr`）
- `git`
- 可选：`gh`（`land --strategy pr`）

本机已经装好并链接到 `~/.local/bin/herdr-thread`。

## 快速开始

```bash
# 1. 新建线程：worktree + workspace + agent + reviewr
herdr-thread new "IRQ rewrite" \
  --repo ~/emb/workspace/QP/QP-GD26-0901 \
  --agent codex \
  --prompt "把 IRQ 采集改成 forced-read 方案，先只改 KEY/M"

# 2. 看所有线程状态
herdr-thread list
herdr-thread show <id>

# 3. 给 Agent 追加指令
herdr-thread prompt <id> "补上单元测试" --wait

# 4. 审查：聚焦 workspace + 打开 reviewr
herdr-thread review <id>
herdr-thread review <id> --verdict approved

# 5. 打 checkpoint（可回退点）
herdr-thread checkpoint <id> -m "wip: IRQ rewrite"

# 6. 落地
herdr-thread land <id> --strategy push          # push -u origin <branch>
herdr-thread land <id> --strategy pr            # gh pr create
herdr-thread land <id> --strategy merge --into main
herdr-thread land <id> --strategy hook          # 执行 ~/.config/herdr-thread/land.sh

# 7. 清理 worktree / workspace
herdr-thread cleanup <id>
```

## 线程 = 什么

一条线程对应：

```
thread
├── git worktree        独立 checkout（不污染主仓）
├── herdr workspace     独立窗口/标签
├── agent pane          codex / claude / pi / ...
└── reviewr pane        diff + 行内评论 + PR
```

对话历史不在本工具里，留在 Agent 自己的 session；本工具只编排和记录状态。

## 命令

| 命令 | 作用 |
|---|---|
| `new` | 建 worktree + workspace + agent + reviewr |
| `list` | 状态总览（agent state / dirty / ahead / review），支持 `--tag --search --bookmarked --all` |
| `show` | 单线程详情 + 最近提交 + uncommitted diff stat |
| `diff` | 打印 `base..HEAD` + 未提交 diff |
| `prompt` | 给 Agent 发指令（`--wait` 等待状态） |
| `review` | 聚焦 + 打开 reviewr，记录 verdict |
| `checkpoint` | `git add -A && git commit`，记录 head |
| `land` | `push` / `pr` / `merge` / `hook` |
| `sync` | 刷新 agent 状态；`--rebase` 拉上游；状态变化发通知 |
| `fork` | 从父线程当前 HEAD 建子线程 |
| `subagent` | `--profile worker/scout/reviewer` 子线程 |
| `open` | 聚焦 workspace |
| `tag` | 标签 / 收藏 / 归档 |
| `cleanup` | 移除 worktree + 关闭 workspace |
| `doctor` | 检查 herdr/git/gh/DB |

## 配置

`~/.config/herdr-thread/config.toml`：

```toml
worktree_root = "~/.herdr-worktrees"
default_agent = "codex"
review_plugin = "persiyanov.reviewr"
review_entrypoint = "pane"
land_strategy = "push"       # push | pr | merge | hook
notify = true

[profiles.worker]
kind = "codex"
prompt = "你是实现子 Agent。只做被指派的子任务，完成后总结改动文件和验证方式。"

[profiles.scout]
kind = "codex"
prompt = "你是调研子 Agent。只读代码并给出结论，不修改任何文件。"

[profiles.reviewer]
kind = "codex"
prompt = "你是审查子 Agent。只读 diff，按文件列出风险、遗漏测试和回退建议，不改文件。"
```

`land --strategy hook` 会执行 `~/.config/herdr-thread/land.sh`，环境变量：

```
THREAD_ID THREAD_LABEL REPO WORKTREE BRANCH
```

## 与 Delta 的取舍

已覆盖：worktree 隔离、thread 元数据、多 Agent、reviewr 审查/行内评论、land、checkpoint、fork/subagent、标签/归档、通知、CLI。

不追：DeltaDB/CRDT、Web 端、富文本多人协作、托管式云端临时 runner。详见 DESIGN.md。

## 开发

```bash
python3 herdr_thread.py --help
python3 -m py_compile herdr_thread.py
```
