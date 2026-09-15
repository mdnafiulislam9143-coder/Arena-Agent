#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
আইকন জেনারেটর — শূন্য ডিপেন্ডেন্সি (PIL নেই, তাই নিজেই PNG লিখি)।

কেন: KaiOS অ্যাপের আইকন ২৪০x৩২০ ডিভাইসে 56x56/112x112 হয়; বড় ছবি রাখলে মেমরি নষ্ট।
আঁকা হয় সুপারস্যাম্পলিং (antialias) দিয়ে, তাই ছোট সাইজেও পরিষ্কার দেখায়।

চালান: python3 tools/make_icons.py
আউটপুট: webapp/icons/icon-56.png, icon-112.png
"""
from __future__ import annotations

import os
import struct
import zlib

BG_TOP = (17, 21, 28)
BG_BOTTOM = (28, 38, 54)
RED = (226, 45, 45)
RED_DARK = (176, 28, 28)
GOLD = (255, 207, 74)
WHITE = (240, 246, 255)


def png_bytes(width: int, height: int, pixels: bytes) -> bytes:
    """pixels = RGB, row-major (width*height*3)।"""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)                                  # filter type 0
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def rounded_rect(x: float, y: float, w: float, h: float, r: float):
    def inside(px: float, py: float) -> bool:
        cx = min(max(px, x + r), x + w - r)
        cy = min(max(py, y + r), y + h - r)
        dx, dy = px - cx, py - cy
        if dx == 0 and dy == 0:
            return x <= px <= x + w and y <= py <= y + h
        return (dx * dx + dy * dy) <= r * r
    return inside


def triangle(p1, p2, p3):
    def sign(a, b, c):
        return (a[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (a[1] - c[1])

    def inside(px: float, py: float) -> bool:
        p = (px, py)
        d1, d2, d3 = sign(p, p1, p2), sign(p, p2, p3), sign(p, p3, p1)
        neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
        pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
        return not (neg and pos)
    return inside


def render(size: int, ss: int = 4) -> bytes:
    """ss = supersampling ফ্যাক্টর (গুণ করা হলে খরচ বাড়ে, কোয়ালিটি বাড়ে)।"""
    s = float(size)
    card = rounded_rect(1.5, 1.5, s - 3, s - 3, s * 0.22)
    play = triangle((s * 0.36, s * 0.27), (s * 0.36, s * 0.73), (s * 0.70, s * 0.50))
    play_shadow = triangle((s * 0.365, s * 0.285), (s * 0.365, s * 0.715), (s * 0.69, s * 0.50))
    bar = rounded_rect(s * 0.22, s * 0.80, s * 0.56, s * 0.075, s * 0.04)

    out = bytearray()
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0]
            hits = 0
            for sy in range(ss):
                for sx in range(ss):
                    px = x + (sx + 0.5) / ss
                    py = y + (sy + 0.5) / ss
                    t = py / s
                    base = tuple(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t for i in range(3))
                    if card(px, py):
                        colour = base
                        if bar(px, py):
                            colour = GOLD
                        elif play(px, py):
                            colour = RED
                        elif play_shadow(px, py):
                            colour = RED_DARK
                        for i in range(3):
                            acc[i] += colour[i]
                        hits += 1
                    else:
                        # কার্ডের বাইরে স্বচ্ছ ধরে অন্ধকার ব্যাকগ্রাউন্ড মেশাই
                        for i in range(3):
                            acc[i] += 8
            total = ss * ss
            out += bytes(int(min(255, max(0, acc[i] / total))) for i in range(3))
    return bytes(out)


def main() -> int:
    outdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "webapp", "icons")
    os.makedirs(outdir, exist_ok=True)
    for size in (56, 112):
        path = os.path.join(outdir, "icon-%d.png" % size)
        with open(path, "wb") as fh:
            fh.write(png_bytes(size, size, render(size)))
        print("লেখা হয়েছে: %s (%d bytes)" % (path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
