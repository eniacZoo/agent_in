from __future__ import annotations

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
providers.py — Provider Profile 注册表（F1/F3，叶子模块）

对齐 szclaw provider_manager 的多 provider 思路，但极简。
叶子模块：只 import config（config 也是叶子），不 import 其他本地模块。

对外接口：
  get_all()          -> dict  所有 profile
  get_active(name)   -> dict  {base_url, api_key, model}
  get_active_name()  -> str   当前 profile 名
  list_names()       -> list[str]
  apply_to_llm(p)    -> None  把 provider 写回 llm 模块常量
  set_active_name(n) -> None  记录活跃 profile 名（H2，供 current_key）
  current_key(model) -> str   "name:model"（H2，rate_limiter 的 per-model key）
"""
import config

# 运行时切换（/provider）后记录的名字；None 则回落到 config["provider"]
_active_name: str | None = None


def get_all() -> dict:
    """读取 config 的 providers 字段，返回 {name: {base_url, api_key, model}}。"""
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    return dict(providers)


def list_names() -> list:
    """返回所有 provider profile 名称。"""
    return list(get_all().keys())


def get_active_name() -> str:
    """当前活跃 profile 名（/provider、/model 切换后优先）。"""
    return _active_name or config.get("provider", "default")


def get_active(name: str | None = None) -> dict:
    """
    返回活跃 provider 的 {base_url, api_key, model}。

    参数：
        name: 指定 profile 名。None 则取 get_active_name()。

    返回：
        dict with keys: base_url, api_key, model
    """
    all_p = get_all()
    target_name = name or get_active_name()
    if target_name not in all_p:
        # 兜底：返回 default profile
        target_name = "default"
        if target_name not in all_p:
            # 终极兜底：内置值
            return {
                "base_url": "https://api.deepseek.com",
                "api_key": "",
                "model": "deepseek-v4.1-flash-expires-on-0910",
            }
    p = all_p[target_name]
    return {
        "base_url": p.get("base_url", ""),
        "api_key": p.get("api_key", ""),
        "model": p.get("model", ""),
    }


def set_active_name(name: str) -> None:
    """记录当前活跃 profile 名（启动 --provider / 交互 /provider 切换时调用）。"""
    global _active_name
    _active_name = name


def current_key(model: str | None = None) -> str:
    """
    返回 "name:model" key（H2：rate_limiter 的 per-model 槽位）。

    对齐 szclaw 的 provider_id:model_name 语义，保证「一个 model 限流
    不影响其他 model」（陷阱 C）。
    model=None 时取活跃 profile 的 model；传 model（如 --model 覆盖）则用之。
    """
    name = get_active_name()
    if model is None:
        model = get_active(name).get("model", "")
    return f"{name}:{model}"


def apply_to_llm(p: dict) -> None:
    """
    把 provider profile 写回 llm 模块的 BASE_URL / API_KEY / MODEL 常量。

    参数：
        p: {"base_url": ..., "api_key": ..., "model": ...}

    注意：llm 模块保留模块常量做默认兜底，这里只是覆盖。
    """
    import llm
    if p.get("base_url"):
        llm.BASE_URL = p["base_url"].rstrip("/")
    if p.get("api_key"):
        llm.API_KEY = p["api_key"]
    if p.get("model"):
        llm.MODEL = p["model"]
