# -*- coding: utf-8 -*-
"""生成差分版 INSTALLER_MANIFEST.json（v1.00：含 maps_pc/ 33 件地图贴图）。"""
import json
import os
import hashlib
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.join(os.path.dirname(BASE), "payload")

FILES = [
    "version.dll", "big_atlas_c.raw", "table_c.bin",
    "免责声明.txt",
    "obscure-hvp.exe", "zh_en.lng",
    "dinput8.dll", "scripts/obscure.asi", "scripts/main.patch",
]
MAP_DIR = "maps_pc"


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    map_tex = []
    md = os.path.join(PAYLOAD, MAP_DIR)
    if os.path.isdir(md):
        for fn in sorted(os.listdir(md)):
            if fn.lower().endswith((".bmp", ".tga")):
                map_tex.append(MAP_DIR + "/" + fn)
    arcs = FILES + map_tex
    missing = [a for a in arcs
               if not os.path.isfile(os.path.join(PAYLOAD, a.replace("/", os.sep)))]
    if missing:
        print("缺 payload:", missing)
        return 1
    files = {}
    for a in arcs:
        p = os.path.join(PAYLOAD, a.replace("/", os.sep))
        files[a] = {"size": os.path.getsize(p), "md5": md5(p)}
    mf = {
        "app": "ObsCure 汉化补丁安装器",
        "ver": "1.00",
        "mode": "diff",
        "build": "20261017",
        "map_textures": len(map_tex),
        "files": files,
    }
    out = os.path.join(PAYLOAD, "INSTALLER_MANIFEST.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=1)
    total = sum(v["size"] for v in files.values())
    print("manifest: %d arcs（含地图贴图 %d 件），payload 合计 %.1f MB -> %s"
          % (len(arcs), len(map_tex), total / 1048576.0, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
