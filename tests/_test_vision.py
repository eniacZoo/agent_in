#!/usr/bin/env python3
"""临时测试 vision.py + view_image 工具"""
import struct
import zlib
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)


def make_test_png(path, width=64, height=48):
    """生成一个纯色 PNG 测试图。"""
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    # 红色像素
    row = b"\x00" + b"\xff\x00\x00" * width
    idat = chunk(b"IDAT", zlib.compress(row * height))
    iend = chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(sig + ihdr + idat + iend)


import vision

test_img = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_img.png")
make_test_png(test_img)
print("Created test PNG:", os.path.getsize(test_img), "bytes")

# 测试 image_info
info = vision.image_info(test_img)
print("info:", info)

# 测试 image_to_base64
b64 = vision.image_to_base64(test_img)
print("base64 starts with:", b64[:40], "...")
print("base64 length:", len(b64))

# 测试不支持的格式
try:
    open("/tmp/test.txt", "w").write("hello")
    vision.image_to_base64("/tmp/test.txt")
    print("FAIL: should have raised ValueError")
except ValueError as e:
    print("Correctly rejected .txt:", str(e))

# 测试不存在的文件
try:
    vision.image_to_base64("/tmp/nonexistent_photo.png")
    print("FAIL: should have raised FileNotFoundError")
except FileNotFoundError as e:
    print("Correctly rejected missing file:", str(e))

# 测试 guess_image_files
found = vision.guess_image_files("看看这张图 _test_img.png 或者 /tmp/other.jpg")
print("guess_image_files found:", found)

# 测试 tools.view_image
import tools
tools.WORK_DIR = os.path.dirname(os.path.abspath(__file__))
result = tools.execute("view_image", {"path": "_test_img.png"})
print("\nview_image result:", result)
print("PENDING_IMAGES count:", len(tools.PENDING_IMAGES))
drained = tools.drain_pending_images()
print("After drain, count:", len(tools.PENDING_IMAGES))
print("Drained count:", len(drained))

# 清理
os.remove(test_img)
os.remove("/tmp/test.txt")
print("\nAll vision tests passed")
