#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：spinner 不得在用户输入期间重绘（原 bug：[y/N] 字符重叠/token 不匹配）。

模拟场景（对应 Windows PowerShell 交互日志）：
  with Spinner(shell 命令, delay=0.3):
      tools.execute(...) → approval 强确认：
        1) ui.confirm 打印提示后 input "y/N"
        2) ui.text_input 键入完整命令 token

验证：
  1) 用户"输入进行中"的时间窗内，stdout 没有任何 spinner 输出（帧/重绘）；
  2) confirm 返回 True；
  3) token 精确匹配（不出现 "token 不匹配"）。
"""
import builtins
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui

FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def main():
    real_stdout = sys.stdout
    captured = {"visible_while_input": "", "frames_while_input": 0}
    input_window = {"active": False}

    class Tee:
        def write(self, s):
            real_stdout.write(s)
            if input_window["active"]:
                if any(f in s for f in ui.Spinner.FRAMES):
                    captured["frames_while_input"] += 1
                stripped = s.replace("\r", "").replace("\033[K", "")
                if stripped.strip():
                    captured["visible_while_input"] += stripped
            return len(s)

        def flush(self):
            real_stdout.flush()

    sys.stdout = Tee()

    CMD = 'Remove-Item -Recurse -Force "D:\\Backup\\agent_in_v1.4"'
    prompts_seen = []

    def fake_input(prompt=""):
        prompts_seen.append(prompt)
        # 模拟用户思考 0.6s（让 spinner 先进入绘制状态，复现原 bug 时序）
        time.sleep(0.6)
        input_window["active"] = True
        try:
            ans = "y" if "[y/N]" in prompt else CMD
            time.sleep(0.2)  # 模拟键入过程
        finally:
            input_window["active"] = False
        return ans

    builtins.input = fake_input

    prompt = "\u26a0\ufe0f [高危] 命中规则 ps_rm_rf（PowerShell 递归强删）\n   命令: " + CMD
    with ui.Spinner("shell: Remove-Item -Recurse (65s)", delay=0.3):
        ok = ui.confirm(prompt)                 # 第一轮 y/N
        if ok:
            token = ui.text_input("  \u26a0\ufe0f 输入完整命令以确认: " + CMD)
            result = "strong_confirmed" if token.strip() == CMD.strip() else "token_mismatch"
        else:
            result = "rejected"

    sys.stdout = real_stdout
    builtins.input = input  # 还原

    check("confirm 返回 True", ok is True)
    check("强确认 token 匹配（非 token_mismatch）", result == "strong_confirmed")
    check("输入期间无旋转帧输出", captured["frames_while_input"] == 0)
    check("输入期间无其他可见输出污染", captured["visible_while_input"].strip() == "")
    check("两次输入提示均收到", len(prompts_seen) == 2)
    check("spinner 结束后活跃实例已清空", ui.Spinner._active is None)

    print()
    if FAILS:
        print("FAILED: %d 项 -> %s" % (len(FAILS), FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
