#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_manager.py — Agent 长期记忆管理

存储：memory/MEMORY.md（Markdown 格式，人可读、agent 可编辑）
功能：load / append / delete / clear / 容量控制
"""
import os
import re
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
MEMORY_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "memory"
MEMORY_FILE = MEMORY_DIR / "MEMORY.md"

# 容量控制
MAX_CHARS = 8000       # 注入 prompt 的最大字符数
HARD_LIMIT = 16000     # 文件硬上限，超过时截断最旧内容

# Section 优先级（截断时保留高优先级的）
SECTION_PRIORITY = [
    "## 规则",
    "## 用户偏好",
    "## 项目上下文",
    "## 经验教训",
]

# 初始模板
TEMPLATE = """# Agent Memory

## 用户偏好
<!-- 用户的命名风格、输出格式、代码规范等 -->

## 规则
<!-- 必须遵守的约束：不要做X、必须用Y -->

## 项目上下文
<!-- 技术栈、路径、端口等环境信息 -->

## 经验教训
<!-- 踩过的坑、最佳实践，格式：[日期] 内容 -->
"""


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------
def _ensure_file():
    """确保 memory 目录和文件存在。"""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(TEMPLATE, encoding="utf-8")


# ---------------------------------------------------------------------------
# 核心接口
# ---------------------------------------------------------------------------
def load():
    """
    读取 MEMORY.md 内容，用于拼入 system prompt。
    超过 MAX_CHARS 时按 section 优先级截断。
    返回 (content: str, loaded_chars: int)
    """
    _ensure_file()
    try:
        content = MEMORY_FILE.read_text(encoding="utf-8")
    except Exception:
        return "", 0

    # 去掉空 section（只有标题没有内容的）
    content = _clean_empty_sections(content)

    if len(content) <= MAX_CHARS:
        return content, len(content)

    # 超长 → 按优先级保留
    sections = _split_sections(content)
    # 按优先级排序
    ordered = []
    for priority_section in SECTION_PRIORITY:
        for name, body in sections:
            if name == priority_section:
                if body.strip():
                    ordered.append((name, body))
    # 剩余未匹配的 section 放最后
    for name, body in sections:
        if name not in SECTION_PRIORITY and body.strip():
            ordered.append((name, body))

    # 组装截断后的内容
    result = "# Agent Memory\n\n"
    for name, body in ordered:
        block = f"{name}\n{body}\n"
        if len(result) + len(block) > MAX_CHARS:
            # 尝试只放部分
            remaining = MAX_CHARS - len(result) - 50
            if remaining > 50:
                result += name + "\n" + body[:remaining] + "\n...(截断)\n"
            break
        result += block + "\n"

    return result, len(content)


def append(section, text):
    """
    向指定 section 追加一条记录。
    section: "用户偏好" / "规则" / "项目上下文" / "经验教训"
    text: 要追加的内容
    返回 True/False
    """
    _ensure_file()
    content = MEMORY_FILE.read_text(encoding="utf-8")

    # 经验教训自动加日期
    if section == "经验教训" and not text.startswith("["):
        text = "[{}] {}".format(datetime.now().strftime("%Y-%m-%d"), text)

    target_header = "## {}".format(section)

    if target_header not in content:
        # section 不存在，追加到文件末尾
        content = content.rstrip() + "\n\n{}\n{}\n".format(target_header, text)
    else:
        # 在 section 末尾（下一个 ## 之前）插入
        lines = content.split("\n")
        insert_idx = None
        in_target = False
        for i, line in enumerate(lines):
            if line.strip() == target_header:
                in_target = True
                continue
            if in_target:
                if line.startswith("## "):
                    # 下一个 section 开头，在它前面插入
                    insert_idx = i
                    break
                in_target = True
        if insert_idx is None:
            # section 在文件末尾
            lines.append(text)
        else:
            lines.insert(insert_idx, text)
        content = "\n".join(lines)

    # 容量检查
    if len(content) > HARD_LIMIT:
        content = _trim_content(content)

    MEMORY_FILE.write_text(content, encoding="utf-8")
    return True


def delete(keyword):
    """
    删除包含关键词的行（不区分大小写）。
    返回删除的行数。
    """
    _ensure_file()
    content = MEMORY_FILE.read_text(encoding="utf-8")
    lines = content.split("\n")
    kept = []
    removed = 0
    for line in lines:
        if keyword.lower() in line.lower() and not line.startswith("#"):
            removed += 1
        else:
            kept.append(line)
    if removed > 0:
        MEMORY_FILE.write_text("\n".join(kept), encoding="utf-8")
    return removed


def clear():
    """清空记忆（恢复为模板）。"""
    _ensure_file()
    MEMORY_FILE.write_text(TEMPLATE, encoding="utf-8")
    return True


def get_full_content():
    """返回 MEMORY.md 完整内容（用于 /memory 命令展示）。"""
    _ensure_file()
    try:
        return MEMORY_FILE.read_text(encoding="utf-8")
    except Exception:
        return "(读取失败)"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _split_sections(content):
    """按 ## 标题拆分，返回 [(header, body), ...]"""
    sections = []
    current_header = ""
    current_lines = []

    for line in content.split("\n"):
        if line.startswith("## "):
            if current_header:
                sections.append((current_header, "\n".join(current_lines)))
            current_header = line
            current_lines = []
        elif line.startswith("# ") and not line.startswith("## "):
            # 主标题，跳过
            continue
        else:
            current_lines.append(line)

    if current_header:
        sections.append((current_header, "\n".join(current_lines)))
    return sections


def _clean_empty_sections(content):
    """去掉只有标题没有实际内容的 section。"""
    sections = _split_sections(content)
    result = "# Agent Memory\n\n"
    for header, body in sections:
        # 去掉 HTML 注释
        clean_body = re.sub(r"<!--.*?-->", "", body).strip()
        if clean_body:
            result += header + "\n" + body.rstrip() + "\n\n"
        else:
            # 保留空 section 但只留标题（让 agent 知道有这个分类）
            result += header + "\n\n"
    return result.rstrip() + "\n"


def _trim_content(content):
    """超过 HARD_LIMIT 时，从最旧（文件底部经验教训）开始删。"""
    lines = content.split("\n")
    while len("\n".join(lines)) > HARD_LIMIT and len(lines) > 10:
        # 从底部删除非标题行
        for i in range(len(lines) - 1, 9, -1):
            if not lines[i].startswith("#") and lines[i].strip():
                lines.pop(i)
                break
        else:
            break
    return "\n".join(lines)
