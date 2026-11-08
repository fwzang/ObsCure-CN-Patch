# -*- coding: utf-8 -*-
"""pack_o1.py — 把旧版（Tk）安装包里的载荷重新打包成原生安装器格式。

用法:
  python pack_o1.py <旧exe> <setup.exe> <输出exe>

载荷命名归一化（新旧布局不同，统一成一套）：
  bin/...    直接释放到游戏根
  rb/...     仅用于重建 datapack（zh_en.lng + maps_pc/）
  tool/...   obscure-hvp.exe / xdelta.exe
  ode/...    L1_*.patch + data/（仅加强版）
Footer2 = "O1SETUP2" + ver(2) + n + 96 × {name[64], off(u64), size(u64), crc(u32)}
"""
import io
import os
import struct
import sys
import zipfile
import zlib

MAXENT = 96
MAGIC = b'O1SETUP2'

SKIP = {
    'INSTALLER_MANIFEST.json',
    '免责声明.txt',
    'files/免责声明.txt',
    'files/logo_banner.png',
    'files/logo_icon.png',
    'patches/L2_datapack_zh.patch',      # 旧版未使用
}


def map_name(n):
    """旧包内路径 → 新载荷名（None 表示丢弃）。"""
    if n in SKIP:
        return None
    if n == 'obscure-hvp.exe':
        return 'tool/obscure-hvp.exe'
    if n == 'tools/xdelta.exe':
        return 'tool/xdelta.exe'
    if n == 'zh_en.lng':
        return 'rb/zh_en.lng'
    if n.startswith('maps_pc/'):
        return 'rb/' + n
    if n.startswith('files/'):
        return 'bin/' + n[len('files/'):]
    if n.startswith('data/'):
        return 'ode/' + n
    if n.startswith('patches/'):
        return 'ode/' + os.path.basename(n)
    if n.startswith('scripts/'):
        return 'bin/' + n
    return 'bin/' + n


def main():
    old, setup, out = sys.argv[1], sys.argv[2], sys.argv[3]
    ents = []
    with zipfile.ZipFile(old) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            nm = map_name(info.filename)
            if not nm:
                continue
            ents.append((nm, z.read(info)))
    # tool/ 与 ode/ 优先，方便调试；其余按名字排序
    ents.sort(key=lambda t: t[0])

    base = open(setup, 'rb').read()
    off = len(base)
    with io.open(out, 'wb') as f:
        f.write(base)
        recs = []
        for nm, data in ents:
            f.write(data)
            recs.append((nm, off, len(data), zlib.crc32(data) & 0xFFFFFFFF))
            off += len(data)

        footer = MAGIC + struct.pack('<II', 2, len(recs))
        for nm, o, sz, crc in recs:
            b = nm.encode('utf-8')
            assert len(b) < 64, nm
            footer += b + b'\x00' * (64 - len(b)) + struct.pack('<QQI', o, sz, crc)
        footer += b'\x00' * ((MAXENT - len(recs)) * 84)
        assert len(footer) == 16 + MAXENT * 84, len(footer)
        f.write(footer)

    size = os.path.getsize(out)
    print('载荷 %d 条，解压后 %.2f MB' % (len(recs), sum(r[2] for r in recs) / 1048576.0))
    print('安装包 %s  %.2f MB' % (out, size / 1048576.0))
    # 回读校验
    with open(out, 'rb') as f:
        f.seek(size - len(footer))
        assert f.read(len(footer)) == footer, 'footer 回读不一致'
    print('footer 回读: OK (%d B)' % len(footer))
    for nm, o, sz, crc in recs[:6]:
        print('  %-40s %10d' % (nm, sz))
    return 0


if __name__ == '__main__':
    sys.exit(main())
