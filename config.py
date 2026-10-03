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
    # 以下 max_tokens / context_limit / max_tool_iterations 是"全局兜底"。
    # 实际取值走 profile_for(provider)：providers.<name>.<key> > 文件里显式写的值 > 内置档位。
    "max_tokens": 16384,
    "context_limit": 196000,
    "shell_timeout": 60,
    "log_level": "INFO",
    "safe_mode": True,
    "max_tool_iterations": 80,  # 兜底；实际硬上限由 profile_for() 按模型给出（DeepSeek 300 / Qwen 200）
    "auto_summarize": True,
    "reasoning_effort": "low",
    "compact_at_tokens": 40000,  # [已弃用] 旧的加压阈值，保留键只为兼容旧 agent_config.json
    "keep_recent_tokens": 20000,  # 兜底；压缩时实际保留量由 profile_for() 给出（默认 24000）
    "compact_ratio": 0.75,       # 输入预算（窗口-输出预留-余量）用到这个比例就压缩
    "keep_turn_reasoning": False,  # 当前回合内的思考链是否回传（Qwen 档默认开）
    "task_temp_ttl_days": 7,     # temp/tasks/<id> 最后活动超过此天数自动清理
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
        "office2": {
            "base_url": "http://118.4.78.6:8088/api/v1",
            "api_key": "",
            "model": "szicbc-claw-01",
        },
    },
}

# 白名单 key（agent_config.json 中只认这些）
_WHITELIST = {
    "work_dir", "provider", "max_tokens", "context_limit",
    "shell_timeout", "log_level", "safe_mode", "max_tool_iterations",
    "auto_summarize", "providers", "reasoning_effort", "compact_at_tokens",
    "keep_recent_tokens", "compact_ratio", "keep_turn_reasoning", "task_temp_ttl_days",
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


# ---------------------------------------------------------------------------
# 按 provider 的预算档位（P1）
# ---------------------------------------------------------------------------
# Qwen（本地 vLLM，窗口 128K，xhigh 思考单轮可达 20K+，要给输出留足）
# DeepSeek（云端，窗口大、有前缀缓存、思考短）
_PROFILES = {
    "qwen": {
        "context_limit": 128000,
        "max_tokens": 32768,
        "max_tool_iterations": 200,
        "compact_ratio": 0.70,
        "keep_recent_tokens": 24000,
        "keep_turn_reasoning": True,
    },
    "deepseek": {
        "context_limit": 196000,
        "max_tokens": 16384,
        "max_tool_iterations": 300,
        "compact_ratio": 0.75,
        "keep_recent_tokens": 24000,
        "keep_turn_reasoning": False,
    },
}
_PROFILE_KEYS = (
    "context_limit", "max_tokens", "max_tool_iterations", "compact_ratio",
    "keep_recent_tokens", "keep_turn_reasoning", "reasoning_effort",
)
# 老版本 /config 会把当时的默认值整体落盘到 agent_config.json。
# 这些值等于"用户没设置过"，不能压住新档位。
_LEGACY_DEFAULTS = {
    "max_tokens": 8192,
    "max_tool_iterations": 80,
    "keep_recent_tokens": 20000,
}
# 窗口里留给"摘要请求/协议开销/估算误差"的余量
OUTPUT_MARGIN_TOKENS = 4096


def profile_kind(name) -> str:
    """office* 是本地 Qwen；其余按 DeepSeek 云端档。"""
    return "qwen" if str(name or "").lower().startswith("office") else "deepseek"


def profile_for(name=None) -> dict:
    """返回某 provider 的预算档位 dict（context_limit / max_tokens / max_tool_iterations /
    compact_ratio / keep_recent_tokens / keep_turn_reasoning / reasoning_effort）。

    优先级：providers.<name>.<key> > agent_config.json 顶层显式值（非遗留默认） > 内置档位。
    """
    prof = dict(_PROFILES[profile_kind(name)])
    cfg = load()
    prof["reasoning_effort"] = cfg.get("reasoning_effort", "low")
    explicit = _read_file()
    for k in _PROFILE_KEYS:
        if k in explicit and explicit[k] != _LEGACY_DEFAULTS.get(k, object()):
            if k == "context_limit" and explicit[k] == _DEFAULTS["context_limit"] \
                    and profile_kind(name) == "qwen":
                continue  # 196000 是旧全局默认，对 Qwen 档无意义
            prof[k] = explicit[k]
    pdict = (cfg.get("providers") or {}).get(name or "") or {}
    if isinstance(pdict, dict):
        for k in _PROFILE_KEYS:
            if k in pdict:
                prof[k] = pdict[k]
    return prof


def input_budget(prof: dict) -> int:
    """输入预算 = 窗口 - 单次输出预留 - 余量。"""
    return max(int(prof["context_limit"]) - int(prof["max_tokens"]) - OUTPUT_MARGIN_TOKENS, 4096)


def compact_trigger(prof: dict) -> int:
    """估算的 prompt 用到这个值就压缩。"""
    return int(input_budget(prof) * float(prof["compact_ratio"]))


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
