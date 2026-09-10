from __future__ import annotations

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — 配置加载（F1，叶子模块）

单一事实来源（single source of truth）。
优先级：CLI 参数 > agent_config.json > 环境变量 > 内置默认

零依赖：json + os + pathlib
"""
import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 内置默认值
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "work_dir": "",
    "provider": "default",
    "max_tokens": 8192,
    "context_limit": 196000,
    "shell_timeout": 60,
    "log_level": "INFO",
    "safe_mode": True,
    "max_tool_iterations": 80,
    "auto_summarize": True,
    "reasoning_effort": "low",
    "compact_at_tokens": 40000,  # 单次请求 prompt 超过此值即加压压缩
    # H 系列（v5.0）
    "max_retries": 3,           # H1: 连接阶段最大重试次数
    "retry_base_delay": 1.0,    # H1: 指数退避基准秒数
    "rate_limit": 0,            # H2: 同 model 两次请求最小间隔秒数（0=关闭）
    "capability_ttl_days": 7,   # H4: 能力探测缓存有效期（天）
    "auto_probe": 1,            # H4: 启动时自动探测能力（0=仅连通检测）
    # providers 字典（至少含 default）
    "providers": {
        "default": {
            "base_url": "https://api.deepseek.com",
            "api_key": "",
            "model": "deepseek-v4.1-flash-expires-on-0910",
        },
        "office": {
            "base_url": "http://118.4.78.6:8088/api/v1",
            "api_key": "",
            "model": "AngelOrDevil",
        },
    },
}

# 白名单 key（agent_config.json 中只认这些）
_WHITELIST = {
    "work_dir", "provider", "max_tokens", "context_limit",
    "shell_timeout", "log_level", "safe_mode", "max_tool_iterations",
    "auto_summarize", "providers", "reasoning_effort", "compact_at_tokens",
    # H 系列（v5.0）
    "max_retries", "retry_base_delay", "rate_limit",
    "capability_ttl_days", "auto_probe",
}

# 项目根目录（__file__ 锁定，不随 work_dir 变）
_PROJECT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# 可通过 CLI --config 覆盖的配置路径（None = 默认项目根）
_config_path_override: "Path | None" = None


def set_path(p) -> None:
    """设置配置文件路径（CLI --config）。传 None 恢复默认。失效缓存。"""
    global _config_path_override, _config_cache
    _config_path_override = Path(p).expanduser().resolve() if p else None
    _config_cache = None


def path() -> Path:
    """agent_config.json 绝对路径（尊重 --config 覆盖）。"""
    return _config_path_override or (_PROJECT_DIR / "agent_config.json")


# ---------------------------------------------------------------------------
# 内部缓存
# ---------------------------------------------------------------------------
_config_cache: dict | None = None


def _read_env() -> dict:
    """从环境变量读取配置（仅顶层标量）。"""
    env_map = {
        "work_dir": "WORK_DIR",
        "max_tokens": "MAX_TOKENS",
        "context_limit": "CONTEXT_LIMIT",
        "shell_timeout": "SHELL_TIMEOUT",
        "log_level": "LOG_LEVEL",
        "provider": "AGENT_PROVIDER",
        "safe_mode": "SAFE_MODE",
    }
    result = {}
    for key, env_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            if isinstance(_DEFAULTS[key], int):
                try:
                    val = int(val)
                except ValueError:
                    continue
            elif isinstance(_DEFAULTS[key], bool):
                val = val.lower() in ("1", "true", "yes")
            result[key] = val
    return result


def _read_file() -> dict:
    """从 agent_config.json 读取配置。文件不存在返回空 dict。"""
    p = path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # 只认白名单 key
    filtered = {}
    for k, v in raw.items():
        if k in _WHITELIST:
            filtered[k] = v
    return filtered


def load(cli_overrides: dict | None = None, force_reload: bool = False) -> dict:
    """
    加载并合并三层配置，返回扁平 dict。

    优先级：cli_overrides > agent_config.json > 环境变量 > _DEFAULTS

    参数：
        cli_overrides: CLI 参数覆盖（只含用户实际传入的）
        force_reload: 强制重新加载（/config 命令落盘后刷新）

    返回：
        完整的配置 dict
    """
    global _config_cache
    if _config_cache is not None and not force_reload:
        # 仍然要合并 cli_overrides（每次调用可能不同）
        merged = dict(_config_cache)
        if cli_overrides:
            for k, v in cli_overrides.items():
                if v is not None and k in _WHITELIST:
                    merged[k] = v
        return merged

    # 三层合并
    merged = dict(_DEFAULTS)
    # 第二层：环境变量
    env_cfg = _read_env()
    merged.update(env_cfg)
    # 第三层：agent_config.json
    file_cfg = _read_file()
    merged.update(file_cfg)
    # 第一层：CLI 覆盖
    if cli_overrides:
        for k, v in cli_overrides.items():
            if v is not None and k in _WHITELIST:
                merged[k] = v

    _config_cache = merged
    return merged


def get(key: str, default=None):
    """供各模块按需取配置值。"""
    cfg = load()
    return cfg.get(key, default)


def save(cfg: dict) -> Path:
    """
    将配置落盘到 agent_config.json。
    只写白名单 key。
    """
    clean = {k: v for k, v in cfg.items() if k in _WHITELIST}
    p = path()
    p.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    global _config_cache
    _config_cache = None  # 失效缓存
    return p
