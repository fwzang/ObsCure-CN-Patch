# -*- coding: utf-8 -*-
"""
把 payload/ 压成 zip 并追加到 exe 尾部 → 单文件安装包。

为什么能追加：zip 的 central directory 在文件尾，且 zip reader 支持
"前置垃圾数据"（self-extracting archive 原理），因此 exe_bytes + zip_bytes
拼接后，zipfile.ZipFile(exe) 可直接读到全部 payload。安装器启动秒开，
不需要解压到临时目录。
"""
import os
import sys
import time
import zipfile
import hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
REL = os.path.dirname(BASE)
PAYLOAD = os.path.join(REL, "payload")
DIST = os.path.join(REL, "dist")
EXE_NAME = "ObsCure汉化安装器_v1.00.exe"

# 已压缩/不可压缩的格式用 STORED：省 CPU 且几乎不增体积
STORE_EXT = {".hvp", ".raw", ".asi", ".dll", ".exe"}


def collect():
    out = []
    for root, _d, files in os.walk(PAYLOAD):
        for fn in files:
            p = os.path.join(root, fn)
            out.append((os.path.relpath(p, PAYLOAD).replace(os.sep, "/"), p))
    return sorted(out)


def main():
    exe_src = os.path.join(DIST, EXE_NAME)
    if not os.path.isfile(exe_src):
        print("[FAIL] exe 不存在:", exe_src)
        return 1

    tmpzip = os.path.join(REL, "_payload_tmp.zip")
    files = collect()
    t0 = time.time()
    with zipfile.ZipFile(tmpzip, "w", zipfile.ZIP_DEFLATED, compresslevel=9,
                         allowZip64=True) as z:
        for rel, p in files:
            ext = os.path.splitext(rel)[1].lower()
            ct = zipfile.ZIP_STORED if ext in STORE_EXT else zipfile.ZIP_DEFLATED
            zi = zipfile.ZipInfo(rel, date_time=(2026, 10, 17, 2, 0, 0))
            zi.compress_type = ct
            zi.external_attr = 0o600 << 16
            with open(p, "rb") as f:
                z.writestr(zi, f.read(), compress_type=ct, compresslevel=9)
    with open(tmpzip, "rb") as f:
        zipdata = f.read()
    os.remove(tmpzip)

    with open(exe_src, "rb") as f:
        exedata = f.read()
    with open(exe_src, "wb") as f:
        f.write(exedata)
        f.write(zipdata)

    with zipfile.ZipFile(exe_src) as z:
        assert "INSTALLER_MANIFEST.json" in z.namelist()
        assert z.testzip() is None

    size = os.path.getsize(exe_src)
    h = hashlib.md5(open(exe_src, "rb").read()).hexdigest()
    print("payload 文件 : %d 个" % len(files))
    print("exe 骨架     : %d B" % len(exedata))
    print("payload zip  : %d B" % len(zipdata))
    print("最终单文件   : %.2f MB" % (size / 1048576.0))
    print("耗时         : %.1f s" % (time.time() - t0))
    print("md5          :", h)
    with open(os.path.join(DIST, "INSTALLER_MD5.txt"), "w", encoding="utf-8") as f:
        f.write("%s *%s\n" % (h, EXE_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())