#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill_manager.py — Skill 文件（plan9：成长在核心外）

- 脚本 skill：skills/<name>/skill.json + entry，人用 /use 执行
- markdown 流程：skills/*.md，人用 /read-skill，模型用 read_file
不把技能注册进 TOOLS。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skill_scanner
import approval
import audit


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = _ROOT / "skills"
VENDOR_DIR = _ROOT / "vendor"
SKILL_TIMEOUT = 120  # skill 执行超时（秒）
SKILL_MAX_OUTPUT = 10_000  # 输出上限
MARKDOWN_MAX_CHARS = 20_000


# ---------------------------------------------------------------------------
# 扫描 & 加载
# ---------------------------------------------------------------------------
def scan_skills():
    """
    扫描 skills/ 目录，返回所有有效 skill 的元数据列表。
    每个元素: {"name": str, "meta": dict, "path": Path}
    """
    skills = []
    if not SKILLS_DIR.exists():
        return skills

    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill_json = d / "skill.json"
        if not skill_json.exists():
            continue
        try:
            meta = json.loads(skill_json.read_text(encoding="utf-8"))
            if "name" not in meta or "entry" not in meta:
                continue
            skills.append({
                "name": meta["name"],
                "meta": meta,
                "path": d,
            })
        except (json.JSONDecodeError, OSError):
            continue

    return skills


def to_tool_schemas():
    """
    将所有 skill 转为 OpenAI function calling 格式。
    返回 list[dict]，可直接拼入 tools 参数。
    """
    schemas = []
    for skill in scan_skills():
        meta = skill["meta"]
        name = "skill_" + meta["name"]  # 前缀避免与内置工具冲突

        # 构建 parameters
        params_def = meta.get("params", {})
        properties = {}
        required = []
        for pname, pinfo in params_def.items():
            prop = {
                "type": pinfo.get("type", "string"),
                "description": pinfo.get("description", ""),
            }
            properties[pname] = prop
            if pinfo.get("required", False):
                required.append(pname)

        description = meta.get("description", "")
        trigger = meta.get("trigger", "")
        if trigger:
            description = description + " (" + trigger + ")"

        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
        schemas.append(schema)

    return schemas


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def execute_skill(name, params, session_id=None, confirm_fn=None, input_fn=None):
    """
    执行指定 skill。
    name: skill 名称（不带 skill_ 前缀）
    params: dict，传入参数
    session_id: 审计"谁"（v6.0）
    confirm_fn/input_fn: 审批回调注入（v6.0 陷阱 F：skill 安全链在此闭合）

    返回执行结果字符串。
    """
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        return "Error: Skill '{}' not found".format(name)

    skill_json = skill_dir / "skill.json"
    if not skill_json.exists():
        return "Error: skill.json not found for '{}'".format(name)

    try:
        meta = json.loads(skill_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return "Error: Invalid skill.json: {}".format(e)

    entry = meta.get("entry", "")
    entry_path = skill_dir / entry
    if not entry_path.exists():
        return "Error: Entry script not found: {}".format(entry_path)

    # ---- S5: 静态扫描 + 审批 + 信任（v6.0，闭合陷阱 F）----
    _gate = _skill_security_gate(name, entry_path, session_id, confirm_fn, input_fn)
    if not _gate.get("approved", False):
        return _gate.get("message", "Skill 执行被拒绝")

    language = meta.get("language", "python")

    # 构建命令
    if language == "python":
        # 找 python
        python_bin = _find_python()
        cmd = [python_bin, str(entry_path), "--args", json.dumps(params, ensure_ascii=False)]
    elif language == "shell" or language == "bash":
        cmd = ["bash", str(entry_path), json.dumps(params, ensure_ascii=False)]
    else:
        # 尝试直接执行
        cmd = [str(entry_path), json.dumps(params, ensure_ascii=False)]

    env = _skill_subprocess_env()

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=SKILL_TIMEOUT,
            cwd=str(Path(os.environ.get("WORK_DIR", os.getcwd()))),
            env=env,
        )
        elapsed = time.time() - t0
    except subprocess.TimeoutExpired:
        return "Error: Skill '{}' timed out after {}s".format(name, SKILL_TIMEOUT)
    except Exception as e:
        return "Error: Failed to run skill '{}': {}".format(name, e)

    # 组装输出。字节解码，避免 text=True 在中文 Windows 上按 GBK 读线程崩掉。
    stdout = _decode_skill_output(proc.stdout)
    stderr = _decode_skill_output(proc.stderr)
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append("[stderr]\n" + stderr)

    output = "\n".join(parts).strip() if parts else "(no output)"

    if len(output) > SKILL_MAX_OUTPUT:
        output = output[:SKILL_MAX_OUTPUT] + "\n... (truncated)"

    result = "[skill:{}] ".format(name)
    if proc.returncode != 0:
        result += "[exit code: {}]\n".format(proc.returncode)
    result += output
    result += "\n[elapsed: {:.1f}s]".format(elapsed)

    return result


# ---------------------------------------------------------------------------
# S5: Skill 安全门（v6.0）
# ---------------------------------------------------------------------------
def _skill_security_gate(name, entry_path, session_id, confirm_fn, input_fn):
    """
    Skill 执行前的安全门：静态扫描 → 审批 → 信任记录。

    返回 {"approved": bool, "message": str}
    """
    entry_path = str(entry_path)

    # 1) 静态扫描
    scan = skill_scanner.scan_entry(entry_path)
    sha = skill_scanner.file_hash(entry_path)

    # 2) 信任校验
    status, _ = skill_scanner.verify_trust(name, sha)

    # 3) 构建 Verdict
    if status == "tampered":
        verdict = {
            "severity": "HIGH",
            "action": "strong_confirm",
            "findings": ["skill_tampered"],
            "reason": "Skill 文件被篡改（hash 不匹配信任记录）",
            "detail": entry_path,
        }
    elif scan["risky"]:
        sev = scan["max_severity"]
        # HIGH → strong_confirm; MEDIUM → confirm
        action = "strong_confirm" if sev in ("HIGH", "CRITICAL") else "confirm"
        findings = ["{}@L{}".format(f["pattern_desc"], f["line"]) for f in scan["findings"][:5]]
        verdict = {
            "severity": sev,
            "action": action,
            "findings": findings,
            "reason": "Skill 含风险模式: {}".format(scan["findings"][0]["pattern_desc"]) if scan["findings"] else "风险模式",
            "detail": entry_path,
        }
    elif status == "untrusted":
        # 未记录过 → confirm（首次使用）
        verdict = {
            "severity": "LOW",
            "action": "confirm",
            "findings": ["skill_untrusted"],
            "reason": "Skill 首次使用（未信任）",
            "detail": entry_path,
        }
    else:
        # trusted + 无风险 → ALLOW（静默）
        return {"approved": True, "message": ""}

    # 4) 审批
    ctx = {
        "session_id": session_id or "-",
        "tool": "skill_{}".format(name),
        "source": "skill",
        "rule_id": ";".join(verdict["findings"]),
    }
    decision = approval.resolve(verdict, confirm_fn, input_fn, ctx)

    if not decision["approved"]:
        return {"approved": False,
                "message": "Skill '{}' 执行被拒绝（{}）".format(name, decision["reason"])}

    # 5) 确认后写信任记录
    try:
        skill_scanner.save_trust(name, "manual", sha)
    except Exception:
        pass  # 信任写入失败不阻断

    return {"approved": True, "message": ""}


def _skill_subprocess_env():
    """与 shell 工具同一套环境：UTF-8 输出，并带上任务临时目录。"""
    try:
        import tools as _tools
        return _tools._shell_env()
    except Exception:
        env = os.environ.copy()
        vendor = str(VENDOR_DIR)
        old_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = vendor + os.pathsep + old_pp if old_pp else vendor
        env["PYTHONIOENCODING"] = "utf-8"
        return env


def _decode_skill_output(data):
    """utf-8 → gb18030 → locale → replace。与 tools._decode_bytes 同一条链。"""
    if not data:
        return ""
    try:
        import tools as _tools
        return _tools._decode_bytes(data)
    except Exception:
        return data.decode("utf-8", errors="replace")


def _find_python():
    """当前进程的解释器。vendor 按这个版本打包。"""
    try:
        import tools as _tools
        py = _tools._vendor_python()
        if py:
            return py
    except Exception:
        pass
    current = sys.executable
    if current and os.path.exists(current):
        return current
    # 尝试常见路径
    for p in ["python3", "python"]:
        found = shutil.which(p)
        if found:
            return found
    return "python3"


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------
def save_skill(name, description, trigger, files, source_task=""):
    """
    保存新 skill。

    name: skill 名称（snake_case）
    description: 功能描述
    trigger: 触发条件描述
    files: dict {filename: content}，要保存的文件（至少包含 entry 脚本）
    source_task: 来源任务描述（记录用）

    返回 (success: bool, msg: str)
    """
    # 名称校验
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        return False, "Invalid skill name: '{}'".format(name)

    skill_dir = SKILLS_DIR / name
    if skill_dir.exists():
        return False, "Skill '{}' already exists. Use a different name or delete it first.".format(name)

    # 确定 entry 文件
    entry = None
    for fname in files:
        if fname.endswith(".py") or fname.endswith(".sh"):
            entry = fname
            break
    if entry is None:
        return False, "No entry script found in files (need .py or .sh)"

    language = "python" if entry.endswith(".py") else "shell"

    # 构建 params（从文件内容简单推断，或留空让用户后续补充）
    params = {}

    skill_meta = {
        "name": name,
        "version": "1.0",
        "description": description,
        "trigger": trigger,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "source_task": source_task,
        "entry": entry,
        "language": language,
        "params": params,
    }

    # 写入文件
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 写 skill.json
    (skill_dir / "skill.json").write_text(
        json.dumps(skill_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 写其他文件
    for fname, content in files.items():
        (skill_dir / fname).write_text(content, encoding="utf-8")

    return True, "Skill '{}' saved to skills/{}/ (entry: {})".format(name, name, entry)


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------
def delete_skill(name):
    """删除 skill。返回 (success, msg)。"""
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        return False, "Skill '{}' not found".format(name)
    shutil.rmtree(skill_dir)
    return True, "Skill '{}' deleted".format(name)


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------
def list_skills():
    """返回脚本 skill 列表摘要（用于 /ls skills）。"""
    skills = scan_skills()
    if not skills:
        return []
    result = []
    for s in skills:
        meta = s["meta"]
        result.append({
            "name": meta["name"],
            "description": meta.get("description", ""),
            "entry": meta.get("entry", ""),
            "created": meta.get("created", ""),
            "version": meta.get("version", ""),
        })
    return result


def list_markdown():
    """
    skills/ 根目录下的 markdown 流程文件（不是脚本 skill）。
    返回 [{"name", "path", "description"}]。
    """
    if not SKILLS_DIR.exists():
        return []
    result = []
    for p in sorted(SKILLS_DIR.glob("*.md")):
        if not p.is_file():
            continue
        desc = ""
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s:
                    continue
                desc = s.lstrip("#").strip()
                break
        except OSError:
            desc = ""
        result.append({
            "name": p.stem,
            "path": str(p),
            "description": desc,
        })
    return result


def read_markdown(name):
    """
    读取 skills/<name>.md。禁止路径穿越。

    返回 (text, err)。成功时 err 为 None。
    """
    if not name or not str(name).strip():
        return None, "未指定流程名"
    base = Path(str(name).strip().replace("\\", "/")).name
    if not base.endswith(".md"):
        base = base + ".md"
    if not SKILLS_DIR.exists():
        return None, "未找到流程文件: {}".format(base)
    target = (SKILLS_DIR / base).resolve()
    root = SKILLS_DIR.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, "非法路径"
    if not target.is_file():
        return None, "未找到流程文件: {}".format(base)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as e:
        return None, str(e)
    if len(text) > MARKDOWN_MAX_CHARS:
        text = text[:MARKDOWN_MAX_CHARS] + "\n... (截断)"
    return text, None
