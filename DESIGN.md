# herdr-thread：用 herdr 复刻 Delta 的好用功能

目标：不依赖 Delta GUI，用 **herdr 原生能力 + reviewr 插件 + 一个轻量编排层 `herdr-thread`**，
覆盖 Delta 里真正有价值的工作流。

状态图例：✅ 已由 herdr 原生提供 · 🟡 本项目 MVP 提供 · 🔵 脚本/插件可补 · ❌ 明确不追

---

## 1. Delta 功能清单 → herdr 替代

### A. 工作单元与隔离

| # | Delta 功能 | 好用的点 | herdr 替代 | 状态 |
|---|---|---|---|---|
| A1 | Thread（线程） | 需求、对话、改动、审查都留在一个上下文里 | `herdr-thread` 的 thread 记录 = workspace + agent + worktree + 状态 | 🟡 |
| A2 | Delta worktree | 每个任务独占一个 checkout，互不污染 | `herdr worktree create/list/open/remove`，workspace 绑定 worktree | ✅ |
| A3 | 本地 checkout / attach | 不发布也能把已有目录接进工作流 | `herdr worktree open` / `workspace create --cwd` | ✅ |
| A4 | 必须有 remote | 保证 worktree 能 fetch/push | herdr 不强制；`herdr-thread land --push/--pr` 时才检查 | 🟡 |
| A5 | 恢复/归档/标签/收藏 | 多任务不丢上下文 | thread DB 的 `status/tags/bookmarked/archived`；workspace 标签 | 🟡 |
| A6 | Fork / 子线程 | 从当前改动分叉试另一条路 | `herdr-thread fork`：以父 thread 的 HEAD 建子 worktree + agent | 🟡 |
| A7 | 多仓库/子模块 | 一个任务跨仓 | 每个 thread 绑一个 repo；多仓用多个 thread 或 `--repo` 多次 | 🔵 |

### B. Agent 与并行

| # | Delta 功能 | 好用的点 | herdr 替代 | 状态 |
|---|---|---|---|---|
| B1 | 多 Agent 并行 | 每个任务一个 Agent | 每个 workspace 的 pane 起一个 `herdr agent start` | ✅ |
| B2 | Subagents（Worker/Scout/Reviewer） | 子任务隔离、不同模型 | `herdr-thread subagent --profile worker/scout/reviewer`，各自 worktree + prompt | 🟡 |
| B3 | Agent 状态/读取/等待 | 自动化编排 | `herdr agent list/get/read/wait/prompt/send-keys/focus` | ✅ |
| B4 | 模型选择 | 不同任务不同模型 | Agent 启动参数/环境变量；`herdr-thread` 读取 profile 配置注入 | 🔵 |
| B5 | 会话恢复 | 关掉再打开还能接上 | Agent 自身会话（codex/claude/pi）+ herdr 持久 session | ✅ |

### C. 审查与合并（Delta 的核心卖点）

| # | Delta 功能 | 好用的点 | herdr 替代 | 状态 |
|---|---|---|---|---|
| C1 | Review thread | 实现与审查分离 | `herdr-thread review`：开 reviewr + 可选 reviewer agent + 审查提示词 | 🟡 |
| C2 | Diff 面板 | 看 Agent 改了什么 | **reviewr**：uncommitted/branch/last-turn/commits 四种 scope | ✅ |
| C3 | 行内评论回传 | 评论直接变 Agent 输入 | **reviewr** line comments → send to agent | ✅ |
| C4 | Change guide | 带讲解的 diff | `review` 生成 reviewer prompt，要求按文件/风险/验证讲一遍 | 🟡 |
| C5 | Verdict | 通过/打回 | thread DB 的 `review_state` + `herdr-thread review --verdict` | 🟡 |
| C6 | Land changes | 一键合并/推送 | `herdr-thread land --push` / `--pr`；`--merge` 走 `--into` 目标分支 | 🟡 |
| C7 | Merge selected worktrees | 多线程合并 | 按依赖顺序逐个 `land`；批量脚本后续补 | 🔵 |
| C8 | 检查远程分支 / bring changes in | 同步上游 | `git fetch` + `rebase` 封装进 `herdr-thread sync --rebase` | 🟡 |
| C9 | PR 视图 | 不离开终端看 PR | reviewr 内置 PR tab（gh/glab/az） | ✅ |

### D. 终端

| # | Delta 功能 | 替代 | 状态 |
|---|---|---|---|
| D1 | 交互式终端 | herdr pane / tab | ✅ |
| D2 | 后台终端 | 独立 tab/pane，`herdr pane run` | ✅ |
| D3 | Agent 跑命令 | Agent 自身终端工具 | ✅ |
| D4 | 共享线程里的终端 | herdr remote attach 同一 session | 🟡 |

### E. 规则 / 项目设置

| # | Delta 功能 | 替代 | 状态 |
|---|---|---|---|
| E5 | `.agents/prepare` / setup | `new --run-hooks` 或 `setup <id>` | 🟡 |
| E6 | 项目规则 | `.agents/` + Agent 自身规则文件 | ✅ |

### F. Provider / 模型

| # | Delta 功能 | 替代 | 状态 |
|---|---|---|---|
| F1 | 自定义 provider | 由 codex/claude/pi 自己配置，本项目不管理 | ✅ |
| F2 | 模型角色 / fallback | profile 配置 `model` + 启动参数；fallback 交给 Agent | 🔵 |
| F3 | 上下文窗口/推理强度 | Agent 配置 | ✅ |

### G. 协作

| # | Delta 功能 | 替代 | 状态 |
|---|---|---|---|
| G1 | 共享 thread / 实时协作 | `herdr --remote <ssh>` 让多人 attach 同一 session | 🟡 |
| G2 | 评论/聊天/mention | reviewr 评论 + pane 里聊天；无 CRDT 富文本 | 🔵 |
| G3 | 权限/邀请/谁付费 | SSH 账号权限；模型费用各付各的 | 🔵 |
| G4 | Web 端 / Inbox | 不做；可用 ttyd 暴露 herdr（有安全风险） | ❌ |
| G5 | 通知 | `herdr notification show --title ... --body ...` | ✅ |

### H. Cloud Runners

| # | Delta 功能 | 替代 | 状态 |
|---|---|---|---|
| H1 | 云端临时 Linux runner | `herdr machine` / `--remote` 到固定 VM；临时 runner 用脚本创建 | 🔵 |
| H2 | `.agents/setup` / secrets | 在远程机器的 shell/profile 里做 | 🔵 |
| H3 | Preview URL | 远程机器自己起服务 + 端口转发 | 🔵 |
| H4 | Git push from runner | 远程机器的 git/gh 凭据 | 🔵 |

### I. UX

| # | Delta 功能 | 替代 | 状态 |
|---|---|---|---|
| I1 | Command palette | herdr 自带 | ✅ |
| I2 | Keybindings | `~/.config/herdr/config.toml` | ✅ |
| I3 | 搜索/文件查看 | reviewr 搜索 + file viewer；`rg` | ✅ |
| I4 | 附件/mention 文件 | Agent 自身支持（@ 文件） | ✅ |
| I5 | 编辑/回退消息 | Agent 会话自身；代码回退用 `herdr-thread checkpoint` + `git reset` | 🟡 |
| I6 | 书签/标签/过滤 | thread DB | 🟡 |
| I7 | 设置页 | `~/.config/herdr-thread/config.toml` | 🟡 |

### J. CLI / 自动化

| # | Delta CLI | 替代 | 状态 |
|---|---|---|---|
| J1 | `delta open` | `herdr-thread open <id>` / `herdr workspace focus` | 🟡 |
| J2 | `delta thread` | `herdr-thread list/show` | 🟡 |
| J3 | `delta worktree` | `herdr worktree *` + `herdr-thread new/cleanup` | ✅ |
| J4 | `delta auth` | 各 Agent 自己的登录 | ✅ |
| J6 | `delta integration` | herdr `integration` | ✅ |

---

## 2. herdr-thread 架构

```
┌────────────────────────────────────────────┐
│ herdr-thread (Python CLI)                  │
│  new/list/show/prompt/review/land/...      │
│  SQLite: ~/.local/share/herdr-thread/db    │
└───────────────┬────────────────────────────┘
                │ herdr CLI (JSON)
┌───────────────▼────────────────────────────┐
│ herdr server                               │
│  workspace ↔ worktree                      │
│  pane: shell / agent / reviewr             │
│  agent: codex/claude/pi/...                │
└────────────────────────────────────────────┘
```

- **一个 thread = 一个 workspace + 一个 worktree + 一个 agent pane + reviewr pane**。
- 状态不复制 Agent 的对话，只存编排元数据；对话留在 Agent 自己的 session 里。
- 所有写操作只通过 herdr CLI / git，不直接碰 herdr 内部状态。

### 数据表

```sql
threads(
  id, parent_id, label, repo, worktree, branch, base_ref,
  workspace_id, pane_id, review_pane_id,
  agent_name, agent_kind, status, review_state,
  tags, bookmarked, archived, created_at, head_sha, landed_at, meta
)
events(id, thread_id, at, kind, detail)
```

### 命令

```
new        建 worktree + workspace + agent + reviewr
list       状态总览（agent state + dirty + ahead/behind + review）
show       单线程详情
prompt     给该线程 Agent 发指令
review     聚焦 + 打开 reviewr（可选 reviewer agent / 记录 verdict）
checkpoint git commit 当前改动（可回退点）
land       push / PR / merge
sync       fetch + 更新状态；--rebase 拉上游
fork       从当前 HEAD 建子线程
subagent   worker/scout/reviewer 子线程
open       聚焦 workspace
tag        标签/收藏/归档
cleanup    移除 worktree + 关闭 workspace
```

### 配置 `~/.config/herdr-thread/config.toml`

```toml
worktree_root = "~/.herdr-worktrees"
default_agent = "codex"
review_plugin = "persiyanov.reviewr"
land_strategy = "push"        # push | pr | merge
notify = true

[profiles.worker]
kind = "codex"
args = []
prompt = "你是实现子 Agent，只做被指派的子任务，完成后总结改动和验证。"

[profiles.scout]
kind = "codex"
args = []
prompt = "你是调研子 Agent，只读代码并给出结论，不改文件。"

[profiles.reviewer]
kind = "codex"
args = []
prompt = "你是审查子 Agent，只读 diff，按文件列出风险、遗漏测试和回退建议。"
```

---

## 3. 分阶段

- **P0（已完成）**：`new/list/show/diff/prompt/review/checkpoint/land/cleanup/open` + DB + 配置。
- **P1（已完成）**：`fork/subagent/sync --rebase/tag/archive`、状态轮询 + 通知、`.agents/prepare|setup` 钩子。
- **P2（已完成）**：review verdict 持久化、`review --reviewer`、批量 land、PR/CI 状态轮询。
- **P3（已完成）**：`machines/remote/notify` 透传、runner 生命周期/端口转发、share、RUNNER.md；托管平台本身不做。

---

## 4. 明确的取舍

- **不复制**：DeltaDB/CRDT、每轮增量快照、Web 端、富文本多人协作、云端临时 runner 的托管体验。
- **保留**：git 作为唯一事实来源；Agent 自己的会话配置；reviewr 的审查体验。
- **优势**：原生 WSL、无 GUI/DPI/光标/Portal 问题；可脚本化；换 Agent 不改工作流。


---

## 5. 实现状态（v0.1，2026-10）

已落地命令：

```
new  list  show  setup  transcript  turn  search  capture  prompt  review
comment  attach  send  checkpoint  land  pr  cleanup  open  diff  tag  sync
recover  watch  fork  subagent  join  machines  remote  runner  share  notify
backup  prune  doctor
```

已验证：

- dummy repo：`new → reviewr → agent → prompt → sync → checkpoint → review --reviewer → land merge → cleanup`；
- 批量 land：`land --all --strategy push --yes --cleanup`，两个分支都推到 bare remote；
- PR/CI：fake `gh` 下 `land pr → sync --pr → MERGED`，`pr_state/ci_state` 正确迁移；
- `land --strategy hook` 环境变量（THREAD_ID/BRANCH/WORKTREE）验证通过。
- `.agents/prepare` + `.agents/setup` 在新 worktree 内执行；
- `QP-GD26-0901`：`new --no-agent` 创建/清理后 `git worktree list` 无残留。

已知边界：

- 多 repo 线程：主 repo 用 herdr workspace，其余 repo 用 `git worktree add`；Agent 默认在主 repo。
- 远端 thread 的 DB 在远端机器，本地 `remote` 只做命令透传；runner 是脚本化流程。
- reviewr 已打补丁：评论变更写入 `~/.cache/herdr-reviewr/*.json`，`comment --from-reviewr` / `review --sync-reviewr` 回写 SQLite。
- 不做每轮自动快照；用 `checkpoint` 显式建提交作为回退点。
- turn 的 edit/revert 只改元数据，不能改写 Agent 已发送的上下文。


---

## 6. v0.2 补齐（2026-10）

新增：

- 安全门：dirty / rejected / require_approval / pre-land 脚本与命令；`land --allow-dirty/--force/--skip-checks`
- PR 闭环：`merge-pr`、auto-merge、`pr --watch`、`watch --daemon/--status/--stop`、CI 归并
- 恢复：`checkpoint --list/--restore`、`recover --create/--start/--prune`、`prune`
- 多仓库：`thread_repos`、`thread_repos` 级 PR/CI、checkpoint/diff/land/cleanup 全仓遍历
- 记录：turns / comments / attachments / search / bookmark / fork 上下文注入 / send
- 子 Agent：`subagent --in-place` + `join`；独立线程子 Agent 仍保留
- 模型：`roles.*`、`--role`、`fallback`
- 运行环境：`runner add/up/down/bootstrap/forward/unforward`、`share`
- 工程化：`backup`、`prune`、`HERDR_THREAD_DATA/CONFIG`、`pyproject.toml`、`tests/smoke.sh`

仍不追：DeltaDB/CRDT、Web 端、富文本多人协作、托管 Cloud Runner。

---

## 7. v0.3 补齐（2026-10）

- Thread 生命周期：`children/resume/rename/delete/restore-thread`、`mention/openfile`
- 对话编辑：`turn --edit/--revert --reseed` 写 canonical context 并重新对齐 Agent
- Review：`guide`、`share --review` HTML、`check-remote`
- Land：依赖 `depend` + 拓扑排序、`local` 策略、`--skip-deps`
- Sync：`--rebase` 与 `--merge` 都先 fetch
- 子 Agent：`follow` 聚焦/读取
- Terminal：线程级 list/new/run/close
- Runner：env/push-env/preview/urls/snapshot/destroy
- Web/Inbox：`serve` 本地 dashboard、POST 写评论/note、token/TLS
- CLI：`config`（含嵌套 key）、`delta` 别名、`restore-db`
- 工程：单元测试 + GitHub Actions、SQLite `user_version=3`

本地等价物：Web/CRDT/托管 runner 用 `serve` + SQLite 单写、review HTML、SSH 脚本替代。

## 8. v0.4 收尾（2026-10）

- `serve` 支持 POST 写评论/note + token 鉴权；`--daemon/--stop/--status`
- `comment --import/--export/--from-clipboard`，`comment --send --to` 跨线程
- `land --json` 纯 JSON（日志在 `log` 字段）
- `config set`、`delta auth login/logout`、`usage`、`restore-db`
- 写命令文件锁 `write.lock`
- `fork --clone-session` fork pi/codex session；多仓 fork/subagent 克隆额外 repo；`HERDR_THREAD_REPOS` 注入 agent 环境；`repos` 命令
- 远端镜像：`mirror <machine>` / `remote-threads`
- PR：GitHub/GitLab/Azure 创建与状态读取
- 写命令文件锁
- SQLite `user_version=3`；unit + smoke + GitHub Actions

## 9. emb-agent 桥接（L1）

- `emb status`：只读 active task / gate / journal
- `emb adopt`：用 emb 创建/选择 worktree，再 `herdr worktree open` 接管
- thread 记录 `emb_task`；`list/show` 显示
- `cleanup` 先调 `emb-agent task worktree cleanup`
- 不自己给 `.emb-agent` 项目建 git worktree
- `land` 前跑 `emb-agent start --json` gate；`--skip-emb-check` 可覆盖
- `--emb-finish` 在 land 后调用 `emb-agent finish-work` 回写 journal
- 待做：自动生成 emb verification（当前由 emb 自己的流程完成）
