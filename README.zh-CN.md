> 中文文档 · [English](README.md)

# herdr-change

Delta 式「改动 + worktree + Agent + 审查 + 落地」工作流，构建在 [herdr](https://herdr.dev) 之上。

不依赖 Delta GUI，也不碰 herdr 内部状态：全部通过 herdr CLI / git / SQLite 操作。

- 设计文档与 Delta 功能对照：[DESIGN.zh-CN.md](DESIGN.zh-CN.md)
- 远端/临时 runner：[RUNNER.zh-CN.md](RUNNER.zh-CN.md)
- 状态库：`~/.local/share/herdr-change/changes.db`
- 配置：`~/.config/herdr-change/config.toml`

## 依赖

- `herdr >= 0.7.5`，且 server 正在运行
- `reviewr` 插件（`herdr plugin install persiyanov/herdr-reviewr`）
- `git`
- 可选：`gh`（PR / CI）

本机已链接到 `~/.local/bin/herdr-change`。

## Herdr 键位（推荐）

```text
prefix+t        新建任务（插件 popup，标题 new task）
prefix+alt+r    审查当前 worktree 对应改动
prefix+alt+d    完成当前改动（插件 popup，标题 finish change）
prefix+alt+c    关闭当前任务：只关窗口 / 清理 worktree
```

安装/修复：

```bash
herdr-change install-keys
herdr server reload-config
```

## 最简用法（日常只用 3 条）

```bash
herdr-change task "把 IRQ 采集改成 forced-read"   # 建改动 + 起 agent + 发任务 + 切过去
herdr-change review <id>                          # 看 diff / reviewr 评论
herdr-change done <id>                            # checkpoint + push
# 或 herdr-change done <id> --pr                   # checkpoint + push + 开 PR
```

其它命令都是可选的；不想记就直接 `herdr-change task` / `review` / `done`。

## 完整用法

```bash
# 1. 新建改动：worktree + workspace + agent + reviewr
herdr-change task "把 IRQ 采集改成 forced-read 方案" --repo ~/emb/workspace/QP/QP-GD26-0901 --agent codex

# 多仓库改动（同一 change，多个 worktree）
herdr-change new "跨仓改动" --repo ~/proj/fw --repo ~/proj/host --no-agent

# 2. 状态
herdr-change list
herdr-change show <id>
herdr-change diff <id>

# 3. 给 Agent 指令 / 捕获回复
herdr-change prompt <id> "补上单元测试" --wait
herdr-change capture <id>          # 把当前 agent 输出存成 turn
herdr-change transcript <id>       # 浏览/搜索改动记录

# 4. 审查
herdr-change review <id> --reviewer --agent codex
herdr-change comment <id> "这里要判空" --file main.c --line 42
herdr-change comment <id> --send
herdr-change review <id> --verdict approved

# 5. checkpoint / 回退
herdr-change checkpoint <id> -m "wip: IRQ"
herdr-change checkpoint <id> --list
herdr-change checkpoint <id> --restore latest

# 6. 落地
herdr-change land <id> --strategy push
herdr-change land <id> --strategy pr
herdr-change land <id> --strategy merge --into main
herdr-change land <id> --strategy merge-pr --method squash
herdr-change land <id> --strategy hook

# 批量
herdr-change land --all --strategy push --dry-run
herdr-change land --all --strategy push --yes --cleanup

# 7. PR / CI
herdr-change pr <id> --watch
herdr-change sync --pr
herdr-change watch --daemon --interval 60
herdr-change watch --status
herdr-change watch --stop

# 8. 生命周期 / 审查包 / 本地 Web
herdr-change children <id>
herdr-change resume <id>
herdr-change rename <id> "new label"
herdr-change delete <id> --keep-worktree
herdr-change guide <id>                 # review guide markdown
herdr-change share <id> --review        # 独立 review HTML
herdr-change serve --daemon --token secret              # http://127.0.0.1:8765
herdr-change serve --tls-cert cert.pem --tls-key key.pem # https

# 9. 清理 / 恢复
herdr-change cleanup <id>
herdr-change recover --create      # herdr 重启/workspace 丢失后重新绑定
herdr-change prune --workspaces --dry-run
```

## 一个改动 = 什么

```
change
├── 1..N 个 git worktree（多仓库）
├── herdr workspace（主仓）
├── agent pane
├── reviewr pane
├── checkpoints / turns / comments / attachments（SQLite）
└── PR / CI 状态（change_repos）
```

对话本体仍归 Agent 自己的 session；`herdr-change` 只保存可搜索的 turn 摘要、评论和编排元数据。

## 命令

| 命令 | 作用 |
|---|---|
| `new` | 建 worktree/workspace/agent/reviewr；`--repo` 可重复；`--profile/--role/--run-hooks` |
| `list` | 状态总览；`--tag --search --bookmarked --all --json` |
| `show` / `repos` | 改动详情 / 多仓路径列表；`--json` |
| `diff` | 所有 repo 的 `base..HEAD` + 未提交 diff |
| `setup` | 在 worktree 跑 `.agents/prepare` / `.agents/setup` |
| `prompt` | 给 Agent 发指令；`--wait` |
| `capture` | 读取 agent 输出并写入 turns |
| `transcript` | 浏览 turns；`--search --bookmarked` |
| `turn` | `--add/--list/--bookmark/--edit/--revert/--reseed` |
| `search` | 跨改动搜索 turns |
| `review` | 打开 reviewr；`--reviewer` 起只读审查 agent；`--verdict`；`--sync-reviewr` |
| `comment` | 新增 / `--list` / `--send [--to]` / `--resolve` / `--import` / `--export` / `--from-clipboard` / `--from-reviewr` |
| `attach` | 文件或 `--text` 附件复制到 worktree `.herdr-change/attachments` |
| `send` | 把文本/transcript 发给另一个改动的 agent |
| `children` | 子改动树 |
| `resume` | 重新绑定/聚焦改动，必要时启动 agent |
| `rename` / `delete` / `restore-change` | 改动生命周期 |
| `mention` / `openfile` | 附件并提示 Agent / 解析 worktree 文件 |
| `guide` | 生成 review guide（commits/files/reviewer notes） |
| `share --review` | 生成独立 review HTML |
| `depend` | 改动依赖；`land` 会检查/拓扑排序 |
| `check-remote` | 对比本地 branch 与 origin |
| `subagent follow` | `follow <parent> <sub>` 聚焦并读子 Agent |
| `terminal` | 改动级终端 list/new/run/close |
| `serve` | 本地 Web dashboard / inbox；POST 写评论/note；`--token` / `--tls-cert` / `--tls-key`；`user` 管理 token |
| `config` | show/path/set，支持 `roles.foo.kind` 这类嵌套 key |
| `delta` | 别名：open/change/worktree/auth(login/logout/status)/integration |
| `restore-db` | 从 backup 恢复 changes.db |
| `checkpoint` | 提交 / `--list` / `--restore`，每个 repo 单独记录 |
| `land` | push/pr/merge/merge-pr/local/hook；批量；dirty/verdict/pre-land/deps/emb gate；`--emb-finish` |
| `pr` | 查看 / `--watch` PR + CI；GitHub/GitLab/Azure（需 gh/glab/az） |
| `sync` | 刷新 agent 状态；`--rebase`（先 fetch）；`--pr` |
| `watch` | 后台轮询；`--daemon/--status/--stop` |
| `fork` | 从当前 HEAD 建子改动；`--with-context/--from-turn` 注入 transcript；`--clone-session` fork pi/codex session |
| `subagent` | 独立改动或 `--in-place` 同 worktree 子 Agent；`--list` |
| `join` | 收集子 Agent 输出并回传父 Agent |
| `open` | 聚焦 workspace |
| `tag` | 标签 / 收藏 / 归档 |
| `cleanup` / `close-here` | 移除所有 repo 的 worktree + workspace；`--window` 只关窗口 |
| `recover` | 重新绑定 workspace/pane/agent；`--create/--start/--prune` |
| `emb` | emb-agent 项目桥接：`status` / `adopt` |
| `adopt` | 接管已有 worktree（不新建） |
| `machines` | 列出 herdr SSH machines |
| `remote` | 透传远端命令：`remote mini agent list` |
| `mirror` / `remote-changes` | 拉取远端 herdr-change 列表到本地 DB 并查看 |
| `user` | serve 的多用户 token：add/list/remove |
| `runner` | add/up/down/destroy/bootstrap/forward/preview/unforward/urls/env/push-env/snapshot |
| `share` | 打印远程 attach / 协作说明 |
| `notify` | 桌面通知 |
| `backup` | 备份 changes.db |
| `prune` | 清理孤儿 change/worktree/workspace |
| `doctor` | 检查 herdr/git/gh/reviewr/DB |

## 安全门

`land` 默认执行：

1. 所有 repo 的 dirty 检查（`--allow-dirty` 跳过）
2. rejected 审查阻断；`require_approval=true` 时要求 approved（`--force` 跳过）
3. pre-land 检查：
   - `.agents/pre-land`
   - `.herdr-change/pre-land.sh`
   - `pre_land_script`
   - `pre_land_commands`（shell）
   - `--skip-checks` 跳过

## 多仓库

```bash
herdr-change new "跨仓" --repo ~/fw --repo ~/host
```

- 第一个 repo 由 herdr 建 workspace；其余用 `git worktree add`
- `checkpoint/diff/land/cleanup` 遍历全部 repo
- `change_repos` 保存每个 repo 的 branch、base、PR、CI
- `land --strategy pr` 每个 repo 各开一个 PR（按 origin 自动选 gh/glab/az）

## 子 Agent

```bash
# 同 worktree，split pane
herdr-change subagent <id> --profile scout --in-place --prompt "只读调研"

# 独立 worktree/change
herdr-change subagent <id> --profile worker --prompt "实现 X"

herdr-change subagent <id> --list
herdr-change join <id> 1
```

`join` 等待子 Agent idle，读取输出，写到父 worktree `.herdr-change/subagents/`，并把路径发给父 Agent。

## 配置

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
prompt = "你是审查子 Agent。只读 diff，按文件列风险、遗漏测试和回退建议。"
```

`land --strategy hook` 执行 `~/.config/herdr-change/land.sh`，环境变量：

```
CHANGE_ID CHANGE_LABEL REPO WORKTREE BRANCH REPOS
```

`runner` 使用 `~/.config/herdr-change/runner-up.sh` / `runner-down.sh`，环境变量
`RUNNER_NAME RUNNER_HOST RUNNER_REPO`。

## reviewr 插件桥接

本机 reviewr 已打补丁：每次添加/编辑/删除/发送评论时，会把评论写到
`~/.cache/herdr-reviewr/<worktree-hash>.json`。同步：

```bash
herdr-change comment <id> --from-reviewr
herdr-change review <id> --sync-reviewr
```

补丁源码提交在 reviewr 插件仓库本地分支 `1746108`；`bin/herdr-reviewr` 已替换为新构建。

## emb-agent 项目

`.emb-agent` 项目里：

- `prefix+t` 会自动接管当前 emb task（不显示新建任务输入框）
- `herdr-change task` 仍会拒绝，避免误建第二套 worktree
- `prefix+alt+d` 会在 land 后自动 `--emb-finish`

手动桥接：

```bash
cd ~/emb/workspace/QP/QP-GD26-0901
herdr-change emb status
herdr-change emb adopt              # 接管当前 active task 的 emb worktree
herdr-change emb adopt --no-agent   # 只占位，不起 agent
```

`emb adopt` 会：

- 读取 active task
- 调 `emb-agent task activate --worktree`（没有 worktree 时）
- `herdr worktree open` 接管已有 worktree
- 在 herdr-change DB 记录 `emb_task`

`cleanup` 会先调 `emb-agent task worktree cleanup`，不会绕过 emb。

`land` 前会检查 emb gate：如果 active task 还在 `verify`，会直接阻断：

```text
emb verification pending ... run emb verify (or --skip-emb-check)
```

验证通过后可选择：

```bash
herdr-change land <id> --emb-finish --emb-disposition no-update --emb-summary "..."
```

`--emb-finish` 会在 land 后调用 `emb-agent finish-work` 回写 journal。

## 本地等价物

- Web/Inbox：`herdr-change serve` 本地 dashboard，读取同一 SQLite
- 共享 review：`share --review` 生成独立 HTML bundle
- Cloud Runner：`runner` + SSH/脚本 + env/preview/snapshot，见 [RUNNER.zh-CN.md](RUNNER.zh-CN.md)
- CRDT：不引入；以 SQLite 单写 + serve 轮询保证一致性
- 对话编辑/回退：`turn --edit/--revert --reseed` 重写 canonical context 文件并通知 Agent

## 开发

```bash
python3 -m py_compile herdr_change.py
tests/smoke.sh
```

`tests/smoke.sh` 使用隔离 DB/config/worktree，覆盖批量 land、多仓、checkpoint、turns、PR、recover。
