#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision.py — 图片处理模块（v3.0）

功能：
- 图片文件读取 + base64 编码
- 格式校验（仅允许常见图片格式）
- 大小限制；超限时用 vendor/PIL 缩图
- MIME 类型推断
- 多图管理
"""
import base64
import os
import sys
from io import BytesIO
from pathlib import Path


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_EDGE = 2048  # 送模型前长边上限
MAX_IMAGES_PER_REQUEST = 4  # 单次请求最多 4 张图

_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _try_pil():
    """加载 Pillow（vendor/）。失败返回 None。"""
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        from PIL import Image
        return Image
    except Exception:
        return None


def maybe_downscale(path, max_bytes=None, max_edge=None):
    """
    若文件超过 max_bytes 或长边超过 max_edge，缩成 JPEG 字节。
    返回 (data, mime, note)；无需缩则 data 为原文件、note 为空。
    """
    max_bytes = MAX_IMAGE_SIZE if max_bytes is None else max_bytes
    max_edge = MAX_EDGE if max_edge is None else max_edge
    p = Path(path)
    raw = p.read_bytes()
    size = len(raw)
    ext = p.suffix.lower()
    mime = _MIME_MAP.get(ext, "image/jpeg")

    Image = _try_pil()
    im = None
    need = size > max_bytes
    if Image is not None:
        try:
            im = Image.open(BytesIO(raw))
            w, h = im.size
            if max(w, h) > max_edge:
                need = True
        except Exception:
            im = None

    if not need:
        return raw, mime, ""

    if Image is None:
        raise ValueError(
            "图片过大: {:.1f}MB（上限 {:.1f}MB）。Pillow 不可用，请缩小后重试。".format(
                size / 1024 / 1024, max_bytes / 1024 / 1024
            )
        )

    if im is None:
        im = Image.open(BytesIO(raw))
    im = im.convert("RGB")
    if max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge))

    data = None
    for q in (85, 75, 65, 55, 40):
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            break
    if data is None or len(data) > max_bytes:
        raise ValueError("图片过大且无法缩到上限内，请缩小后重试。")
    note = "原图 {:.1f}MB，已缩到 {}KB".format(
        size / 1024 / 1024, max(1, len(data) // 1024)
    )
    return data, "image/jpeg", note


def _validate_image_path(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("图片不存在: {}".format(path))
    if not p.is_file():
        raise ValueError("路径不是文件: {}".format(path))
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        supported = ", ".join(sorted(SUPPORTED_EXTS))
        raise ValueError(
            "不支持的图片格式: {}（支持: {}）".format(ext, supported)
        )
    return p


def encode_for_llm(path):
    """
    编码为 data URL。超限时先 maybe_downscale。
    返回 (data_url, note)，note 为空表示未缩图。
    """
    p = _validate_image_path(path)
    data, mime, note = maybe_downscale(str(p))
    b64 = base64.b64encode(data).decode("ascii")
    return "data:{};base64,{}".format(mime, b64), note


# ---------------------------------------------------------------------------
# 核心接口
# ---------------------------------------------------------------------------
def image_to_base64(path):
    """
    读取图片文件，返回 data URL 格式的 base64 字符串。

    返回: "data:image/png;base64,iVBORw0KGgo..."
    异常: FileNotFoundError / ValueError
    """
    url, _note = encode_for_llm(path)
    return url


def image_info(path):
    """
    获取图片基本信息（不读取内容）。

    返回: {"path": str, "name": str, "ext": str, "size_bytes": int, "size_kb": int}
    异常: FileNotFoundError / ValueError
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("图片不存在: {}".format(path))
    if not p.is_file():
        raise ValueError("路径不是文件: {}".format(path))

    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError("不支持的图片格式: {}".format(ext))

    size = p.stat().st_size
    return {
        "path": str(p.resolve()),
        "name": p.name,
        "ext": ext,
        "size_bytes": size,
        "size_kb": max(1, size // 1024),
    }


def encode_images(paths):
    """
    批量编码多张图片。

    参数:
        paths: list[str] — 图片路径列表

    返回:
        list[str] — data URL 列表

    异常:
        ValueError — 超过 MAX_IMAGES_PER_REQUEST
    """
    if len(paths) > MAX_IMAGES_PER_REQUEST:
        raise ValueError(
            "单次最多 {} 张图片（当前 {} 张）".format(
                MAX_IMAGES_PER_REQUEST, len(paths)
            )
        )

    results = []
    for p in paths:
        results.append(image_to_base64(p))
    return results


def is_supported_image(path):
    """检查路径是否为支持的图片文件（不读取内容）。"""
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return False
        return p.suffix.lower() in SUPPORTED_EXTS
    except Exception:
        return False


def guess_image_files(text):
    """
    从用户文本中识别可能的图片路径。
    简单的启发式：检测以图片扩展名结尾的路径样 token。

    返回: list[str]
    """
    import re
    pattern = r'(?:[\w./~-]+\.(?:png|jpg|jpeg|gif|webp|bmp))'
    matches = re.findall(pattern, text, re.IGNORECASE)
    # 过滤：仅保留实际存在的文件
    results = []
    for m in matches:
        if os.path.isfile(m):
            if os.path.splitext(m)[1].lower() in SUPPORTED_EXTS:
                results.append(m)
    return results
