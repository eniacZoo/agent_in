#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PTY 可视化测试 v3：真 input() + 正确屏幕网格模拟。

- 子进程在真实 pty 上运行 ui.Spinner + ui.confirm + ui.text_input
- 主进程通过 pty master 写入 "y" / "the-token" 模拟用户键入（含延迟）
- 用屏幕网格状态机还原最终画面
"""
import os
import pty
import select
import sys
import time

SCRIPT = (
    "import sys\n"
    "sys.path.insert(0, '.')\n"
    "import ui\n"
    "with ui.Spinner('shell: Remove-Item -Recurse long', delay=0.2):\n"
    "    time = __import__('time'); time.sleep(0.6)\n"
    "    ok = ui.confirm('高危 测试提示 命令: xxx')\n"
    "    tok = ui.text_input('  输入完整命令以确认: the-token')\n"
    "print('RESULT ok=%s tok=%s' % (ok, tok))\n"
    "print('DONE-MARKER')\n"
)

pid, fd = pty.fork()
if pid == 0:
    os.execv(sys.executable, [sys.executable, "-c", SCRIPT])

screen_rows = []   # 每行: list[char]
col = 0
row = 0
raw = b""


def ensure_rows(n):
    while len(screen_rows) < n + 1:
        screen_rows.append([])


def put(ch):
    global col
    ensure_rows(row)
    line = screen_rows[row]
    while len(line) <= col:
        line.append(" ")
    line[col] = ch
    col += 1


def feed_after(seconds, data):
    time.sleep(seconds)
    os.write(fd, data)


# 线程 1：按时间线投喂用户输入（等 prompt 出现再打）
def typist():
    time.sleep(1.2)      # 等 confirm prompt 打印出来
    os.write(fd, b"y\r")
    time.sleep(0.8)      # 等 text_input prompt
    os.write(fd, b"the-token\r")


import threading
threading.Thread(target=typist, daemon=True).start()

# 主循环：读 master，喂给屏幕状态机
while True:
    r, _, _ = select.select([fd], [], [], 5.0)
    if not r:
        break
    try:
        chunk = os.read(fd, 65536)
    except OSError:
        break
    if not chunk:
        break
    raw += chunk
    i = 0
    text = chunk.decode("utf-8", "replace")
    while i < len(text):
        c = text[i]
        if c == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and not ("@" <= text[j] <= "~"):
                j += 1
            if j < len(text) and text[j] == "K":
                line = screen_rows[row] if row < len(screen_rows) else []
                del line[col:]          # 擦到行尾
            i = j + 1
            continue
        if c == "\r":
            col = 0
        elif c == "\n":
            row += 1
            col = 0
        else:
            put(c)
        i += 1

os.waitpid(pid, 0)

print("==== 终端最终画面 ====")
for n, line in enumerate(screen_rows):
    print(f"{n:2d} | {''.join(line)}")
print("==== 断言 ====")
FRAMES = set(["\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
              "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"])
residue = [l for l in screen_rows if any(ch in FRAMES for ch in l)]
print("无残留 spinner 帧:", not residue, ["".join(r) for r in residue])
joined = "\n".join("".join(l) for l in screen_rows)
print("RESULT 行正确:", "RESULT ok=True tok=the-token" in joined)
print("用户输入回显存在 (y / the-token):", "y" in joined and "the-token" in joined)
print("prompt 行未被 spinner 覆盖:", "输入完整命令以确认: the-token" in joined)
