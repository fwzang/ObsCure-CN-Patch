# -*- coding: utf-8 -*-
"""extract_icon.py — 从 PE 的 RT_GROUP_ICON/RT_ICON 还原多尺寸 .ico。

用法: python extract_icon.py <game.exe> <out.ico>
"""
import struct
import sys


def main():
    src, out = sys.argv[1], sys.argv[2]
    d = open(src, 'rb').read()
    e = struct.unpack_from('<I', d, 0x3C)[0]
    coff = e + 4
    nsec = struct.unpack_from('<H', d, coff + 2)[0]
    optsz = struct.unpack_from('<H', d, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from('<H', d, opt)[0]
    dd = opt + (96 if magic == 0x10b else 112)
    rva, rsz = struct.unpack_from('<II', d, dd + 2 * 8)

    secs = []
    so = opt + optsz
    for i in range(nsec):
        s = so + i * 40
        vsz, va, rsz2, raw = struct.unpack_from('<IIII', d, s + 8)
        secs.append((va, max(vsz, rsz2), raw))

    def r2o(r):
        for va, sz, raw in secs:
            if va <= r < va + sz:
                return raw + (r - va)
        return None

    base = r2o(rva)
    assert base is not None, 'no resource section'

    def dir_entries(off):
        """off 为节内偏移，返回 [(id, 子项节内偏移, 是否目录)]"""
        o = base + off
        nn, ni = struct.unpack_from('<HH', d, o + 12)
        out = []
        for i in range(nn + ni):
            nm, sub = struct.unpack_from('<II', d, o + 16 + i * 8)
            out.append((nm, sub & 0x7FFFFFFF, bool(sub & 0x80000000)))
        return out

    icons, groups = {}, {}
    for typ, toff, isdir in dir_entries(0):
        if typ not in (3, 14) or not isdir:
            continue
        for iid, ioff, iisdir in dir_entries(toff):
            if not iisdir:
                continue
            for lang, loff, _ in dir_entries(ioff):
                doff, dsize = struct.unpack_from('<II', d, base + loff)
                data = d[r2o(doff):r2o(doff) + dsize]
                if typ == 3:
                    icons[iid] = data
                else:
                    groups[iid] = data
                break

    if not groups:
        print('no group icon in', src)
        return 1
    gid = sorted(groups)[0]
    g = groups[gid]
    _, count = struct.unpack_from('<HH', g, 0)
    print('%s: group=%d, %d 个尺寸' % (src.split('\\')[-1], gid, count))

    imgs = []
    for i in range(count):
        o = 6 + i * 14
        w, h, ncol, res, planes, bpp, size, iid = struct.unpack_from('<BBBBHHIH', g, o)
        data = icons.get(iid)
        if data is None:
            print('  ! 缺 RT_ICON', iid)
            continue
        imgs.append((w, h, ncol, res, planes, bpp, data))
        print('  %3dx%-3d %2dbpp %8d B' % (w or 256, h or 256, bpp, len(data)))

    imgs.sort(key=lambda t: (t[0] or 256))
    hdr = struct.pack('<HHH', 0, 1, len(imgs))
    off = 6 + 16 * len(imgs)
    dirs, blob = b'', b''
    for w, h, ncol, res, planes, bpp, data in imgs:
        dirs += struct.pack('<BBBBHHII', w, h, ncol, res, planes, bpp, len(data), off)
        blob += data
        off += len(data)
    open(out, 'wb').write(hdr + dirs + blob)
    print('→ %s (%d B)' % (out, 6 + 16 * len(imgs) + len(blob)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
