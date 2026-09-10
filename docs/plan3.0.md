# agent_in v3.0 — 功能增强规划

## 概述

在 v2.0（Agent 循环 + 工具 + Skill + Memory + 日志）基础上，增加两项体验与能力升级：

| # | 功能 | 核心价值 |
|---|------|----------|
| 1 | 流式过程回显 | 执行过程中实时展示思考内容与代码生成内容，消除"黑盒等待"感 |
| 2 | 多模态图像识别 | Agent 能"看图"——用户丢一张图片即可描述、OCR、分析架构图 |

> ## ✅ 实现状态总览（2026-09-09）
> **两项功能全部完成并通过端到端验证。**
>
> - **功能 1 流式回显**：`SHOW_REASONING` 默认 1、reasoning 斜体、代码预览块、
>   `Spinner`、`COLOR`/`STREAM_CODE`/`SPINNER` 环境变量全部落地（Phase A A1–A7 ✅）。
> - **功能 2 多模态**：`vision.py` 编码 + `view_image` 工具 + `llm.py` `_inject_images()`
>   + `agent.py` `--image`/自动识别 + 每轮图片注入主循环，全链路打通（Phase B B1–B8 ✅）。
>   实测 `AngelOrDevil` **原生支持 vision**（无需 `VISION_MODEL` 降级）；
>   `python agent.py -i _e2e_red.png "这是什么颜色？"` → 正确回答"纯红色"（1.5s）。
> - 遗留：B9 截图工具集成（P2 可选）暂缓。

---

## 1. 流式过程回显（Thinking & Code Streaming）

### 1.1 目标

当前体验问题：
- 用户输入任务后，终端长时间"静默"（只有工具调用完成才输出）
- 用户不知道 Agent 在想什么、在写什么代码
- 代码生成（write_file/edit_file）完成后才看到结果，无法"看着 Agent 写代码"

**期望体验：**
```
  ── thinking ──────────────────────────────────────────────
  🧠 用户要求创建 utils.py，我需要...
     先确认目录结构，然后实现 reverse_string 函数...
     函数签名：def reverse_string(s: str) -> str
     边界情况：空字符串、Unicode...

  ── ai ────────────────────────────────────────────────────
  🤖 我来帮你创建。先看看目录结构...

  🔧 shell( ls src/ )
     src/
     (empty)

  ── thinking ──────────────────────────────────────────────
  🧠 目录是空的，直接创建 utils.py...

  ── ai ────────────────────────────────────────────────────
  🤖 创建 utils.py：

  ┌─ 📝 src/utils.py ─────────────────────────────────────
  │  1  """String utilities."""
  │  2
  │  3  def reverse_string(s: str) -> str:
  │  4      """Return the reversed string."""
  │  5      return s[::-1]
  │  6
  │  7  def count_vowels(s: str) -> int:
  │  8      vowels = "aeiouAEIOU"
  │  9      return sum(1 for c in s if c in vowels)
  └────────────────────────────────────────────────────────

  🔧 write_file( src/utils.py (9 lines) )
     ✓ 已写入 156 bytes

  ── ai ────────────────────────────────────────────────────
  🤖 完成。src/utils.py 已创建，包含两个函数...
```

### 1.2 功能拆解

| 子功能 | 说明 |
|--------|------|
| A. Thinking 流式展示 | reasoning chunks 逐字显示（灰色斜体），默认开启（`SHOW_REASONING=1`） |
| B. 代码生成预览 | LLM 生成 `write_file`/`edit_file` 时，在工具实际执行**前**以代码块样式流式展示文件内容 |
| C. 代码块高亮 | 根据文件扩展名选择语法色（.py→蓝/绿，.sh→紫，.json→黄等） |
| D. 进度指示器 | 长耗时操作（>2s）显示旋转 spinner `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` + 已耗时 |
| E. 可配置 | `STREAM_CODE=1`（默认开）/ `STREAM_THINK=1`（默认开）/ `COLOR=auto` |

### 1.3 技术方案

#### A. Thinking 流式展示（改动最小）

当前 `ui.py` 的 `StreamDisplay` 已支持 `show_reasoning=True` 时逐 chunk 打印 reasoning。

**改动点：**
- `agent.py`：`SHOW_REASONING` 默认值从 `0` 改为 `1`
- `ui.py`：`StreamDisplay.handle()` 中 reasoning 部分加 `\\033[3m`（斜体）区分
- 新增 `STREAM_THINK` 环境变量（`0` 可关闭），兼容旧的 `SHOW_REASONING`

```python
# ui.py StreamDisplay 改进
def handle(self, chunk):
    if ctype == "reasoning":
        if self.show_reasoning:
            if not self._in_reasoning:
                self._in_reasoning = True
                turn_sep("thinking")
                print(f"  🧠 ", end="", flush=True)
            # 斜体 + 灰色，逐字 flush
            print(f"\033[3m{_color(chunk['content'], C_GRAY)}\033[0m", end="", flush=True)
```

#### B. 代码生成预览（核心新功能）

**原理：** LLM 在流式输出 tool_call 时，`write_file` 的 `content` 参数会分片到达（SSE 流式）。我们在**收到完整 arguments 后、工具执行前**，先以代码块样式展示内容。

**关键问题：** 当前 `llm.py` 中 tool_call 的 arguments 是拼接完毕后一次性 yield 的（`pending_tool_calls` 缓冲）。要实现"边生成边预览"，需要改造：

**方案 1：工具执行前预览（推荐，改动小）**
- 不改 `llm.py`（仍等 arguments 拼完再 yield）
- 在 `agent.py` 的工具执行环节，`write_file`/`edit_file` 执行前调用 `ui.preview_code(path, content)`
- 优点：实现简单，不影响流式解析逻辑
- 缺点：预览是"瞬间出现"而非逐字打字效果（因为 content 已经完整了）

**方案 2：流式预览（体验最好，改动大）**
- 改造 `llm.py`：`_chat_stream()` 中对 `write_file`/`edit_file` 的 arguments 分片，实时 yield 新 chunk type `{"type": "code_preview", "path": "...", "delta": "..."}`
- `ui.py` 新增处理 `code_preview` chunk：逐字打印到代码块中
- 优点：真正的"看着 Agent 打字写代码"
- 缺点：需要改 SSE 解析逻辑，复杂度增加

**v3.0 选择方案 1**（实用优先），方案 2 作为 v3.1 优化项。

```python
# ui.py 新增
def preview_code(path: str, content: str, max_lines: int = 40):
    """工具执行前，以代码块样式展示文件内容。"""
    ext = os.path.splitext(path)[1].lower()
    color = _EXT_COLORS.get(ext, "")
    
    lines = content.split("\n")
    shown = lines[:max_lines]
    total = len(lines)
    
    # 标题栏
    title = f" 📝 {path} "
    bar_w = W - len(title) - 4
    print(f"  ┌─{title}{'─' * bar_w}")
    
    # 代码行（带行号）
    for i, line in enumerate(shown, 1):
        line_display = line.rstrip()
        if color:
            line_display = f"{color}{line_display}\033[0m"
        print(f"  │{i:>4}  {line_display}")
    
    if total > max_lines:
        print(f"  │      ... ({total - max_lines} more lines)")
    print(f"  └{'─' * (W - 2)}")
    print()
```

```python
# agent.py 工具执行环节改造
for tc in tool_calls:
    name = tc["name"]
    args = tc["arguments"]
    
    # 代码预览
    if verbose and STREAM_CODE and name in ("write_file", "edit_file"):
        if name == "write_file":
            ui.preview_code(args.get("path", ""), args.get("content", ""))
        elif name == "edit_file":
            # edit_file 只展示 old→new 的 diff
            ui.preview_edit(
                args.get("path", ""),
                args.get("old_text", ""),
                args.get("new_text", "")
            )
    
    # 执行工具
    result = tools.execute(name, args)
    ...
```

#### C. 代码块语法高亮（轻量版）

不引入外部高亮库，用简易颜色映射：

```python
_EXT_COLORS = {
    ".py":  "\033[34m",    # 蓝
    ".js":  "\033[33m",    # 黄
    ".ts":  "\033[35m",    # 紫
    ".sh":  "\033[35m",    # 紫
    ".bash":"\033[35m",
    ".json":"\033[33m",    # 黄
    ".md":  "\033[37m",    # 白
    ".html":"\033[36m",    # 青
    ".css": "\033[36m",
    ".yml": "\033[32m",    # 绿
    ".yaml":"\033[32m",
    ".sql": "\033[31m",    # 红
}
```

> 注意：这里是对**整行**着色（不是 token 级别），视觉上已足够区分。完整 token 级高亮（如关键字加粗）留到后续版本。

#### D. 进度指示器

工具执行超过 2 秒时，显示 spinner：

```python
# ui.py 新增
class Spinner:
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    def __init__(self, text="执行中"):
        self.text = text
        self._stop = False
        self._thread = None
    
    def __enter__(self):
        import threading
        self._t0 = time.time()
        def spin():
            i = 0
            while not self._stop:
                frame = self.FRAMES[i % len(self.FRAMES)]
                elapsed = time.time() - self._t0
                if elapsed > 2:  # 2s 后才显示
                    sys.stdout.write(f"\r  {frame} {self.text} ({elapsed:.0f}s)")
                    sys.stdout.flush()
                i += 1
                time.sleep(0.1)
        self._thread = threading.Thread(target=spin, daemon=True)
        self._thread.start()
        return self
    
    def __exit__(self, *args):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=0.2)
        # 清除 spinner 行
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()
```

```python
# agent.py 使用
if verbose:
    with ui.Spinner(f"shell: {args['command'][:30]}"):
        result = tools.execute(name, args)
else:
    result = tools.execute(name, args)
```

### 1.4 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STREAM_THINK` | `1` | 是否流式显示 thinking（兼容 `SHOW_REASONING`） |
| `STREAM_CODE` | `1` | 是否在工具执行前预览代码内容 |
| `SPINNER` | `1` | 是否显示进度 spinner |
| `COLOR` | `auto` | `auto` / `always` / `never`（控制 ANSI 颜色） |

### 1.5 新增/修改文件

```
agent_in/
├── ui.py          # 修改：preview_code() / preview_edit() / Spinner / 代码高亮
├── agent.py       # 修改：工具执行前调用 preview_code；SHOW_REASONING 默认改 1
└── llm.py         # 无改动（方案 1 不需要）
```

### 1.6 开发任务

- [x] `ui.py`：新增 `preview_code(path, content, max_lines=40)` — 代码块展示
- [x] `ui.py`：新增 `preview_edit(path, old_text, new_text)` — diff 样式展示（`-` 红色 / `+` 绿色）
- [x] `ui.py`：新增 `_EXT_COLORS` 字典 + 文件扩展名颜色映射
- [x] `ui.py`：新增 `Spinner` 类（threading 实现，2s 延迟显示）
- [x] `ui.py`：`StreamDisplay` 改进 — reasoning 加斜体
- [x] `ui.py`：`COLOR=never` 时禁用所有 ANSI
- [x] `agent.py`：`SHOW_REASONING` 默认值改 `1`；新增 `STREAM_CODE` / `SPINNER` 环境变量
- [x] `agent.py`：`write_file` 执行前调用 `ui.preview_code()`
- [x] `agent.py`：`edit_file` 执行前调用 `ui.preview_edit()`
- [x] `agent.py`：工具执行包裹 `Spinner`（仅 verbose 模式）
- [x] 验证：执行 "创建 hello.py" 任务 → 终端能看到 thinking 流 + 代码预览块 + spinner

---

## 2. 多模态图像识别

### 2.1 目标

让 Agent 能"看图"：
- 用户提供图片路径 → Agent 描述图片内容
- 用户截图 → Agent 分析 UI 界面 / 架构图 / 代码截图
- 表格图片 → Agent 提取数据
- 配合 `shell` 工具截图（`screencapture` / `gnome-screenshot` / PowerShell）

**典型交互：**
```
用户: 看看这张图里写了什么 /tmp/screenshot.png
Agent: [view_image /tmp/screenshot.png]
       → 图片已加载（1280x720 PNG, 342KB）
Agent: 这是一张系统架构图，包含三个服务：
       1. 前端 (React) → 2. API Gateway → 3. 后端 (Go)
       数据库使用 PostgreSQL，缓存用 Redis...

用户: 帮我看看终端截图里报了什么错
Agent: [shell: gnome-screenshot -f /tmp/term.png]
       [view_image /tmp/term.png]
       → 识别到错误: TypeError: Cannot read property 'map' of undefined
       位置: App.tsx:42，数组变量可能为 null...
```

### 2.2 技术方案

#### 核心：OpenAI Vision API 格式

OpenAI 兼容 API 的多模态消息格式：
```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "这张图里写了什么？"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
  ]
}
```

**关键改动：** `messages` 中 user 消息的 `content` 从纯字符串变为**数组**（mixed content）。

#### 2.2.1 图片编码

```python
# 新增 vision.py（或合并到 tools.py）
import base64
import os
from pathlib import Path

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB 上限

def image_to_base64(path: str) -> str:
    """读取图片文件，返回 base64 编码字符串。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {path}")
    if p.suffix.lower() not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的图片格式: {p.suffix}（支持: {', '.join(SUPPORTED_EXTS)}）")
    size = p.stat().st_size
    if size > MAX_IMAGE_SIZE:
        raise ValueError(f"图片过大: {size / 1024 / 1024:.1f}MB（上限 10MB）")
    
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    mime = _guess_mime(p.suffix)
    return f"data:{mime};base64,{b64}"

def _guess_mime(ext: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext.lower(), "image/png")
```

#### 2.2.2 新增工具：`view_image`

```python
# tools.py 新增
VIEW_IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "view_image",
        "description": "Load an image file for visual analysis. Use when the user asks to look at / describe / OCR / analyze an image. Returns a confirmation that the image has been loaded into context.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the image file (absolute or relative to WORK_DIR)"
                }
            },
            "required": ["path"]
        }
    }
}
```

**执行逻辑：**
```python
def _execute_view_image(args):
    path = args["path"]
    # 解析到绝对路径
    full_path = _resolve(path)
    # 编码
    data_url = image_to_base64(full_path)
    # 存入全局 context（供 llm.py 构建多模态 messages 时使用）
    _pending_image = data_url
    return f"图片已加载: {os.path.basename(full_path)} ({os.path.getsize(full_path)//1024}KB)。我已看到这张图，可以继续分析。"
```

#### 2.2.3 LLM 请求改造

**核心改动：** `llm.py` 的 `chat()` 需要支持 messages 中 content 为数组的情况。

```python
# llm.py 改动
def chat(messages, tools=None, stream=True, model=None, max_tokens=None, images=None):
    """
    新增参数:
      images: list[str] — base64 data URL 列表，拼接到最后一条 user message
    """
    # 如果有图片，将最后一条 user message 的 content 改为数组格式
    if images:
        messages = _inject_images(messages, images)
    ...

def _inject_images(messages, images):
    """将图片注入最后一条 user message（OpenAI vision 格式）。"""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            # 将 content 转为数组
            original = messages[i].get("content", "")
            content_parts = [{"type": "text", "text": original if isinstance(original, str) else ""}]
            for img_url in images:
                content_parts.append({"type": "image_url", "image_url": {"url": img_url}})
            messages[i] = dict(messages[i])  # shallow copy
            messages[i]["content"] = content_parts
            break
    return messages
```

#### 2.2.4 Agent 主循环改造

```python
# agent.py 改动
_pending_images = []  # 本 session 待发送的图片

# 工具执行后，如果 view_image 成功，收集图片
for tc in tool_calls:
    name = tc["name"]
    args = tc["arguments"]
    result = tools.execute(name, args)
    
    # view_image 特殊处理：收集图片供下一轮 LLM 调用使用
    if name == "view_image":
        _pending_images.append(result)  # result 是 data_url
    ...

# 下一轮 LLM 调用时传入图片
chunks = llm.chat(full_messages, tools=tools.TOOLS, images=_pending_images)
_pending_images = []  # 发送后清空
```

#### 2.2.5 用户直接提供图片

**交互模式：**
```
用户: [拖拽/粘贴图片路径] 帮我看看这张图
Agent: 检测到图片 /tmp/photo.jpg，已加载...
```

**CLI 模式：**
```bash
python agent.py --image /tmp/screenshot.png "这张架构图里有什么组件？"
```

**实现：**
```python
# agent.py main()
parser.add_argument("--image", "-i", action="append", default=[],
                    help="提供图片文件路径（可多次指定）")

# 启动时加载
images = [image_to_base64(p) for p in args.image]
# 拼入首条 user message
user_content = [{"type": "text", "text": task}, ...]
for img in images:
    user_content.append({"type": "image_url", "image_url": {"url": img}})
```

### 2.3 模型要求

| 项 | 说明 |
|----|------|
| 模型支持 | 当前 `AngelOrDevil` 需确认是否支持 vision（`image_url` content type） |
| 降级策略 | 如果模型不支持 vision → `view_image` 返回警告 "当前模型不支持图像分析，请切换支持多模态的模型" |
| 模型切换 | 支持 `--model vision-model` 参数切换多模态模型 |
| 上下文消耗 | 一张 1024x1024 图片 ≈ 1000-1500 tokens（具体取决于模型分块策略） |

### 2.4 安全 & 限制

| 项 | 策略 |
|----|------|
| 图片大小 | 单张 ≤ 10MB，一次请求 ≤ 4 张图 |
| 文件格式 | 仅 PNG/JPG/GIF/WebP/BMP（拒绝 SVG/EXIF 可执行格式） |
| 路径限制 | 默认限 WORK_DIR 内；`--allow-any-path` 可放开 |
| 隐私 | 图片 base64 仅发送给 LLM API，不写入日志 |
| 日志 | `logger.info("image_loaded", {"path": basename, "size_kb": N})` 不记录 base64 内容 |

### 2.5 新增/修改文件

```
agent_in/
├── vision.py        # 新建：图片编码、格式校验、base64 工具
├── tools.py         # 修改：新增 view_image 工具定义 + 执行逻辑
├── llm.py           # 修改：chat() 支持 images 参数；_inject_images()
├── agent.py         # 修改：--image CLI 参数；_pending_images 管理
├── ui.py            # 修改：图片加载状态提示（📷 已加载 xxx.png 342KB）
└── plan3.0.md       # 本文件
```

### 2.6 开发任务

- [x] 确认 `AngelOrDevil` 模型是否支持 vision（发一张测试图验证）→ **支持，无需 VISION_MODEL**
- [x] 如果不支持 → 确定替代多模态模型 + 环境变量 `VISION_MODEL`（**未触发，模型原生支持**）
- [x] `vision.py`：新建（`image_to_base64()` / 格式校验 / 大小检查）
- [x] `tools.py`：新增 `view_image` 工具 schema + `_exec_view_image()`
- [x] `llm.py`：`chat()` 新增 `images` 参数；`_inject_images()` 函数
- [x] `agent.py`：
  - `--image` / `-i` CLI 参数
  - `_pending_images` 生命周期管理（每轮 `drain_pending_images()` 注入 `llm.chat(images=)`）
  - `view_image` 工具结果 → 自动收集到 `PENDING_IMAGES` → 下一轮注入
- [x] `ui.py`：图片加载提示样式 `📷 photo.png (342KB) ✓`（`print_image_loaded` 已在主循环接线）
- [x] System prompt 更新：告知 Agent 有 `view_image` 工具可用
- [x] 验证：
  - [x] `python agent.py -i _e2e_red.png "这是什么颜色？"` → 正确回答"纯红色"（1.5s）
  - [x] 交互模式：自动 `guess_image_files(prompt)` 检测图片路径并注入
  - [x] 不支持的格式 → 友好报错（`_test_vision.py` 覆盖）
  - [x] 超大文件/不存在 → 友好报错，不污染缓冲（`_test_vision.py` 覆盖）

---

## 开发顺序

### Phase A：流式回显（1-2 天）

> ✅ **Phase A 已完成（2026-09-09）**，见 1.6 开发任务清单。

| 步骤 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| A1 | `ui.py` 代码块 `preview_code()` + 扩展名颜色 | P0 | ✅ |
| A2 | `ui.py` `preview_edit()` diff 展示 | P0 | ✅ |
| A3 | `agent.py` write_file/edit_file 执行前调用 preview | P0 | ✅ |
| A4 | `SHOW_REASONING` 默认改 1 + reasoning 斜体 | P0 | ✅ |
| A5 | `ui.py` `Spinner` 类 | P1 | ✅ |
| A6 | `agent.py` 工具执行包裹 Spinner | P1 | ✅ |
| A7 | `COLOR` 环境变量 | P2 | ✅ |

**验收标准：**
```bash
$ python agent.py "在 test/ 下创建一个 calculator.py，实现加减乘除"

  ── thinking ──────────────────────────────────────────────
  🧠 *用户要一个计算器，需要实现 add/subtract/multiply/divide...*

  ── ai ────────────────────────────────────────────────────
  🤖 创建 calculator.py：

  ┌─ 📝 test/calculator.py ─────────────────────────────────
  │   1  """Simple calculator module."""
  │   2
  │   3  def add(a: float, b: float) -> float:
  │   4      return a + b
  │   ...
  └────────────────────────────────────────────────────────

  🔧 write_file( test/calculator.py (24 lines) )
     ✓ 已写入 412 bytes
```

### Phase B：多模态图像识别（2-3 天）

> ✅ **Phase B 已完成（2026-09-09）**：B1–B8 全部落地并通过端到端验证
> （`python agent.py -i _e2e_red.png "这是什么颜色？"` → 正确回答"纯红色"，1.5s）。
> B9 为可选增强，暂缓。

| 步骤 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| B1 | 确认模型 vision 支持（发测试请求） | P0 | ✅ `AngelOrDevil` 实测支持 |
| B2 | `vision.py` 图片编码模块 | P0 | ✅ |
| B3 | `tools.py` `view_image` 工具 | P0 | ✅ |
| B4 | `llm.py` 多模态消息格式支持 | P0 | ✅ `_inject_images()` + `chat(images=)` |
| B5 | `agent.py` `--image` CLI 参数 | P0 | ✅ `-i/--image` + 交互模式自动识别 |
| B6 | `agent.py` `_pending_images` 管理 | P1 | ✅ 每轮 `drain_pending_images()` 注入 |
| B7 | `ui.py` 图片状态提示 | P1 | ✅ `print_image_loaded` 已在主循环接线 |
| B8 | System prompt 更新 | P1 | ✅ 增加 `view_image` 工具说明 |
| B9 | 截图工具集成（`shell` 调 `screencapture`） | P2 | ⏸ 可选，暂缓 |

**验收标准：**
```bash
# CLI 模式
$ python agent.py --image ./screenshot.png "这张图里有什么错误？"
  📷 screenshot.png (2.1MB) ✓
  ── thinking ──────────────────────────────────────────────
  🧠 *图片是一个 IDE 截图，右侧有红色错误标记...*
  ── ai ────────────────────────────────────────────────────
  🤖 图片中显示了 3 个错误：
     1. Line 12: NameError - name 'foo' is not defined
     2. Line 28: TypeError - unsupported operand
     3. Line 45: IndentationError
     建议修复顺序...

# 交互模式
$ python agent.py
> 帮我看看 /tmp/arch_diagram.png 这张架构图
  📷 arch_diagram.png (856KB) ✓
  🤖 这是一个微服务架构，包含...
```

---

## 项目最终结构（v3.0）

```
agent_in/
├── agent.py             # 主入口（+ 代码预览 / spinner / --image / pending_images）
├── llm.py               # LLM 客户端（+ images 参数 / _inject_images）
├── tools.py             # 工具（+ view_image）
├── ui.py                # 终端 UI（+ preview_code / preview_edit / Spinner / 高亮）
├── vision.py            # 图片处理（新建）
├── skill_manager.py     # Skill 管理
├── memory_manager.py    # Memory 管理
├── logger.py            # 日志模块
├── plan.md              # v1.0 规划
├── plan2.0.md           # v2.0 规划
├── plan3.0.md           # 本文件
├── README.md            # 使用说明
├── skills/              # 技能目录（运行时）
├── memory/
│   └── MEMORY.md
├── logs/                # 日志目录（运行时）
└── chat_3.8.py          # 原始基础（可删）
```

---

## 风险 & 注意

| 项 | 风险 | 应对 |
|----|------|------|
| 模型不支持 vision | `AngelOrDevil` 可能是纯文本模型 | 启动时检测；`VISION_MODEL` 环境变量指定多模态模型；优雅降级 |
| 图片 token 消耗大 | 一张高清图可占 1500+ tokens → 加速上下文消耗 | 4 张图上限 + 上下文监控联动（图片 token 计入 usage） |
| 代码预览刷屏 | 大文件 write_file（1000+ 行）→ 终端被代码淹没 | `max_lines=40` 截断 + 提示 "完整内容见文件" |
| Spinner 线程安全 | 多线程下 stdout 竞争 | spinner 线程 daemon=True + 退出时 join；与 StreamDisplay 不同时输出 |
| Base64 内存 | 10MB 图片 base64 后 ≈ 13MB 字符串 | 10MB 硬限 + 用完即释放（`_pending_images` 发送后清空） |
| Python 3.8 兼容 | 不能用 walrus / match / f-string `=` 等新语法 | 同 v2.0 约束 |

---

## 成功标准

```bash
# 功能 1：流式回显
$ python agent.py "创建一个 web 服务器，支持 GET/POST，写个测试"
# 应看到：
#   1. 🧠 thinking 灰色斜体实时输出
#   2. 📝 代码块带行号 + 语法颜色预览
#   3. ⠙ spinner 在 shell 执行时旋转
#   4. 所有输出是流式的，不是"等完了才出现"

# 功能 2：多模态
$ python agent.py -i ./error_screenshot.png "这个报错怎么修？"
# 应看到：
#   1. 📷 图片加载确认
#   2. Agent 正确识别图中的错误信息
#   3. 给出具体修复建议

# 交互模式
$ python agent.py
> 看看这张图 [粘贴 /tmp/chart.png]
> 这个柱状图说明了什么？
# → Agent 描述图表内容
```
