#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
patch_version_101.py  ·  2026-10-26  （v2：只改文件头 + 排除 payload 区间）
把安装器的版本号本体改到 1.01：

  A. UI 文本（UTF-16）：v1.00 / V1.00  ->  v1.01 / V1.01   （等长）
  B. 版本资源字符串：1.0.0.0 -> 1.0.0.1（FileVersion / ProductVersion）
  C. VS_FIXEDFILEINFO：FileVersionLS / ProductVersionLS -> 1 （即 1.0.0.1）

安全设计
  · 只在**文件头 32 MB** 内查找（版本串/资源都在最前 256 KB）
  · 先解析 O1SETUP2 footer，得到全部 payload 区间，**拒绝在 payload 内做任何替换**
  · 全部等长替换，文件大小不变
  · 改完复核：新值读回、旧值残留为 0、payload 全量 crc 仍通过
"""
import struct, os, sys, zlib, hashlib

PKG = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18\01_安装包'
FILES = [
    ('精简版', 'ObsCure汉化安装器_v1.01.exe'),
    ('加强版', 'ObsCure加强汉化版安装器_v1.01.exe'),
]
FOOTER_LEN, STRIDE = 8080, 84
HEAD = 32 * 1024 * 1024
FFI_SIG = struct.pack('<I', 0xFEEF04BD)


def u16(s):
    return s.encode('utf-16-le')


def footer_entries(fh, total):
    fh.seek(total - FOOTER_LEN)
    tail = fh.read()
    assert tail[:8] == b'O1SETUP2', 'footer 魔数不对'
    ver, n = struct.unpack_from('<II', tail, 8)
    ents = []
    for i in range(n):
        o = 16 + i * STRIDE
        nm = tail[o:o + 64].split(b'\x00')[0].decode('latin1')
        off, size, crc = struct.unpack_from('<QQI', tail, o + 64)
        ents.append((nm, off, size, crc))
    return n, ents


def inside_payload(pos, ents):
    return any(off <= pos < off + size for _, off, size, _ in ents)


def patch_file(path):
    print('=== %s ===' % os.path.basename(path))
    total = os.path.getsize(path)
    with open(path, 'r+b') as fh:
        n, ents = footer_entries(fh, total)
        fh.seek(0)
        head = bytearray(fh.read(min(HEAD, total)))

        changes = []

        # ---- A. v1.00 / V1.00 -> v1.01 / V1.01
        i, cnt = 0, 0
        while True:
            j = head.find(u16('1.00'), i)
            if j < 0:
                break
            prev = bytes(head[j - 2:j])
            if prev in (u16('v'), u16('V')):
                assert not inside_payload(j, ents), '命中 payload 区间，拒绝：@0x%X' % j
                head[j:j + 8] = u16('1.01')
                changes.append('UI  @0x%X  %s1.00 -> %s1.01'
                               % (j, prev.decode('utf-16-le'), prev.decode('utf-16-le')))
                cnt += 1
            i = j + 8
        assert cnt >= 3, 'UI 版本串命中过少（%d），可能结构不同，拒绝继续' % cnt

        # ---- B. 版本资源字符串
        for key in ('FileVersion', 'ProductVersion'):
            k = head.find(u16(key))
            assert k >= 0, '未找到资源键 %s' % key
            j = head.find(u16('1.0.0.0'), k)
            assert 0 <= j - k < 160, '%s 后未见 1.0.0.0' % key
            assert not inside_payload(j, ents)
            head[j:j + 14] = u16('1.0.0.1')
            changes.append('VER @0x%X  %s = 1.0.0.0 -> 1.0.0.1' % (j, key))

        # ---- C. VS_FIXEDFILEINFO
        x = head.find(FFI_SIG)
        assert x >= 0, '未找到 VS_FIXEDFILEINFO'
        ms, ls, pms, pls = struct.unpack_from('<IIII', head, x + 8)
        assert (ms, ls, pms, pls) == (0x00010000, 0, 0x00010000, 0), \
            '固定版本块非 1.0.0.0（%08X %08X %08X %08X）' % (ms, ls, pms, pls)
        struct.pack_into('<IIII', head, x + 8, 0x00010000, 1, 0x00010000, 1)
        changes.append('FFI @0x%X  1.0.0.0 -> 1.0.0.1' % (x + 8))

        # 写回文件头
        fh.seek(0)
        fh.write(bytes(head))
        fh.flush()

        # ---- 复核
        fh.seek(0)
        head2 = fh.read(min(HEAD, total))
        n101 = head2.count(u16('v1.01')) + head2.count(u16('V1.01'))
        n100 = head2.count(u16('v1.00')) + head2.count(u16('V1.00'))
        bad = 0
        for nm, off, size, crc in ents:
            fh.seek(off)
            blob = fh.read(size)
            if (zlib.crc32(blob) & 0xFFFFFFFF) != crc:
                bad += 1
                print('    [!!] payload crc 不符:', nm)
        fh.seek(0)
        allbytes = fh.read()
        md5 = hashlib.md5(allbytes).hexdigest()

    for c in changes:
        print('   ', c)
    print('    复核 UI：v1.01 %d 处，残留 v1.00 %d 处' % (n101, n100))
    print('    复核 payload：%d 条，crc 异常 %d 条' % (n, bad))
    print('    文件大小 %d（未变）  md5 %s' % (total, md5))
    ok = (n100 == 0) and (bad == 0) and (os.path.getsize(path) == total)
    return ok


def main():
    allok = True
    for ed, fn in FILES:
        p = os.path.join(PKG, ed, fn)
        if not os.path.exists(p):
            print('缺少:', p); allok = False; continue
        allok &= patch_file(p)
        print()
    print('总体：', '全部通过' if allok else '存在失败')
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
