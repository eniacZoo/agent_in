# agent_in v2.0 — 功能增强规划

## 概述

在 v1.0（基础 Agent 循环 + 4 个工具）基础上，增加三大能力：

| # | 功能 | 核心价值 |
|---|------|----------|
| 1 | Skill 体系 | 任务完成后沉淀为可复用技能，避免重复劳动 |
| 2 | Memory 体系 | 持久化用户偏好/规则/经验，跨会话保持一致 |
| 3 | 状态日志 | 连接监控 + 上下文预警 + 全链路日志，便于排障 |

---

## 1. Skill 体系

### 1.1 概念

Skill = 一项**可复用的能力单元**，由以下部分组成：
- **元数据**：名称、描述、触发条件、参数 schema
- **执行脚本**：完成任务所需的 Python/Shell 脚本
- **使用示例**：调用示例 + 预期输出

### 1.2 目录结构

```
agent_in/
├── skills/                    # 所有 skill 存放处
│   ├── <skill_name>/
│   │   ├── skill.json         # 元数据 + 参数定义
│   │   ├── main.py            # 主执行脚本（或 .sh）
│   │   ├── helpers.py         # 辅助模块（可选）
│   │   └── README.md          # 人类可读说明（可选）
│   ├── another_skill/
│   │   ├── skill.json
│   │   └── run.sh
│   └── ...
```

### 1.3 skill.json 格式

```json
{
  "name": "pdf_to_text",
  "version": "1.0",
  "description": "将 PDF 文件转为纯文本，支持多页、表格提取",
  "trigger": "当用户要求读取/解析/转换 PDF 文件时",
  "created": "2026-09-09",
  "source_task": "将 xxx.pdf 转为文本",
  "entry": "main.py",
  "language": "python",
  "params": {
    "input_file": {"type": "string", "required": true, "description": "PDF 文件路径"},
    "output_file": {"type": "string", "required": false, "description": "输出 txt 路径（默认同目录）"}
  },
  "example": {
    "call": {"input_file": "report.pdf", "output_file": "report.txt"},
    "output": "提取了 12 页文本，共 3,456 字"
  }
}
```

### 1.4 Agent 交互流程

**保存 Skill：**
```
用户: "把刚才的操作保存为 skill，名字叫 pdf_convert"
Agent: [分析本会话中执行的操作和生成的文件]
Agent: [写入 skills/pdf_convert/ 目录]
Agent: ✓ 已保存 skill "pdf_convert"，下次可直接调用
```

**调用 Skill：**
- Agent 启动时扫描 `skills/` 目录，加载所有 `skill.json`
- 将每个 skill 注册为一个 **动态工具**（与 read_file 等同级）
- LLM 可根据 skill 的 `trigger` 描述自主判断何时调用
- 执行方式：`subprocess` 调用 entry 脚本，传入参数

**管理 Skill：**
```
/ls skills          # 列出所有 skill
/use pdf_convert    # 手动触发某 skill
/del skill_name     # 删除 skill（需确认）
```

### 1.5 实现要点

| 项 | 方案 |
|----|------|
| 自动识别 | 用户说"保存为 skill"时，agent 回顾本轮使用的工具调用和生成的文件 |
| 动态注册 | `tools.py` 启动时扫描 `skills/`，将每个 skill 包装成 OpenAI function 格式 |
| 执行隔离 | skill 脚本在 `WORK_DIR` 下运行，超时 120s，stdout/stderr 捕获 |
| 参数传递 | 通过 JSON 命令行参数传入：`python main.py --args '{"input_file": "..."}'` |
| 错误处理 | skill 执行失败 → 返回 stderr 给 LLM → LLM 可修复或报告用户 |

### 1.6 新增文件

```
agent_in/
├── skills/           # (运行时生成)
├── skill_manager.py  # skill 扫描/注册/执行/保存/删除
```

### 1.7 代码修改

- `tools.py`：`execute()` 增加 skill 分支；启动时调用 `skill_manager.load_all()` 动态注册
- `agent.py`：新增 `/ls skills`、`/use`、`/del` 命令；"保存为 skill" 意图识别
- `skill_manager.py`：新建

---

## 2. Memory 体系

### 2.1 概念

Memory = Agent 的**长期记忆**，跨会话持久化，包含：
- **用户偏好**：命名风格、代码规范、输出格式
- **规则约束**：「不要做 X」「必须用 Y」
- **项目上下文**：技术栈、端口、路径约定
- **经验教训**：踩过的坑、最佳实践

### 2.2 存储格式

```
agent_in/
├── memory/
│   ├── MEMORY.md          # 主记忆文件（人可读、agent 可编辑）
│   └── sessions/          # 会话快照（可选，自动记录关键决策）
│       ├── 2026-09-09.md
│       └── ...
```

**MEMORY.md 结构：**
```markdown
# Agent Memory

## 用户偏好
- 代码注释用中文
- 文件命名用 snake_case
- 回复要简洁，不要寒暄

## 规则
- 不要直接修改 src/ 下的文件，先备份
- shell 命令超时设为 30s
- 输出路径固定在 output/ 目录

## 项目上下文
- Python 路径: /opt/szclaw/Python-3.12.12/bin/python3.12
- 默认工作目录: /home/xxx/projects
- 测试框架: pytest

## 经验教训
- [2026-09-09] Windows 下路径分隔符要用 os.path.join，不能硬编码 /
- [2026-09-10] 大文件读取要先检查 size，避免撑爆上下文
```

### 2.3 Agent 交互流程

**写入 Memory：**
```
用户: "记住：以后生成的代码都要加 type hints"
Agent: [追加到 MEMORY.md → 用户偏好 section]
Agent: ✓ 已记录
```

**主动建议：**
```
Agent 完成任务后发现一个坑 → 
Agent: "我注意到 Windows 下 subprocess 需要 shell=True 才能用管道，已记入经验。"
```

**读取 Memory：**
- Agent 每次启动时读取 `MEMORY.md`，拼入 system prompt
- 如果 MEMORY.md 超过 4K 字符，只取最近 4K（或按 section 优先级截取）

### 2.4 记忆管理命令

```
/memory              # 查看当前 memory 内容
/memory add "规则内容"  # 手动追加
/memory del "关键词"   # 删除包含关键词的行
/memory clear        # 清空（需确认）
```

### 2.5 实现要点

| 项 | 方案 |
|----|------|
| 存储 | 单文件 `MEMORY.md`（Markdown 格式，方便人编辑） |
| 读取时机 | 每次 `agent.py` 启动时 → 注入 system prompt |
| 写入时机 | ① 用户明确说"记住" ② agent 判断有价值时主动建议 ③ `/memory add` 命令 |
| 容量控制 | 超过 8K 字符时提醒用户清理；超过 16K 时截断最旧内容 |
| 格式约束 | 4 个固定 section，agent 写入时归入对应 section |
| 安全 | 不记录密码/token/敏感信息（system prompt 中明确告知 agent） |

### 2.6 新增文件

```
agent_in/
├── memory/
│   └── MEMORY.md         # 初始为空模板
├── memory_manager.py     # 读取/追加/删除/容量控制
```

### 2.7 代码修改

- `memory_manager.py`：新建（`load()`, `append(section, text)`, `delete(keyword)`, `clear()`）
- `agent.py`：启动时 `system_prompt += memory_manager.load()`；新增 `/memory` 系列命令
- `llm.py`：无改动（memory 拼入 system prompt 即可）

---

## 3. 状态检测 & 日志

### 3.1 目标

解决三个排障痛点：
1. **连接异常**：API 无响应 / 超时 / 返回 502，用户只知道"卡了"
2. **上下文溢出**：不知道什么时候 token 会超限导致报错
3. **全链路追溯**：出了问题不知道是 LLM 调用 / 工具执行 / 网络 哪一环

### 3.2 日志文件

```
agent_in/
├── logs/
│   ├── agent_2026-09-09.log    # 当日日志
│   ├── agent_2026-09-10.log
│   └── ...
```

### 3.3 日志格式（每行一个事件，JSON Lines）

```json
{"ts": "2026-09-09T11:30:01.123", "level": "INFO", "event": "session_start", "data": {"session_id": "abc12345", "model": "AngelOrDevil"}}
{"ts": "2026-09-09T11:30:01.456", "level": "INFO", "event": "llm_request", "data": {"turn": 1, "prompt_tokens": 925, "messages_count": 3}}
{"ts": "2026-09-09T11:30:02.789", "level": "INFO", "event": "llm_response", "data": {"turn": 1, "elapsed_ms": 1333, "completion_tokens": 39, "finish_reason": "stop"}}
{"ts": "2026-09-09T11:30:02.800", "level": "INFO", "event": "tool_call", "data": {"name": "shell", "args": {"command": "ls -la"}}}
{"ts": "2026-09-09T11:30:02.900", "level": "INFO", "event": "tool_result", "data": {"name": "shell", "exit_code": 0, "elapsed_ms": 100, "output_size": 256}}
{"ts": "2026-09-09T11:30:05.000", "level": "WARN", "event": "context_warning", "data": {"used_tokens": 150000, "limit": 196000, "percent": 77}}
{"ts": "2026-09-09T11:30:10.000", "level": "ERROR", "event": "llm_timeout", "data": {"timeout_s": 600, "turn": 3, "last_activity": "stream_idle"}}
{"ts": "2026-09-09T11:30:10.001", "level": "ERROR", "event": "connection_lost", "data": {"error": "HTTP 502: Bad Gateway"}}
```

### 3.4 事件类型枚举

| event | level | 触发条件 |
|-------|-------|----------|
| `session_start` | INFO | 会话开始 |
| `session_end` | INFO | 会话结束（含统计） |
| `llm_request` | INFO | 发送 LLM 请求 |
| `llm_response` | INFO | 收到完整 LLM 响应 |
| `llm_stream_chunk` | DEBUG | 每个流式 chunk（可选，默认关） |
| `llm_timeout` | ERROR | 请求超时 |
| `llm_error` | ERROR | HTTP 错误 / JSON 解析失败 |
| `tool_call` | INFO | 工具调用 |
| `tool_result` | INFO | 工具执行完成 |
| `tool_error` | WARN | 工具执行失败/超时 |
| `context_warning` | WARN | 上下文使用 > 70% |
| `context_critical` | ERROR | 上下文使用 > 90% |
| `context_overflow` | ERROR | 上下文超出模型限制 |
| `connection_ok` | INFO | 连通性检查通过 |
| `connection_lost` | ERROR | 连接断开/服务端无响应 |
| `skill_loaded` | INFO | Skill 加载 |
| `skill_executed` | INFO | Skill 执行完成 |
| `memory_loaded` | INFO | Memory 加载（字符数） |
| `memory_written` | INFO | Memory 写入 |

### 3.5 上下文监控

```python
# 在 agent_loop 每轮结束后检查
CONTEXT_LIMIT = 196_000  # AngelOrDevil 的上下文限制

def check_context(used_tokens, limit=CONTEXT_LIMIT):
    percent = used_tokens / limit * 100
    if percent > 90:
        log("ERROR", "context_overflow", {...})
        # → 向用户提示 "上下文即将用尽，建议 /new 开新会话"
    elif percent > 70:
        log("WARN", "context_warning", {...})
        # → 终端显示黄色提醒
```

**上下文用量估算：**
- 精确值：从 API 响应的 `usage.prompt_tokens` 获取
- 本地估算：`len(json.dumps(messages)) / 3`（粗略）
- 每轮取最新 `prompt_tokens` 作为当前占用

### 3.6 连接监控

```python
# 在 llm.py 的 chat() 中
- 请求前：log("llm_request")
- 首 token 到达：log 首包延迟（time_to_first_token）
- 流结束：log 总延迟
- 异常：log("llm_error" / "llm_timeout")

# 连续失败检测
consecutive_failures = 0
# 每次请求成功 → 归零
# 每次失败 → +1
# 连续 3 次失败 → 终端红色警告 + log("connection_lost")
```

### 3.7 终端实时提示

| 状态 | 终端表现 |
|------|----------|
| 正常 | 无额外输出 |
| 上下文 > 70% | 黄色：`⚠️ 上下文已用 72%（141K/196K）` |
| 上下文 > 90% | 红色：`🚨 上下文即将用尽（93%），建议 /new` |
| 连接超时 | 红色：`🔴 连接超时 (60s)，正在重试...` |
| 连续 3 次失败 | 红色：`🔴 连续 3 次连接失败，API 可能不可用` |
| 工具执行慢 (>10s) | 黄色：`⏳ shell 执行中... 已耗时 12s` |

### 3.8 实现要点

| 项 | 方案 |
|----|------|
| 日志库 | 自写 50 行轻量 logger（不引入 logging 模块的复杂性） |
| 日志级别 | 环境变量 `LOG_LEVEL`：`DEBUG` / `INFO`（默认）/ `WARN` / `ERROR` |
| 日志轮转 | 按天分文件；单文件 > 10MB 时自动归档为 `.gz` |
| 性能 | 日志写入用 `append` 模式，不 flush（进程退出时 flush） |
| 上下文跟踪 | `agent.py` 维护 `context_used` 变量，每轮从 usage 更新 |
| 连接健康 | `llm.py` 内部维护 `consecutive_failures` 计数器 |

### 3.9 新增文件

```
agent_in/
├── logs/               # (运行时生成)
├── logger.py           # 轻量日志模块
```

### 3.10 代码修改

- `logger.py`：新建（`log(level, event, data)`, 文件写入 + 终端警告）
- `llm.py`：请求/响应/错误处插桩；维护 `consecutive_failures`
- `agent.py`：上下文监控；启动时初始化日志；`/logs` 命令查看最近日志
- `tools.py`：工具执行耗时记录

---

## 开发顺序

### Phase A：Memory（最简单，1 个文件 + 少量修改）

- [ ] 创建 `memory_manager.py`（load / append / delete / clear）
- [ ] 创建 `memory/MEMORY.md` 初始模板
- [ ] `agent.py` 启动时加载 memory 拼入 system prompt
- [ ] 新增 `/memory`、`/memory add`、`/memory del` 命令
- [ ] Agent 识别"记住"/"以后都..."等意图 → 自动写入
- [ ] 验证：跨两次会话，第二次能记住第一次的偏好

### Phase B：日志（独立模块，不改变核心行为）

- [ ] 创建 `logger.py`（JSON Lines 写文件 + 终端 warning/error 输出）
- [ ] `llm.py` 插桩：request/response/error/timeout
- [ ] `agent.py` 插桩：session_start/end、context 监控
- [ ] `tools.py` 插桩：tool_call/tool_result/tool_error
- [ ] 上下文 > 70%/90% 终端提醒
- [ ] 连续失败 > 3 次红色警告
- [ ] 新增 `/logs` 命令（查看最近 20 条日志）
- [ ] 验证：运行一次完整任务 → 检查 `logs/agent_*.log` 内容

### Phase C：Skill（最复杂，需要动态注册机制）

- [ ] 创建 `skill_manager.py`
  - `scan_skills()` → 扫描 `skills/*/skill.json`
  - `to_tool_schema(skill)` → 转为 OpenAI function 格式
  - `execute_skill(name, params)` → subprocess 执行 entry 脚本
  - `save_skill(name, description, files)` → 保存新 skill
- [ ] `tools.py` 修改：`TOOLS` 变为动态列表（基础 4 个 + 已加载 skills）
- [ ] `agent.py` 新增：
  - "保存为 skill" 意图识别 → 收集本会话产物 → 调用 `save_skill()`
  - `/ls skills`、`/use <name>`、`/del <name>` 命令
- [ ] Skill 执行超时（120s）+ 错误回传
- [ ] 验证：
  - 手动放一个 skill → agent 能识别并调用
  - 完成一个任务 → "保存为 skill" → 新开会话能调用该 skill

---

## 项目最终结构（v2.0）

```
agent_in/
├── agent.py             # 主入口（+ memory 加载 / skill 命令 / 日志初始化）
├── llm.py               # LLM 客户端（+ 日志插桩 / 连接健康跟踪）
├── tools.py             # 基础工具（+ skill 动态注册）
├── ui.py                # 终端 UI（+ 上下文/连接警告）
├── skill_manager.py     # Skill 管理（新建）
├── memory_manager.py    # Memory 管理（新建）
├── logger.py            # 日志模块（新建）
├── plan.md              # v1.0 规划
├── plan2.0.md           # 本文件
├── README.md            # 使用说明（Phase C 完成后写）
├── skills/              # 技能目录（运行时）
├── memory/
│   └── MEMORY.md        # 长期记忆
├── logs/                # 日志目录（运行时）
└── chat_3.8.py          # 原始基础（可删）
```

---

## 风险 & 注意

| 项 | 风险 | 应对 |
|----|------|------|
| Skill 注入 | 恶意 skill.json 可能包含危险脚本 | skill 执行前显示 entry 路径 + 首行代码，需确认 |
| Memory 膨胀 | 用户不断"记住" → system prompt 膨胀 → 吃上下文 | 8K 上限 + 优先级截断（规则 > 偏好 > 经验） |
| 日志磁盘 | 高频任务 → 日志增长快 | 按天分 + 10MB 归档 + `LOG_LEVEL=ERROR` 只记异常 |
| 上下文估算不准 | token 计数有误差（多语言混合） | 以 API 返回的 `prompt_tokens` 为准，本地估算仅兜底 |
| Windows 路径 | skill 脚本路径、日志路径 | 统一用 `pathlib.Path` |

---

## 成功标准

```bash
# Memory
$ python agent.py
> 记住：代码注释一律用英文
> ✓ 已记录到 memory
> /quit

$ python agent.py        # 新会话
> 写一个 hello.py
→ 生成的代码注释是英文的 ✓

# Skill
$ python agent.py "把 report.pdf 转成文本"
→ (执行完毕)
> 保存为 skill
> ✓ skill "pdf_convert" 已保存
> /quit

$ python agent.py "帮我解析 data.pdf"
→ Agent 自动调用 skill "pdf_convert" ✓

# 日志
$ python agent.py "列一下文件"
→ (执行完毕)
$ cat logs/agent_$(date +%F).log
[{"ts":"...","level":"INFO","event":"session_start",...},
 {"ts":"...","level":"INFO","event":"llm_request",...},
 {"ts":"...","level":"INFO","event":"tool_call","data":{"name":"shell",...}},
 {"ts":"...","level":"INFO","event":"session_end",...}]
```
