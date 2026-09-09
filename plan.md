# agent_in — 极简 CLI Agent 项目规划

## 目标

以 `chat_3.8.py`（OpenAI 兼容客户端）为起点，构建一个**跨平台**（Windows / Linux / macOS）的极简 CLI Agent，支持：

- 与 LLM 多轮对话（OpenAI 兼容 API）
- 调用本地工具：`read_file`、`write_file`、`edit_file`、`shell`
- Agent 循环：LLM 自主决定何时调用工具 → 执行 → 将结果回传 → 继续推理，直到输出最终答案
- 零外部依赖（仅 Python 标准库）

## 设计原则

| 原则 | 说明 |
|------|------|
| 极简 | 核心逻辑 ≤ 800 行，不引入框架/SDK |
| 零依赖 | 只用 stdlib：`urllib`、`json`、`subprocess`、`pathlib`、`os`、`time`、`uuid`、`shutil` |
| 跨平台 | 文件操作统一 `pathlib`；shell 自动检测（bash / PowerShell / cmd） |
| 可插拔 | 通过环境变量 / 配置文件切换 API、模型 |
| 安全 | 破坏性操作（rm、删除目录等）执行前需用户确认 |

## 项目结构

```
agent_in/
├── plan.md              # 本文件
├── README.md            # 使用说明（最终交付时写）
├── agent.py             # 主入口：CLI 解析 + Agent 循环
├── llm.py               # LLM 客户端：OpenAI 兼容、流式、tool calling
├── tools.py             # 工具定义 + 执行器（read/write/edit/shell）
├── ui.py                # 终端 UI：框线、流式输出、确认提示
└── chat_3.8.py          # 原始基础（保留参考，后续可删除）
```

## 模块设计

### 1. `llm.py` — LLM 客户端

从 `chat_3.8.py` 的 HTTP 请求逻辑演进，增加 **function calling** 支持。

```python
# 核心接口
def chat(messages, tools=None, stream=True) -> Iterator[Chunk]
```

**Chunk 结构：**
```python
{
    "type": "reasoning" | "text" | "tool_call" | "usage",
    "content": str,          # reasoning 或 text
    "tool_call": {           # 仅 type=tool_call 时
        "id": "call_xxx",
        "name": "read_file",
        "arguments": {"path": "/tmp/foo.txt"}
    },
    "usage": {"prompt_tokens": N, "completion_tokens": M}  # 仅最后
}
```

**关键实现点：**
- `POST {BASE_URL}/chat/completions`
- `stream: true` + `stream_options: {"include_usage": true}`
- 请求体含 `tools: [...]`（OpenAI function calling 格式）
- 解析 SSE 流：
  - `delta.content` → 文本输出
  - `delta.reasoning_content`（或 `.thinking`）→ 思考过程
  - `delta.tool_calls[].function.name/arguments` → 工具调用（arguments 可能分片，需拼接）
  - 最终 chunk 的 `usage` → token 统计
- 超时：600s（大上下文场景）

**环境变量：**
| 变量 | 默认值 |
|------|--------|
| `BASE_URL` | `http://118.4.78.6:8088/api/v1` |
| `API_KEY` | `sk-RKndn...` (内置) |
| `MODEL` | `AngelOrDevil` |
| `SYSTEM_PROMPT` | agent system prompt（见下） |
| `MAX_TOKENS` | `8192` |
| `SHOW_REASONING` | `0` |
| `WORK_DIR` | 当前目录 |

### 2. `tools.py` — 工具定义与执行

**4 个工具，OpenAI function calling schema：**

#### `read_file`
```json
{
  "name": "read_file",
  "description": "Read the content of a file. Use start_line/end_line for partial reads.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "File path (absolute or relative to WORK_DIR)"},
      "start_line": {"type": "integer", "description": "First line (1-based, optional)"},
      "end_line": {"type": "integer", "description": "Last line (1-based, optional)"}
    },
    "required": ["path"]
  }
}
```

#### `write_file`
```json
{
  "name": "write_file",
  "description": "Create or overwrite a file with the given content.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "File path"},
      "content": {"type": "string", "description": "Content to write"}
    },
    "required": ["path", "content"]
  }
}
```

#### `edit_file`
```json
{
  "name": "edit_file",
  "description": "Find-and-replace in a file. Replaces ALL occurrences of old_text with new_text.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "old_text": {"type": "string", "description": "Exact text to find"},
      "new_text": {"type": "string", "description": "Replacement text"}
    },
    "required": ["path", "old_text", "new_text"]
  }
}
```

#### `shell`
```json
{
  "name": "shell",
  "description": "Execute a shell command. Returns stdout, stderr, and exit code. Use for running scripts, git, grep, ls, etc.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {"type": "string", "description": "Shell command to execute"},
      "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"}
    },
    "required": ["command"]
  }
}
```

**执行逻辑：**

```python
def execute(tool_name: str, args: dict) -> str:
    """执行工具，返回字符串结果（喂回给 LLM）"""
```

- 路径统一 resolve 到 `WORK_DIR`
- `read_file`：加行号输出（`  1: line content`），超过 200 行截断并提示
- `write_file`：自动创建父目录
- `edit_file`：校验 old_text 存在（否则报错），替换后返回修改行上下文
- `shell`：
  - 自动检测 shell：`shutil.which("bash")` → bash；Windows 无 bash 时用 `powershell -Command`
  - 超时 kill
  - 输出限制 10K 字符（防刷屏）
  - 危险命令检测（`rm -rf`、`del /s`、`format` 等）→ 需用户确认

**安全规则：**
- `shell` 中的 `rm -rf`、`del /s`、`Remove-Item -Recurse -Force`、`format` → 打印命令并询问 `[y/N]`
- 文件操作超出 `WORK_DIR` 时警告（不阻止，除非配置 `SAFE_MODE=1`）

### 3. `ui.py` — 终端 UI

从 `chat_3.8.py` 的框线/流式输出逻辑抽取。

**职责：**
- `turn_header(turn)` / `turn_footer(usage, elapsed)` — 轮次框
- `print_stream(chunks)` — 流式输出（reasoning 灰色 / 文本白色 / 工具调用高亮）
- `print_tool_call(name, args)` — 显示 "🔧 read_file(path=/tmp/x)"
- `print_tool_result(result, truncated=False)` — 缩进显示结果
- `confirm(prompt)` — 用户确认（y/N）
- `banner()` / `summary()` — 启动横幅 / 结束汇总

**颜色（可选增强，用 ANSI 转义，Windows 10+ 原生支持）：**
- reasoning → 灰色 `\033[90m`
- 工具名 → 青色 `\033[36m`
- 错误 → 红色 `\033[31m`
- 正常 → 默认

### 4. `agent.py` — 主入口 + Agent 循环

**CLI 接口：**
```bash
python agent.py                    # 交互模式
python agent.py "帮我看看当前目录"   # 单次任务
python agent.py -w /path/to/dir "任务"  # 指定工作目录
python agent.py --model xxx "任务"   # 临时切换模型
```

**Agent 循环（核心逻辑）：**

```python
def agent_loop(initial_prompt, work_dir):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    if initial_prompt:
        messages.append({"role": "user", "content": initial_prompt})
    
    while True:
        # 1. 调用 LLM
        chunks = llm.chat(messages, tools=TOOLS)
        
        # 2. 流式展示 + 收集 tool_calls
        text = ""
        tool_calls = []
        for chunk in chunks:
            ui.display(chunk)
            if chunk.type == "text":
                text += chunk.content
            elif chunk.type == "tool_call":
                tool_calls.append(chunk.tool_call)
        
        # 3. 无工具调用 → 最终回复，结束
        if not tool_calls:
            break
        
        # 4. 有工具调用 → 执行
        messages.append({"role": "assistant", "content": text or None, "tool_calls": [...]})
        for tc in tool_calls:
            result = tools.execute(tc.name, tc.arguments)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        
        # 5. 回到循环（LLM 继续思考）
```

**交互模式额外功能：**
- `/quit` 退出
- `/new` 新会话
- `/status` 查看 token 消耗
- `/history` 查看消息历史
- 每次工具执行后暂停（`[Enter]` 继续），让用户可以审查操作

**最大迭代：** 单次任务最多 20 轮工具调用（防死循环）

## 开发阶段

### Phase 1：骨架（chat_3.8.py 拆分 + llm.py tool calling）

- [ ] 拆分 `chat_3.8.py` → `llm.py` + `ui.py`
- [ ] `llm.py` 增加 `tools` 参数传递
- [ ] `llm.py` 解析流式 `tool_calls`（注意 arguments 分片拼接）
- [ ] `agent.py` 基本循环（无工具，纯对话，验证拆分无损）
- [ ] 验证：`python agent.py "你好"` 行为与 chat_3.8.py 一致

### Phase 2：工具实现

- [ ] `tools.py`：实现 `read_file`（含行号、截断）
- [ ] `tools.py`：实现 `write_file`（含 mkdir -p）
- [ ] `tools.py`：实现 `edit_file`（含存在性校验）
- [ ] `tools.py`：实现 `shell`（跨平台 shell 检测 + 超时 + 输出截断）
- [ ] 危险命令确认机制
- [ ] 验证：手动构造 tool_call JSON → 确认执行正确

### Phase 3：Agent 循环跑通

- [ ] `agent.py` 完整循环：LLM → tool_call → execute → 回传 → LLM
- [ ] 流式展示工具调用过程（`🔧 read_file(/path) → 25 lines`）
- [ ] 多工具调用支持（LLM 一次返回多个 tool_calls）
- [ ] 迭代上限 + 异常处理（工具报错 → 告知 LLM → 继续）
- [ ] E2E 测试：
  - "列出当前目录文件" → 应调 shell `ls` / `dir`
  - "读一下 xxx.py 的前 20 行" → 应调 read_file
  - "创建一个 hello.py 打印 hello" → 应调 write_file
  - "把 hello.py 里的 hello 改成 world" → 应调 edit_file

### Phase 4：打磨 + Windows 验证

- [ ] 跨平台 shell 检测（bash / PowerShell / cmd）
- [ ] Windows 路径处理（`C:\Users\...` vs `/c/Users/...`）
- [ ] ANSI 颜色兼容（Windows 10+ `os.system('')` 激活 VT100）
- [ ] README.md
- [ ] 环境变量 / 命令行参数文档
- [ ] 在 Windows 上实际验证（如用户有环境）

## System Prompt 模板

```
你是一个极简 CLI Agent，运行在用户的本地机器上。
你可以使用以下工具来完成任务：read_file、write_file、edit_file、shell。

规则：
1. 简洁高效，不要寒暄
2. 先理解再行动：必要时先 read_file / shell(ls, cat) 了解现状
3. 文件路径相对于工作目录：{work_dir}
4. 修改文件前先看内容（read_file），避免盲目覆盖
5. shell 命令注意跨平台（用户可能在 Windows）
6. 完成后简要总结做了什么
```

## 风险与注意事项

| 风险 | 应对 |
|------|------|
| AngelOrDevil 模型 function calling 兼容性 | Phase 1 结束时先测；如果不支持 `tools` 参数，降级为 ReAct 文本协议（"Tool: read_file\nArgs: {...}"） |
| 流式 tool_call arguments 分片 | 按 `tool_call index` 拼接 JSON string，全部收齐后 parse |
| Windows 无 bash | 检测顺序：`bash` → `pwsh` → `powershell` → `cmd /c` |
| 大文件读取撑爆上下文 | read_file 默认最多 500 行，超出截断 + 提示 |
| 死循环（LLM 反复调同一工具） | 20 轮上限 + 重复调用检测（同 name+args 连续 3 次 → 终止） |

## 成功标准

```bash
# 一句话完成跨文件操作
python agent.py "在 src/ 下创建一个 utils.py，实现一个 reverse_string 函数，然后写个 test.py 测试它，最后运行测试"

# Agent 应自动：
#   1. shell: ls src/          (确认目录存在)
#   2. write_file: src/utils.py
#   3. write_file: src/test.py
#   4. shell: python src/test.py
#   5. 输出测试通过确认
```
