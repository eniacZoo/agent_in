# agent_in v2.0 — 办公技能集成（Office Skill Integration）

## 概述

在 v1.0（Agent 循环 + 5 个基础工具）+ v2.0（Skill 体系 + Memory + 日志）+ v3.0（流式回显 + 多模态）的基础上，**引入 szclaw 的办公类 Skill**，让 agent_in 具备 Office 文档全生命周期处理能力：

| Skill | 能力 | SKILL.md 大小 | 核心依赖 |
|-------|------|--------------|----------|
| **docx** | Word 文档创建/编辑/读取/转格式 | 17.6KB | python-docx, pandoc, soffice |
| **xlsx** | Excel 创建/编辑/公式/数据分析 | 12.0KB | openpyxl, pandas, soffice |
| **pptx** | PPT 创建/编辑/读取/缩略图 | 9.7KB | python-pptx, markitdown, PIL |
| **pdf** | PDF 读取/合并/拆分/创建/OCR | 8.6KB | pypdf, pdfplumber, reportlab, qpdf |
| **html** | HTML 页面创建/预览/转 PDF/抓取解析 | 新建(~6KB) | bs4, lxml, jinja2, soffice, pandoc |
| **wps-write** | 快速生成 Word 并唤起 WPS | 0.6KB | pandoc, wps |

> **注：** docx/xlsx/pptx/pdf/wps-write 来自 szclaw（复制+改写）；**html 为新建 skill**（szclaw 无现成的，本期从零编写）。

**核心价值：** 用户说"帮我写一份报告存成 Word"或"把这个 PDF 拆成 3 份"，Agent 直接完成，无需用户手动操作。

---

## 一、现状 & 约束

### 1.1 已有基础

```
agent_in/
├── agent.py           # 1012 行，主循环 + skill 命令（/ls skills, /use, /del）
├── llm.py             # OpenAI 兼容客户端，流式 + tool calling + vision
├── tools.py           # 500 行，5 个工具（read/write/edit/shell/view_image）
├── skill_manager.py   # 381 行，skill.json + entry 脚本格式
├── skill_scanner.py   # 静态安全扫描
├── memory_manager.py  # 长期记忆
├── logger.py          # JSON Lines 日志
├── ui.py              # 终端 UI（流式/代码预览/spinner）
├── vision.py          # 图片 base64
└── skills/            # (空，无 skill)
```

### 1.2 环境依赖（已验证 2026-09-09）

| 依赖 | 状态 | 用途 |
|------|------|------|
| Python 3.12 | ✅ `/opt/szclaw/Python-3.12.12/bin/python3.12` | 主运行环境 |
| openpyxl 3.1.5 | ✅ | xlsx 读写 |
| pandas 3.0.2 | ✅ | 数据分析 |
| pypdf 6.11.0 | ✅ | PDF 基本操作 |
| pdfplumber 0.11.9 | ✅ | PDF 文本/表格提取 |
| reportlab 4.5.1 | ✅ | PDF 创建 |
| python-docx 1.2.0 | ✅ | Word 创建/编辑（替代 npm docx） |
| python-pptx 1.0.2 | ✅ | PPT 创建/编辑（替代 npm pptxgenjs） |
| markitdown | ✅ | PPT 文本提取 |
| PIL 12.2.0 | ✅ | 图片/缩略图 |
| soffice | ✅ | 格式转换/公式重算 |
| pandoc | ✅ | 格式转换（md→docx 等） |
| pdftoppm/pdftotext | ✅ | PDF→图片/文本 |
| qpdf | ✅ | PDF 合并/拆分/解密 |
| wps | ✅ | 打开 Word |
| **node / npm** | ❌ **缺失** | docx/pptx npm 包不可用 → 用 Python 替代 |

### 1.3 核心约束

1. **node/npm 不可用** → szclaw 的 SKILL.md 中 npm 路径（`docx` npm 包 / `pptxgenjs`）需改写为 Python 路径（`python-docx` / `python-pptx`）
2. **Token 预算** → 5 个 SKILL.md 合计 ~15K tokens，不能全部塞 system prompt
3. **脚本路径** → szclaw skill 的 scripts/ 使用相对路径（`cd {skill_dir} && python scripts/...`），需适配
4. **Python 3.8 兼容** → 不能 walrus / match（沿用项目约束）
5. **零外部依赖原则** → 基础工具仍用 stdlib；Python 库（openpyxl 等）是 skill 的运行时依赖，不算 agent 框架依赖

---

## 二、架构设计

### 2.1 Skill 格式扩展：两种 Skill

当前 `skill_manager.py` 只支持 **Script Skill**（`skill.json` + entry 脚本）。
szclaw 的办公 skill 是 **Instruction Skill**（`SKILL.md` 指令文档 + 辅助脚本）。

```
skills/
├── _script_skills/           # 原有 Script Skill（skill.json 格式）
│   └── my_script_skill/
│       ├── skill.json
│       └── main.py
├── docx/                     # Instruction Skill（SKILL.md 格式）
│   ├── SKILL.md
│   ├── scripts/              # 辅助脚本
│   └── templates/            # XML 模板等
├── xlsx/
│   ├── SKILL.md
│   └── scripts/
├── pptx/
│   ├── SKILL.md
│   ├── editing.md            # 补充文档
│   ├── pptxgenjs.md          # (改用 python-pptx.md)
│   └── scripts/
├── pdf/
│   ├── SKILL.md
│   ├── REFERENCE.md          # 补充文档
│   └── FORMS.md
└── wps-write/
    ├── SKILL.md
    └── reference/
```

**检测规则：**
- 目录下有 `skill.json` → Script Skill（走现有 `skill_manager.execute_skill()`）
- 目录下有 `SKILL.md` → Instruction Skill（走新的 `skill_doc_loader`）

### 2.2 上下文注入策略（核心设计）

**原则：不在启动时全量注入，按需加载。**

```
┌─────────────────────────────────────────────────────────┐
│ System Prompt (固定)                                     │
│  - 基础工具说明                                          │
│  - Skill 索引 (~200 chars):                              │
│    "可用技能: docx(Word), xlsx(Excel), pptx(PPT),       │
│     pdf(PDF), wps-write(快速Word)。                       │
│     使用 load_skill 工具加载详细指令。"                     │
└─────────────────────────────────────────────────────────┘
                         │
                    用户输入
                         │
              ┌──────────┴──────────┐
              │ 关键词匹配？          │
              │ (docx/word → docx)   │
              └──────────┬──────────┘
                         │ 命中
              ┌──────────▼──────────┐
              │ 自动注入 SKILL.md    │  ← 作为 system 消息
              │ (17.6KB ≈ 5.5K tok) │     插入 messages
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │ LLM 自主调用          │
              │ load_skill("docx")   │  ← 兜底：LLM 觉得需要
              │ 可获取完整 SKILL.md   │
              └─────────────────────┘
```

#### 2.2.1 关键词触发映射

```python
SKILL_TRIGGERS = {
    "docx": ["word", "docx", "文档", "word文档", "报告", "备忘录", "letter",
             "公文", "通知", "制度", "方案"],
    "xlsx": ["excel", "xlsx", "表格", "电子表格", "spreadsheet", "csv",
             "数据表", "报表"],
    "pptx": ["ppt", "pptx", "幻灯片", "演示文稿", "presentation", "deck",
             "slides", "路演"],
    "pdf":  ["pdf", "合并pdf", "拆分pdf", "pdf转", "扫描件", "ocr",
             "pdf提取", "pdf水印"],
    "wps-write": ["wps", "快速写", "写个word", "存成word"],
}
```

#### 2.2.2 `load_skill` 工具（LLM 可主动调用）

```python
LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "Load the full instruction document (SKILL.md) for a skill. "
                       "Available skills: docx, xlsx, pptx, pdf, wps-write. "
                       "Call this before working with office documents.",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "enum": ["docx", "xlsx", "pptx", "pdf", "wps-write"],
                    "description": "The skill to load"
                }
            },
            "required": ["skill_name"]
        }
    }
}
```

**执行逻辑：**
- 读取 `skills/{skill_name}/SKILL.md`
- 返回内容（截断上限 20KB）
- 同时返回 `skill_dir` 路径（告诉 LLM scripts/ 在哪里）
- 记录日志

#### 2.2.3 自动注入时机

在 `agent_loop` 中，**每轮用户消息处理前**：

```python
def _maybe_inject_skill_doc(messages, user_text):
    """如果用户消息命中 skill 触发词，注入 SKILL.md 到 messages。"""
    for skill_name, keywords in SKILL_TRIGGERS.items():
        if any(kw in user_text.lower() for kw in keywords):
            # 检查是否已注入过（避免重复）
            if not _skill_injected(skill_name, messages):
                skill_doc = load_skill_doc(skill_name)
                # 注入为 system 消息（在首条 user 消息之前）
                inject_position = _find_inject_position(messages)
                messages.insert(inject_position, {
                    "role": "system",
                    "content": f"[SKILL: {skill_name}]\n"
                               f"Skill directory: {SKILLS_DIR / skill_name}\n"
                               f"{skill_doc}"
                })
            return skill_name
    return None
```

**防重复注入：** 用标记检测，如果 messages 中已有 `[SKILL: docx]` 则不再注入。

### 2.3 Path Resolution & 脚本调用

szclaw SKILL.md 中脚本调用模式：
```bash
cd {skill_dir} && python scripts/office/unpack.py document.docx unpacked/
python scripts/recalc.py output.xlsx
```

**适配方案：**
- `load_skill` 返回值中明确告知 `skill_dir` 路径
- LLM 用 `shell` 工具调用脚本时，命令格式：
  ```
  cd /path/to/agent_in/skills/docx && /opt/szclaw/Python-3.12.12/bin/python3.12 scripts/office/unpack.py ...
  ```
- SKILL.md 中的 `scripts/` 相对路径在 `cd {skill_dir}` 后自然生效
- 在 system prompt 的 skill 部分明确说明 Python 路径：
  ```
  Python 解释器: /opt/szclaw/Python-3.12.12/bin/python3.12
  ```

### 2.4 npm 依赖替代方案

**docx skill：**

| 操作 | 原方案 (npm) | 替代方案 (Python) |
|------|-------------|------------------|
| 新建 Word 文档 | `docx` npm 包 (JS 脚本) | `python-docx` |
| 读取/分析 | `pandoc` / `unpack.py` | 不变 |
| 编辑已有文档 | `unpack.py` → XML → `pack.py` | 不变 |
| .doc→.docx | `soffice.py` | 不变 |
| 验证 | `validate.py` | 不变 |

**改写 SKILL.md 的 Creating New Documents 部分：**

```markdown
## Creating New Documents (Python)

使用 python-docx 创建 .docx 文件：

```python
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

doc = Document()

# 页面设置
sections = doc.sections
for section in sections:
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)

# 标题
heading = doc.add_heading('标题文字', level=1)

# 段落
doc.add_paragraph('正文内容')

# 表格
table = doc.add_table(rows=2, cols=3)
table.style = 'Table Grid'
table.cell(0, 0).text = '表头1'

# 保存
doc.save('output.docx')
```

### 关键规则
- 用 `doc.add_heading(text, level=1)` 添加标题（不要用手动加粗）
- 列表用 `doc.add_paragraph('...', style='List Bullet')` / `'List Number'`
- 表格必须设 `style`，否则无边框
- 图片：`doc.add_picture('img.png', width=Inches(4))`
```

**pptx skill：**

| 操作 | 原方案 (npm) | 替代方案 (Python) |
|------|-------------|------------------|
| 新建 PPT | `pptxgenjs` (JS 脚本) | `python-pptx` |
| 读取/提取文本 | `markitdown` | 不变 |
| 编辑已有 PPT | `unpack.py` → XML → `pack.py` | 不变 |
| 缩略图 | `thumbnail.py` | 不变 |
| PPT→PDF | `soffice` | 不变 |

---

## 三、Skill 文件准备

### 3.1 目录结构（最终）

```
agent_in/skills/
├── docx/
│   ├── SKILL.md              # 改写后（npm→python-docx）
│   ├── scripts/
│   │   ├── office/
│   │   │   ├── unpack.py     # 从 szclaw 复制
│   │   │   ├── pack.py
│   │   │   ├── validate.py
│   │   │   └── soffice.py
│   │   ├── comment.py
│   │   ├── accept_changes.py
│   │   └── templates/        # XML 模板
│   └── deps.txt              # 依赖清单
├── xlsx/
│   ├── SKILL.md              # 改写后
│   ├── scripts/
│   │   ├── recalc.py
│   │   └── office/
│   │       └── soffice.py
│   └── deps.txt
├── pptx/
│   ├── SKILL.md              # 改写后（npm→python-pptx）
│   ├── editing.md            # 从 szclaw 复制
│   ├── python-pptx-guide.md  # 替代 pptxgenjs.md
│   ├── scripts/
│   │   ├── thumbnail.py
│   │   ├── add_slide.py
│   │   ├── clean.py
│   │   └── office/
│   │       ├── unpack.py
│   │       ├── pack.py
│   │       └── soffice.py
│   └── deps.txt
├── pdf/
│   ├── SKILL.md              # 基本不变
│   ├── REFERENCE.md          # 从 szclaw 复制
│   ├── FORMS.md
│   └── deps.txt
└── wps-write/
    ├── SKILL.md              # 基本不变
    └── deps.txt
```

### 3.2 各 SKILL.md 改写要点

| Skill | 改动量 | 具体改动 |
|-------|--------|----------|
| docx | **中** | ① "Creating New Documents" 段改用 python-docx；② 删除 npm install 提示；③ 保留 XML 编辑流程（unpack→edit→pack）；④ 保留 page size / table / numbering 等规则说明（改为 python-docx 对应写法） |
| xlsx | **小** | ① 确认 `scripts/recalc.py` 路径正确；② Python 路径指向 3.12；③ 删除 Windows PATH 检查段 |
| pptx | **中** | ① "Creating from Scratch" 段改用 python-pptx；② 删除 pptxgenjs.md 引用，新增 python-pptx-guide.md；③ 保留 editing 流程；④ 保留 Design Ideas / QA 段 |
| pdf | **小** | ① Python 路径确认；② 删除 Windows 相关；③ 基本不变 |
| wps-write | **小** | ① pandoc 路径改为系统 pandoc（`/usr/bin/pandoc`）或保留 zhiyongwork 路径 |

### 3.3 依赖检查模块

新建 `skills/deps.txt` 格式：
```
# docx/deps.txt
python:docx
python:pandoc
system:soffice
system:pdftoppm
```

`skill_manager.py` 新增 `check_deps(skill_name)` → 返回缺失列表：
```python
def check_deps(skill_name):
    """检查 skill 的依赖是否满足。返回 (ok: bool, missing: list[str])"""
    deps_file = SKILLS_DIR / skill_name / "deps.txt"
    missing = []
    if not deps_file.exists():
        return True, []
    for line in deps_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, name = line.split(":", 1)
        if kind == "python":
            try:
                __import__(name)
            except ImportError:
                missing.append(f"python:{name}")
        elif kind == "system":
            if not shutil.which(name):
                missing.append(f"system:{name}")
    return len(missing) == 0, missing
```

---

## 四、代码改动明细

### 4.1 新文件

| 文件 | 职责 | 预估行数 |
|------|------|----------|
| `skill_doc_loader.py` | Instruction Skill 加载器：扫描/读取 SKILL.md、关键词触发、上下文注入 | ~150 |
| `skills/docx/SKILL.md` | Word 文档操作指令（改写版） | ~600 |
| `skills/docx/scripts/` | 从 szclaw 复制的辅助脚本 | — |
| `skills/xlsx/SKILL.md` | Excel 操作指令 | ~400 |
| `skills/xlsx/scripts/` | recalc.py 等 | — |
| `skills/pptx/SKILL.md` | PPT 操作指令（改写版） | ~500 |
| `skills/pptx/scripts/` | thumbnail.py 等 | — |
| `skills/pptx/python-pptx-guide.md` | python-pptx 创建指南（替代 pptxgenjs.md） | ~200 |
| `skills/pdf/SKILL.md` | PDF 操作指令 | ~350 |
| `skills/wps-write/SKILL.md` | 快速 Word 生成 | ~30 |

### 4.2 修改文件

#### `agent.py` 改动

```python
# 1. 新增 import
import skill_doc_loader

# 2. System prompt 增加 skill 索引
DEFAULT_SYSTEM_PROMPT = """...
可用技能（用 load_skill 工具加载详细指令后操作）：
- docx: Word 文档（.docx）创建/编辑/读取/转换
- xlsx: Excel 表格（.xlsx）创建/编辑/公式/数据分析
- pptx: PPT 演示文稿（.pptx）创建/编辑/读取
- pdf: PDF 文件读取/合并/拆分/创建/OCR
- wps-write: 快速生成 Word 并唤起 WPS 打开

Python 解释器: /opt/szclaw/Python-3.12.12/bin/python3.12
Skill 脚本目录: {work_dir}/skills/

规则补充：
8. 处理办公文档前，先调用 load_skill 加载对应 skill 的指令文档
9. 调用 skill 脚本时，cd 到 skill 目录再执行（如: cd {work_dir}/skills/docx && ...）
"""

# 3. agent_loop 中：用户消息处理后、LLM 调用前
#    检查关键词 → 自动注入 SKILL.md
def agent_loop(...):
    ...
    # 每轮处理用户消息时
    matched_skill = skill_doc_loader.maybe_inject(messages, user_text)
    if matched_skill:
        logger.info("skill_auto_injected", {"skill": matched_skill})
    ...
```

#### `tools.py` 改动

```python
# 1. 新增 load_skill 工具定义
LOAD_SKILL_TOOL = { ... }  # 见 2.2.2

# 2. TOOLS 列表增加 load_skill
ALL_TOOLS = BASE_TOOLS + [LOAD_SKILL_TOOL] + skill_manager.to_tool_schemas()

# 3. execute() 增加 load_skill 分支
def execute(name, args):
    ...
    elif name == "load_skill":
        return _exec_load_skill(args)

def _exec_load_skill(args):
    skill_name = args["skill_name"]
    skill_dir = skill_doc_loader.SKILLS_DIR / skill_name
    if not skill_dir.exists():
        return f"错误: skill '{skill_name}' 不存在"
    # 检查依赖
    ok, missing = skill_doc_loader.check_deps(skill_name)
    dep_note = ""
    if not ok:
        dep_note = f"\n\n⚠️ 缺少依赖: {', '.join(missing)}。请先安装。"
    # 读取 SKILL.md
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if len(skill_md) > 20_000:
        skill_md = skill_md[:20_000] + "\n... (截断)"
    return f"Skill directory: {skill_dir}\n\n{skill_md}{dep_note}"
```

#### `skill_manager.py` 改动

- `scan_skills()` 增加 Instruction Skill 识别（检测 SKILL.md 存在）
- `to_tool_schemas()` 只为 Script Skill 生成 schema（Instruction Skill 不注册为工具，而是通过 `load_skill` 加载）
- 新增 `list_instruction_skills()` 返回可用 instruction skill 列表

#### `skill_scanner.py` 改动

- Instruction Skill 不需要 entry 扫描（没有单入口脚本）
- 但 `scripts/` 下的 .py 文件首次加载时可跑一次安全扫描（可选）

### 4.3 `skill_doc_loader.py`（新建，核心模块）

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill_doc_loader.py — Instruction Skill 加载器

职责：
- 扫描 skills/ 目录，识别 Instruction Skill（有 SKILL.md 的）
- 关键词匹配 → 判断该注入哪个 skill
- 读取 SKILL.md 内容
- 注入到 messages（防重复）
- 依赖检查
"""
import os
import shutil
from pathlib import Path

SKILLS_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "skills"
MAX_SKILL_DOC_SIZE = 20_000  # SKILL.md 注入上限（字符）

# 关键词触发映射
SKILL_TRIGGERS = {
    "docx": [...],
    "xlsx": [...],
    "pptx": [...],
    "pdf": [...],
    "wps-write": [...],
}


def scan_instruction_skills():
    """扫描 skills/ 下所有有 SKILL.md 的目录，返回 {name: {"path": Path, "size": int}}"""


def maybe_inject(messages, user_text):
    """
    关键词匹配，命中则注入 SKILL.md 到 messages。
    返回命中的 skill name 或 None。
    """


def load_skill_doc(skill_name):
    """读取指定 skill 的 SKILL.md，返回文本。"""


def check_deps(skill_name):
    """检查 skill 依赖，返回 (ok, missing_list)。"""


def _is_injected(skill_name, messages):
    """检测 messages 中是否已有该 skill 的注入。"""


def _inject_position(messages):
    """找注入位置：首条 user 消息之前。"""
```

---

## 五、python-pptx 创建指南

### 5.1 新增 `skills/pptx/python-pptx-guide.md`

替代 `pptxgenjs.md`，提供 python-pptx 的创建模板：

```markdown
# Creating PPTX with python-pptx

## Basic Setup

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

prs = Presentation()
prs.slide_width = Inches(13.333)   # 16:9
prs.slide_height = Inches(7.5)
```

### Title Slide
```python
slide_layout = prs.slide_layouts[0]  # Title slide layout
slide = prs.slides.add_slide(slide_layout)
slide.shapes.title.text = "Slide Title"
slide.placeholders[1].text = "Subtitle"
```

### Blank Slide with Text
```python
slide_layout = prs.slide_layouts[6]  # Blank
slide = prs.slides.add_slide(slide_layout)

# Add text box
left = Inches(1)
top = Inches(2)
width = Inches(8)
height = Inches(3)
txBox = slide.shapes.add_textbox(left, top, width, height)
tf = txBox.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Your text here"
p.font.size = Pt(18)
p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
```

### Table
```python
rows, cols = 4, 3
table_shape = slide.shapes.add_table(rows, cols, Inches(1), Inches(2), Inches(8), Inches(3))
table = table_shape.table
table.cell(0, 0).text = "Header"
table.columns[0].width = Inches(3)
```

### Image
```python
slide.shapes.add_picture("chart.png", Inches(5), Inches(1), width=Inches(6))
```

### Key Rules
- 16:9 比例: slide_width=Inches(13.333), slide_height=Inches(7.5)
- 字体: `p.font.name = "Microsoft YaHei"` 设置中文
- 颜色: 用 RGBColor，不要 hex string
- 对齐: `p.alignment = PP_ALIGN.CENTER`
- 背景: `slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor(...)`
- 保存: `prs.save("output.pptx")`
```

---

## 六、开发阶段

### Phase A：基础框架（Day 1）

> 目标：Instruction Skill 加载机制跑通

| 步骤 | 内容 | 状态 |
|------|------|------|
| A1 | 新建 `skill_doc_loader.py`（scan / load / maybe_inject / check_deps） | ☐ |
| A2 | `tools.py` 新增 `load_skill` 工具 + `_exec_load_skill()` | ☐ |
| A3 | `agent.py` system prompt 增加 skill 索引（5 个 skill 名称 + 一句话描述） | ☐ |
| A4 | `agent.py` agent_loop 中增加 `skill_doc_loader.maybe_inject()` 调用 | ☐ |
| A5 | `skill_manager.py` 扩展 scan 逻辑识别 SKILL.md | ☐ |
| A6 | 验证：`python agent.py "帮我看下有什么技能"` → LLM 列出 5 个 skill | ☐ |
| A7 | 验证：`python agent.py "帮我创建一个 test.docx"` → LLM 自动调 `load_skill("docx")` | ☐ |

### Phase B：Skill 文件迁移（Day 2）

> 目标：5 个办公 skill 文件就位

| 步骤 | 内容 | 状态 |
|------|------|------|
| B1 | `skills/docx/` 目录：复制 scripts/ + templates/，改写 SKILL.md（npm→python-docx） | ☐ |
| B2 | `skills/xlsx/` 目录：复制 scripts/，微调 SKILL.md（Python 路径） | ☐ |
| B3 | `skills/pptx/` 目录：复制 scripts/ + editing.md，改写 SKILL.md，新建 python-pptx-guide.md | ☐ |
| B4 | `skills/pdf/` 目录：复制 SKILL.md + REFERENCE.md + FORMS.md，微调 | ☐ |
| B5 | `skills/wps-write/` 目录：复制 SKILL.md，确认 pandoc 路径 | ☐ |
| B6 | 每个 skill 写 `deps.txt` | ☐ |
| B7 | 验证：手动调用 `load_skill("docx")` → 返回完整指令 + 依赖检查通过 | ☐ |

### Phase C：端到端验证（Day 3）

> 目标：真实任务跑通

| 步骤 | 测试用例 | 预期 | 状态 |
|------|----------|------|------|
| C1 | `"帮我写一份项目总结报告，存成 Word"` | 生成 .docx，有标题/段落/表格 | ☐ |
| C2 | `"创建一个 Excel 报表，包含 Q1-Q4 销售数据，加个总计公式"` | 生成 .xlsx，公式正确 | ☐ |
| C3 | `"做一个 5 页的产品介绍 PPT"` | 生成 .pptx，5 页，有标题/内容 | ☐ |
| C4 | `"把 a.pdf 和 b.pdf 合并成一个"` | 生成 merged.pdf | ☐ |
| C5 | `"把 report.pdf 的前 3 页提取出来"` | 生成 page_1_3.pdf | ☐ |
| C6 | `"读取 data.xlsx，分析下趋势"` | 正确读取并分析 | ☐ |
| C7 | `"把这段文字快速存成 Word 打开"` | 生成 .docx 并唤起 wps | ☐ |
| C8 | `"编辑 report.docx，把标题改成XXX"` | unpack→XML edit→pack 成功 | ☐ |
| C9 | `"把这个 PDF 转成图片看看内容"` | pdftoppm → view_image | ☐ |
| C10 | 上下文压力测试：连续 3 个不同 skill 任务 → 确认不会撑爆 | 正常完成 | ☐ |

### Phase D：打磨（Day 4，可选）

| 步骤 | 内容 | 状态 |
|------|------|------|
| D1 | `/skill status` 命令：显示已加载的 skill + 上下文占用 | ☐ |
| D2 | Skill 文件热更新：agent 运行中 SKILL.md 改动下次生效 | ☐ |
| D3 | Skill 执行超时：长耗时操作（soffice 转换）的超时处理 | ☐ |
| D4 | 错误恢复：skill 脚本执行失败 → LLM 看到 stderr → 自动重试/报告 | ☐ |
| D5 | README 更新：办公技能使用说明 | ☐ |

---

## 七、上下文预算估算

| 场景 | tokens 估算 | 占比 (196K) |
|------|-----------|------------|
| System prompt（基础 + skill 索引） | ~800 | 0.4% |
| + docx SKILL.md 注入 | +5,500 | +2.8% |
| + xlsx SKILL.md 注入 | +3,700 | +1.9% |
| + pdf SKILL.md 注入 | +2,700 | +1.4% |
| 5 个全注入（极端） | +15,000 | +7.7% |
| 工具调用 + 对话（10 轮） | ~15,000 | 7.7% |
| **典型办公任务总消耗** | **~25,000-40,000** | **13-20%** |

**结论：** 单个 skill 注入对上下文影响可控。即使用户一次会话中用了 2-3 个不同 skill（~12K tokens），仍有充足余量完成工具调用。

---

## 八、风险 & 应对

| 风险 | 影响 | 应对 |
|------|------|------|
| SKILL.md 太长，LLM 忽略部分指令 | 生成质量下降 | 20K 字符截断 + 关键规则放在 SKILL.md 前 20% |
| LLM 不调 `load_skill` 就直接操作 | 缺少指令，输出不规范 | ① system prompt 明确要求先 load ② 关键词自动注入兜底 |
| python-docx/python-pptx 能力有限（相比 npm 包） | 复杂排版受限 | 接受限制；复杂场景走 unpack→XML→pack 路径 |
| soffice 转换慢（大文件） | 用户等待 | shell 超时设 120s + Spinner 提示 |
| 多 skill 同时触发（"把这个 docx 转成 pdf"） | 注入了 2 个 skill，token 翻倍 | 允许，但注入上限 2 个；第 3 个提示用户 |
| 跨平台路径 | Windows 用户 | 本期聚焦 Linux（szclaw 环境），Windows 后续 |
| 脚本安全 | 恶意脚本 | skill_scanner 对 scripts/*.py 跑静态扫描（首次加载时） |

---

## 九、项目最终结构（v2.0 Office）

```
agent_in/
├── agent.py             # + skill 索引 / 自动注入
├── llm.py               # 不变
├── tools.py             # + load_skill 工具
├── ui.py                # 不变
├── vision.py            # 不变
├── skill_manager.py     # + instruction skill 识别
├── skill_doc_loader.py  # 新建：instruction skill 加载器
├── skill_scanner.py     # 微调
├── memory_manager.py    # 不变
├── logger.py            # 不变
├── config.py            # 不变
├── plan.md              # v1.0
├── plan2.0.md           # v2.0 (skill/memory/logging)
├── plan3.0.md           # v3.0 (streaming/vision) ✅
├── plan_office_2.0.md   # 本文件
├── skills/
│   ├── docx/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   └── deps.txt
│   ├── xlsx/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   └── deps.txt
│   ├── pptx/
│   │   ├── SKILL.md
│   │   ├── editing.md
│   │   ├── python-pptx-guide.md
│   │   ├── scripts/
│   │   └── deps.txt
│   ├── pdf/
│   │   ├── SKILL.md
│   │   ├── REFERENCE.md
│   │   ├── FORMS.md
│   │   └── deps.txt
│   └── wps-write/
│       ├── SKILL.md
│       └── deps.txt
├── memory/
│   └── MEMORY.md
└── logs/
```

---

## 十、成功标准

```bash
# 1. Word 文档
$ python agent.py "帮我写一份 2026 年度技术部工作总结，包含：
   1. 年度概述
   2. 重点项目（表格）
   3. 下年规划
   保存为 summary.docx"
→ 生成 summary.docx ✓（打开后格式正确、有标题层级和表格）

# 2. Excel
$ python agent.py "创建一个销售报表 sales.xlsx，
   包含 2024-2026 三年的季度营收数据（数字随机），
   每行末尾加同比增长率公式，最后加总计行"
→ 生成 sales.xlsx ✓（公式正确、recalc 通过）

# 3. PPT
$ python agent.py "做一个 5 页的产品介绍 PPT：
   封面、产品功能（3 个要点）、技术架构、
   商业模式、谢谢页。深色风格。"
→ 生成 product.pptx ✓（5 页、深色背景、有内容）

# 4. PDF 操作
$ python agent.py "把 report.pdf 和 appendix.pdf 合并成 merged.pdf，
   然后提取第 1-3 页存为 extract.pdf"
→ merged.pdf (28 页) + extract.pdf (3 页) ✓

# 5. 读取分析
$ python agent.py "读取 data.xlsx，分析哪个产品季度环比增长最快"
→ 正确读取数据、计算环比、给出结论 ✓

# 6. 快速 Word
$ python agent.py "快速写个会议纪要：主题 XX 项目评审，
   参加人：张三、李四，决议：通过 v2 方案，保存并打开"
→ 生成 会议纪要.docx + WPS 自动打开 ✓
```
