# -*- coding: utf-8 -*-
"""从游戏 logoload.bmp 生成安装器多尺寸图标 app_icon.ico。
用法: python make_app_icon.py  （源: E:/SteamLibrary/.../Obscure/logoload.bmp）"""
import os
from PIL import Image

SRC = r"E:/SteamLibrary/steamapps/common/Obscure/logoload.bmp"
OUT_ICO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")

src = Image.open(SRC).convert("RGBA")
w, h = src.size
px = src.load()
for y in range(h):
    for x in range(w):
        r, g, b, a = px[x, y]
        if r < 40 and g < 40 and b < 40:
            px[x, y] = (r, g, b, 0)          # 黑底转透明
S = 256
canvas = Image.new("RGBA", (S, S), (16, 19, 28, 255))
tw = int(S * 0.84); th = int(h * tw / w)
canvas.alpha_composite(src.resize((tw, th), Image.LANCZOS),
                       ((S - tw) // 2, (S - th) // 2))
canvas.save(OUT_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                            (64, 64), (128, 128), (256, 256)])
print("图标已生成:", OUT_ICO)
