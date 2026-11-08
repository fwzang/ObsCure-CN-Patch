# -*- coding: utf-8 -*-
"""把 holstein24 字形表搬进新增的 .wfont 节，并改 grp1 指针。
--verify-only : 只搬运原表（150 条），用于验证结构安全
"""
import struct, os, sys

BASE = r'E:\SteamLibrary\steamapps\common\Obscure'
SRC = os.path.join(BASE, r'hzp_tools\out\Obscure.exe.bak')
DST = os.path.join(BASE, 'Obscure.exe')
IMG = 0x400000
H24_TBL_FO = 0x293F80
GRP1_TBL_IMM = 0x5AB89
GRP1_CNT_IMM = 0x5AB90


def pe_info(d):
    e = struct.unpack_from('<I', d, 0x3C)[0]
    coff = e + 4
    nsec = struct.unpack_from('<H', d, coff + 2)[0]
    optsz = struct.unpack_from('<H', d, coff + 16)[0]
    opt = coff + 20
    return dict(e=e, nsec=nsec, opt=opt, secalign=struct.unpack_from('<I', d, opt + 32)[0],
                filealign=struct.unpack_from('<I', d, opt + 36)[0], sec0=opt + optsz)


def add_section(d, name, blob):
    info = pe_info(d)
    sa, fa = info['secalign'], info['filealign']
    secs = []
    for i in range(info['nsec']):
        o = info['sec0'] + i * 40
        nm = d[o:o+8].rstrip(b'\0').decode()
        vs, va, rs, raw = struct.unpack_from('<IIII', d, o + 8)
        secs.append((nm, va, vs))
    last_va = max(va + vs for _, va, vs in secs)
    new_va = (last_va + sa - 1) // sa * sa
    hdr_end = info['sec0'] + info['nsec'] * 40
    first_raw = min(struct.unpack_from('<I', d, info['sec0'] + i * 40 + 20)[0]
                    for i in range(info['nsec']))
    assert hdr_end + 40 <= first_raw, 'no room for section header'
    out = bytearray(d)
    new_raw = (len(out) + fa - 1) // fa * fa
    out += b'\x00' * (new_raw - len(out))
    out += blob
    out += b'\x00' * ((-len(out)) % fa)
    h = bytearray(40)
    h[0:len(name)] = name.encode()
    struct.pack_into('<I', h, 8, len(blob))
    struct.pack_into('<I', h, 12, new_va)
    struct.pack_into('<I', h, 16, len(blob))
    struct.pack_into('<I', h, 20, new_raw)
    struct.pack_into('<I', h, 36, 0xC0000040)
    out[hdr_end:hdr_end+40] = h
    struct.pack_into('<H', out, info['e'] + 6, info['nsec'] + 1)
    struct.pack_into('<I', out, info['opt'] + 56, (new_va + len(blob) + sa - 1) // sa * sa)
    return out, IMG + new_va, new_raw


def main():
    d0 = open(SRC, 'rb').read()
    tbl = bytearray(d0[H24_TBL_FO:H24_TBL_FO + 150 * 10])
    out, va, raw = add_section(bytearray(d0), '.wfont', bytes(tbl))
    struct.pack_into('<I', out, GRP1_TBL_IMM, va)
    struct.pack_into('<I', out, GRP1_CNT_IMM, 150)
    print(f'table @ VA {va:#x} (file {raw:#x}), 150 records, grp1 repointed')
    open(DST, 'wb').write(bytes(out))
    print('wrote', DST, len(out))


if __name__ == '__main__':
    main()
