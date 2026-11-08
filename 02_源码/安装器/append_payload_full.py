# -*- coding: utf-8 -*-
"""payload_full/ → zip → 追加到 dist exe 尾部（单文件）。只能对新鲜骨架执行一次！"""
import os, sys, time, zipfile, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.join(BASE, "payload_full")
DIST = os.path.join(BASE, "dist")
EXE_NAME = "ObsCure加强汉化版安装器_v1.00.exe"
STORE_EXT = {".hvp", ".raw", ".asi", ".dll", ".exe", ".patch", ".bmp", ".tga", ".png"}

def main():
    exe_src = os.path.join(DIST, EXE_NAME)
    assert os.path.isfile(exe_src), "先跑 PyInstaller 生成骨架: " + exe_src
    t0 = time.time()
    zip_path = os.path.join(BASE, "payload_full.zip")
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for root, _d, fs in os.walk(PAYLOAD):
            for fn in fs:
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, PAYLOAD).replace("\\", "/")
                if rel == "INSTALLER_MANIFEST.json":
                    continue   # manifest 也要进包，但不能重复 —— 见下
    # 上面预扫只为计数；正式打包（含 manifest，放最前）
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.write(os.path.join(PAYLOAD, "INSTALLER_MANIFEST.json"), "INSTALLER_MANIFEST.json")
        for root, _d, fs in os.walk(PAYLOAD):
            for fn in sorted(fs):
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, PAYLOAD).replace("\\", "/")
                if rel == "INSTALLER_MANIFEST.json":
                    continue
                ext = os.path.splitext(fn)[1].lower()
                comp = zipfile.ZIP_STORED if ext in STORE_EXT else zipfile.ZIP_DEFLATED
                z.write(p, rel, compress_type=comp)
                n += 1
    exe = open(exe_src, "rb").read()
    assert b"PK\x05\x06" not in exe[-100000:], "骨架已含 payload？请重新 PyInstaller"
    with open(zip_path, "rb") as f:
        payload = f.read()
    out = os.path.join(DIST, EXE_NAME)
    with open(out, "wb") as o:
        o.write(exe); o.write(payload)
    h = hashlib.md5(open(out, "rb").read()).hexdigest()
    print("payload 文件 : %d 个" % n)
    print("exe 骨架     : %s B" % f"{len(exe):,}")
    print("payload zip  : %s B" % f"{len(payload):,}")
    print("最终单文件   : %.2f GB" % ((len(exe) + len(payload)) / 1073741824.0))
    print("耗时         : %.1f s" % (time.time() - t0))
    print("md5          : %s" % h)
    with open(os.path.join(DIST, "INSTALLER_MD5.txt"), "w") as f:
        f.write("%s *%s\n" % (h, EXE_NAME))
    return 0

if __name__ == "__main__":
    sys.exit(main())
