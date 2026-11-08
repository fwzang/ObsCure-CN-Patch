# -*- coding: utf-8 -*-
"""生成加强版 INSTALLER_MANIFEST.json（v1.00：v0.99 的 30 arcs + 33 地图贴图 + hvp 工具 + 译文）。"""
import json, os, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.join(BASE, "payload_full")

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b: break
            h.update(b)
    return h.hexdigest()

def main():
    arcs = []
    for root, _d, fs in os.walk(PAYLOAD):
        for fn in fs:
            if fn == "INSTALLER_MANIFEST.json":
                continue
            rel = os.path.relpath(os.path.join(root, fn), PAYLOAD).replace("\\", "/")
            arcs.append(rel)
    arcs.sort()
    files = {}
    for a in arcs:
        p = os.path.join(PAYLOAD, a.replace("/", os.sep))
        files[a] = {"size": os.path.getsize(p), "md5": md5(p)}
    n_map = sum(1 for a in arcs if a.startswith("maps_pc/"))
    mf = {"app": "ObsCure 加强汉化版", "ver": "1.00", "mode": "full",
          "build": "20261017", "map_textures": n_map, "files": files}
    out = os.path.join(PAYLOAD, "INSTALLER_MANIFEST.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=1)
    total = sum(v["size"] for v in files.values())
    print("manifest: %d arcs（地图贴图 %d），payload %.2f GB -> %s" % (len(arcs), n_map, total/1073741824.0, out))
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
