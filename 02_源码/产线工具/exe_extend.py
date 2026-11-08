# -*- coding: utf-8 -*-
"""exe 扩展：把 grp1(holstein24) 的字形表搬到文件尾部新区域，
同时重定位 .data 的原始数据。目的：验证 exe 结构手术安全 + 为中文条目腾出空间。"""
import struct, os

BASE = r'E:\SteamLibrary\steamapps\common\Obscure'
SRC = os.path.join(BASE, r'hzp_tools\out\Obscure.exe.bak')
DST = os.path.join(BASE, 'Obscure.exe')
IMG = 0x400000
H24_TBL_FO = 0x293F80
H24_CNT = 150
GRP1_TBL_IMM = 0x5AB89     # mov [esi+0x3c], imm32
GRP1_CNT_IMM = 0x5AB90     # mov [esi+0x40], imm32


def sec_info(d):
    e = struct.unpack_from('<I', d, 0x3C)[0]
    coff = e + 4
    nsec = struct.unpack_from('<H', d, coff + 2)[0]
    optsz = struct.unpack_from('<H', d, coff + 16)[0]
    sec0 = coff + 20 + optsz
    secs = []
    for i in range(nsec):
        o = sec0 + i * 40
        nm = d[o:o+8].rstrip(b'\0').decode()
        vs, va, rs, raw = struct.unpack_from('<IIII', d, o + 8)
        secs.append(dict(off=o, name=nm, va=va, vsize=vs, rsize=rs, raw=raw))
    return secs


def build(extra=b''):
    d = bytearray(open(SRC, 'rb').read())
    secs = sec_info(d)
    data = next(s for s in secs if s['name'] == '.data')
    tail = bytes(d)
    pad = (-len(tail)) % 0x200
    new_start = len(tail) + pad
    data_raw = bytes(d[data['raw']:data['raw'] + data['rsize']])

    block = data_raw + extra
    out = bytearray(tail) + b'\x00' * pad + block
    out += b'\x00' * ((-len(out)) % 0x200)
    struct.pack_into('<I', out, data['off'] + 16, len(block))
    struct.pack_into('<I', out, data['off'] + 20, new_start)

    tbl_file = new_start + data['rsize']            # extra 的起始文件偏移
    tbl_va = IMG + data['va'] + data['rsize']       # extra 的起始 VA
    print(f'.data raw -> {new_start:#x} (rsize {len(block):#x})')
    print(f'extra  -> file {tbl_file:#x}  VA {tbl_va:#x}')
    return out, tbl_va, tbl_file


def main():
    # 1) 取原表
    d0 = open(SRC, 'rb').read()
    tbl = bytearray(d0[H24_TBL_FO:H24_TBL_FO + H24_CNT * 10])
    assert struct.unpack_from('<5H', tbl, 0)[0] == 32, 'table sanity failed'
    print('orig h24 table:', H24_CNT, 'records,', len(tbl), 'bytes')

    # 2) 构造 extra：新表（本步骤=原样搬迁）
    extra = bytes(tbl)
    out, tbl_va, tbl_file = build(extra)

    # 3) 改 grp1 指针/条数
    struct.pack_into('<I', out, GRP1_TBL_IMM, tbl_va)
    struct.pack_into('<I', out, GRP1_CNT_IMM, H24_CNT)
    print(f'grp1 table -> {tbl_va:#x}, count {H24_CNT}')

    # 4) 校验新表内容
    assert bytes(out[tbl_file:tbl_file + 10]) == bytes(tbl[:10])
    open(DST, 'wb').write(bytes(out))
    print('wrote', DST, len(out))


if __name__ == '__main__':
    main()
