# claude-nightshift 中文文档

**额度感知的 Claude Code 自动工作流系统。** 查额度、起床前预热 5 小时窗口、
睡前排队任务、限额一解除自动续跑被打断的会话。

零第三方依赖（Python 3.10+ 标准库），Windows 优先（任务计划程序集成），
核心命令在任何能跑 Claude Code 的平台都可用。

## 解决什么问题

Claude 订阅按**滚动 5 小时窗口**计量，窗口从你的**第一条消息**开始计时，
而不是从你坐下干活开始。这造成三种可挽回的浪费：

1. **窗口开始得太晚。** 9 点起床，9:05 发第一条消息，窗口 9:05–14:05。
   如果 7:00 有个定时 ping 先把窗口激活，窗口 12:00 就重置——而重度用户
   一般用 3 小时左右就撞限额了。等于每天早上白捡约 2 小时额度。
2. **夜里的额度全浪费。** 23:00 被限额，01:00 就重置，但没人醒着用。
   睡前把任务排进队列，runner 在额度恢复的瞬间无头执行。
3. **被打断的任务原地卡住。** 限额把会话拦腰斩断，任务就一直停在那里。
   nightshift 从会话 transcript 里检测到中断，额度一恢复自动续跑。

## 安装

```
git clone https://github.com/dujunyi416/claude-nightshift
cd claude-nightshift
python -m nightshift status
```

前提：本机安装了 [Claude Code](https://code.claude.com) 且已用 Pro/Max
订阅登录（nightshift 读取 CLI 自己的本地 OAuth 凭据，除了 Anthropic
官方用量端点外不向任何地方发送数据）。

> Windows 提示：如果裸 `python` 指向商店占位符，请用完整路径，例如
> `C:\Users\<你>\AppData\Local\Programs\Python\Python312\python.exe -m nightshift ...`
> 注册的计划任务内部已固化完整路径，无需担心。

可选 `pip install -e .` 获得全局 `nightshift` 命令。

## 命令一览

| 命令 | 作用 |
|---|---|
| `nightshift status` | 5h / 7d 额度百分比 + 重置倒计时 |
| `nightshift warmup` | 发一条极小的 haiku ping 激活 5h 窗口（已有活跃窗口则跳过） |
| `nightshift add "提示词" --cwd 目录` | 把任务加入队列 |
| `nightshift queue` / `remove ID` | 查看 / 删除队列任务 |
| `nightshift run` | 等额度恢复后依次无头执行队列 |
| `nightshift resume` | 找出并续跑被限额打断的会话 |
| `nightshift watch` | 全自动驾驶：额度一允许就自动续跑中断会话 + 清空队列 |
| `nightshift learn` | 分析你的作息节律，建议预热时间 |
| `nightshift schedule --warmup 07:00` | 注册每日 Windows 计划任务（可从睡眠唤醒电脑） |
| `nightshift schedule --auto` | 从历史记录学习预热时间并注册 |
| `nightshift unschedule` | 移除所有 nightshift 计划任务 |
| `nightshift config` | 查看配置文件路径和当前配置 |

## 三大工作流

### 1. 早晨预热（省 2 小时）

```
nightshift schedule --warmup 07:00     # 或自动学习: nightshift schedule --auto
```

注册一个每日 07:00 的计划任务（已启用"从睡眠唤醒"），发送一条最小的
haiku 提示。成本：约等于零。效果：5h 窗口变成 07:00–12:00 而不是
09:00–14:00 —— 你刚要撞墙的时候新窗口已经就位。
`--auto` 会分析你最近 30 天的会话记录自动推算时间
（`nightshift learn` 可单独查看分析结果，含每小时活跃度直方图）。

注意：电脑需处于**睡眠**状态才能被唤醒；彻底关机无法唤醒。

### 2. 睡前排队

```
nightshift add "按 TODO 重构 data/loader.py，跑测试并修复失败" --cwd D:\myproject
nightshift add "给 utils/ 下所有公开函数写 docstring" --cwd D:\myproject
nightshift run        # 终端开着别关，去睡觉
```

`run` 轮询额度；窗口一重置就在各任务自己的目录里执行
`claude -p <提示词>`，完整输出存档到 `~/.nightshift/done/`（失败进
`failed/`），可选 Telegram 通知。任务中途撞限额会**重新排队**等下个窗口，
不会被标记为失败。

实用参数：`--model opus` 指定模型、`--priority 1` 优先执行、
`--timeout 120` 单任务超时（分钟）、`add -` 从 stdin 读多行提示词。

### 3. 中断自动续跑（watch 自动驾驶）

```
nightshift watch
```

限额切断会话时，Claude Code 会在 transcript 里写入一条
`isApiErrorMessage` 记录（"You've hit your session limit · resets 4am…"）。
`watch` 扫描最后一个事件是这种中断的会话，额度一恢复就用
`claude -p --resume <会话ID>` 在原目录续跑——然后清空提示词队列。
状态文件保证同一次中断只续跑一次；如果续跑又撞了限额，下个窗口接着来，
长任务就这样一个窗口一个窗口往前爬，直到完成。

睡前直接开 `nightshift watch` = 三个功能合体：白天被打断的活儿夜里自动
续上 + 排队任务自动跑。

## 额度数据从哪来

两个来源，按序降级：

1. **OAuth 用量端点**——`GET https://api.anthropic.com/api/oauth/usage`，
   Bearer token 取自 `~/.claude/.credentials.json`，加
   `anthropic-beta: oauth-2025-04-20` 头。返回 `five_hour` / `seven_day`
   的 `utilization`（%）和 `resets_at`。这正是 claude-hud 一类状态栏工具
   的后备数据源（社区发现的未文档端点）。默认缓存 60 秒；出错时退回
   旧缓存而不是直接挂掉。
2. **statusline 快照**（可选，零网络）——Claude Code 会把 `rate_limits`
   通过 stdin 喂给 statusline 命令。在 `~/.claude/settings.json` 加：

   ```json
   {"statusLine": {"type": "command", "command": "python -m nightshift statusline"}}
   ```

   即可在终端里看到简洁状态行，同时每次更新都被捕获到
   `~/.nightshift/usage_snapshot.json` 作为后备额度源。

OAuth token 几小时过期一次，任何 `claude` 命令都会刷新它——warmup ping
顺便就干了这件事。nightshift 永远不写 Claude 的凭据文件。

## 配置

`nightshift config` 会生成并打印 `~/.nightshift/config.json`：

```jsonc
{
  "claude_cmd": "",                    // 留空 = 自动检测
  "warmup":  { "times": ["07:00"], "model": "haiku",
               "prompt": "Reply with exactly: ok", "skip_if_active": true },
  "runner":  { "start_when": "reset",  // 或 "now"
               "stop_utilization": 95, "job_timeout_min": 240,
               "permission_mode": "acceptEdits", "poll_interval_sec": 300 },
  "resume":  { "enabled": true, "lookback_hours": 24,
               "permission_mode": "acceptEdits", "max_sessions": 3,
               "prompt": "You were interrupted by the usage limit. Continue..." },
  "quota":   { "cache_ttl_sec": 60 },
  "telegram": { "bot_token": "", "chat_id": "" }   // 或环境变量 NIGHTSHIFT_TG_TOKEN / _CHAT
}
```

所有状态都在 `~/.nightshift/` 下（可用 `NIGHTSHIFT_HOME` 改）：
`queue/`、`done/`、`failed/`、`logs/`、`usage_cache.json`、`resumed.json`。

## 安全须知

- 队列任务和续跑默认用 `--permission-mode acceptEdits`：Claude 可以改
  任务目录里的文件，但危险 shell 命令会直接失败而不是卡住等确认。只有在
  完全信任所排提示词时才改 `bypassPermissions`——这是无人值守执行。
- 用量端点是**未文档**接口，随时可能变；但队列/runner/续跑/计划任务
  不依赖它也能工作——Claude CLI 自己会强制执行限额，nightshift 能从
  输出里检测到。
- warmup ping 成本约等于零，但它**确实**会启动一个 5h 窗口。如果你早上
  起床时间不固定，用 `--auto` 学习或把时间设晚一点，避免预热了一个你
  睡过去的窗口。

## 测试

```
python -m unittest discover -s tests
```

23 个单元测试覆盖额度解析、窗口状态机、队列排序/归档、中断检测
（含"已被人工续跑的会话不重复触发"等边界）、节律分析。

## License

MIT
