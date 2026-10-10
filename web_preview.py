#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固定页面验收。由 preview_page 工具调用，不要在任务里改这个文件。"""
import json
import os
import sys

STOP = "报告原因并停止。不要安装浏览器，不要另写验收脚本。"


def edge_missing_message():
    return "本机没有 Edge。" + STOP


def connection_message(url):
    return "页面没打开：{} 先确认服务在听这个端口。{}".format(url, STOP)


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")


def _looks_like_missing_edge(text):
    low = (text or "").lower()
    return any(s in low for s in (
        "msedge", "channel", "executable doesn't exist", "not found", "找不到",
    ))


def main(argv):
    if len(argv) < 3:
        emit({"ok": False, "stop": True, "error": "preview 参数不足。" + STOP})
        return 2
    url, out = argv[1], argv[2]
    os.makedirs(out, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        emit({
            "ok": False,
            "stop": True,
            "error": "vendor 与解释器不匹配，不要搜索其他 python。{}".format(e),
        })
        return 2
    errors = []
    shots = {}
    title = ""
    excerpt = ""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception as e:
                msg = edge_missing_message()
                if not _looks_like_missing_edge(str(e)):
                    msg = msg + " " + str(e)[:300]
                emit({"ok": False, "stop": True, "error": msg})
                return 1
            try:
                page = browser.new_page()
                page.on("pageerror", lambda err: errors.append(str(err)))

                def on_console(msg):
                    if msg.type == "error":
                        errors.append(msg.text)

                page.on("console", on_console)
                for w, h, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
                    page.set_viewport_size({"width": w, "height": h})
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    except Exception as e:
                        emit({
                            "ok": False,
                            "stop": True,
                            "error": connection_message(url) + " " + str(e)[:300],
                        })
                        return 1
                    path = os.path.join(out, name + ".png")
                    page.screenshot(path=path, full_page=True)
                    shots[name] = path
                try:
                    title = page.title() or ""
                except Exception:
                    title = ""
                try:
                    excerpt = (page.inner_text("body") or "")[:2000]
                except Exception:
                    excerpt = ""
            finally:
                browser.close()
    except Exception as e:
        emit({
            "ok": False,
            "stop": True,
            "error": connection_message(url) + " " + str(e)[:300],
        })
        return 1
    emit({
        "ok": True,
        "stop": False,
        "title": title,
        "excerpt": excerpt,
        "errors": errors[:20],
        "shots": shots,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
