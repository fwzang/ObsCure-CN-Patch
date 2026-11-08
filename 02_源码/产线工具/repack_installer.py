#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
repack_installer.py · 2026-10-26
把「新编译的安装器骨架」与「原 v1.01 的载荷」重新合成，产出修好的 v1.02。

O1SETUP2 结构（实测）
  footer（贴文件尾，共 8080 字节）
    "O1SETUP2"(8) + ver u32 + n u32 + n*84 + 零填充
    record = name[64] + off u64 + size u64 + crc u32
  payload 区 = [min(off), 文件尾-8080)，紧跟在骨架之后

重打包只做一件事：所有 off 平移 delta = len(新骨架) - 旧 payload 起点。
crc 不变（内容不变）。
"""
import struct, os, sys, zlib

FOOTER_LEN, STRIDE = 8080, 84
CHUNK = 8 << 20


def parse_footer(fh, total):
    fh.seek(total - FOOTER_LEN)
    tail = fh.read()
    assert tail[:8] == b'O1SETUP2', 'magic not found'
    ver, n = struct.unpack_from('<II', tail, 8)
    ents = []
    for i in range(n):
        o = 16 + i * STRIDE
        nm = tail[o:o + 64].split(b'\x00')[0].decode('latin1')
        off, size, crc = struct.unpack_from('<QQI', tail, o + 64)
        ents.append([nm, off, size, crc, o])
    return ver, n, bytearray(tail), ents


def build(old_exe, new_skel, out_exe):
    total_old = os.path.getsize(old_exe)
    skel = open(new_skel, 'rb').read()
    print('  skeleton : %d bytes  (%s)' % (len(skel), os.path.basename(new_skel)))

    with open(old_exe, 'rb') as fh:
        ver, n, tail, ents = parse_footer(fh, total_old)
        payload_start = min(e[1] for e in ents)
        payload_end = total_old - FOOTER_LEN
        assert max(e[1] + e[2] for e in ents) == payload_end, \
            '载荷不连续：max(off+size)=%d, footer@%d' % (max(e[1] + e[2] for e in ents), payload_end)
        print('  old payload : %d .. %d  (%d bytes), entries=%d'
              % (payload_start, payload_end, payload_end - payload_start, n))

        delta = len(skel) - payload_start
        print('  delta       : %+d 字节' % delta)

        # 平移偏移
        for e in ents:
            struct.pack_into('<Q', tail, e[4] + 64, e[1] + delta)

        with open(out_exe, 'wb') as out:
            out.write(skel)
            fh.seek(payload_start)
            left = payload_end - payload_start
            while left > 0:
                buf = fh.read(min(CHUNK, left))
                if not buf:
                    break
                out.write(buf)
                left -= len(buf)
            out.write(bytes(tail))
    return ver, n, ents, delta


def verify(old_exe, new_exe):
    """逐条比对：新包的 blob 内容 md5 与旧包一致，且 crc 自洽。"""
    import hashlib
    def blobs(path):
        total = os.path.getsize(path)
        with open(path, 'rb') as fh:
            ver, n, tail, ents = parse_footer(fh, total)
            res = {}
            for nm, off, size, crc, _ in ents:
                fh.seek(off)
                data = fh.read(size)
                res[nm] = (hashlib.md5(data).hexdigest(),
                           (zlib.crc32(data) & 0xFFFFFFFF) == crc)
        return res

    a, b = blobs(old_exe), blobs(new_exe)
    assert set(a) == set(b), '条目名不一致'
    bad = 0
    for nm in a:
        if a[nm][0] != b[nm][0]:
            print('    [!!] 内容不同:', nm); bad += 1
        if not b[nm][1]:
            print('    [!!] crc 不自洽:', nm); bad += 1
    print('  复核：%d 条，异常 %d 条' % (len(a), bad))
    return bad == 0


def main():
    if len(sys.argv) < 4:
        print(__doc__); return 2
    old, skel, out = sys.argv[1], sys.argv[2], sys.argv[3]
    print('=== %s ===' % os.path.basename(out))
    ver, n, ents, delta = build(old, skel, out)
    ok = verify(old, out)
    print('  大小 %d -> %d' % (os.path.getsize(old), os.path.getsize(out)))
    print('  结果：', '通过' if ok else '失败')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
