#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heartbeat.py — TCP/HTTP 端到端探测（叶子模块，仅 stdlib）

不 import 业务模块。调用方负责 logger。
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


LAST = {}  # 最近一次 ping 结果


def ping(base_url, timeout=3.0):
    """
    探测 base_url 的 DNS → TCP → HTTP。

    返回 dict: ok, stage (dns|tcp|http), elapsed_ms, error, errno
    stage 为失败发生的阶段；全成功时 stage=http。
    """
    t0 = time.time()
    result = {
        "ok": False,
        "stage": "dns",
        "elapsed_ms": 0,
        "error": "",
        "errno": None,
        "host": "",
        "port": 0,
    }
    url = (base_url or "").strip()
    if not url:
        result["error"] = "empty base_url"
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        _store(result)
        return result
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    result["host"] = host
    result["port"] = port
    if not host:
        result["error"] = "no host"
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        _store(result)
        return result

    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        result["error"] = str(e)
        result["errno"] = getattr(e, "errno", None)
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        _store(result)
        return result

    result["stage"] = "tcp"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        result["error"] = str(e)
        result["errno"] = getattr(e, "errno", None)
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        _store(result)
        return result

    result["stage"] = "http"
    probe_url = parsed.scheme + "://" + parsed.netloc + "/"
    req = urllib.request.Request(probe_url, method="GET")
    try:
        urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        # 4xx/5xx 仍说明 HTTP 达得到
        pass
    except OSError as e:
        result["error"] = str(e)
        result["errno"] = getattr(e, "errno", None)
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        _store(result)
        return result

    result["ok"] = True
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    _store(result)
    return result


def _store(result):
    global LAST
    LAST = dict(result)
