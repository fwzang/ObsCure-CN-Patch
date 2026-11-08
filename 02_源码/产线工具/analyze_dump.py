#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_dump.py  ·  崩溃转储快速定位
用法：
    python analyze_dump.py <dump.mdmp> [version.dll]
不给 dll 路径时默认 D:\\SteamLibrary\\steamapps\\common\\Obscure\\version.dll

输出：异常码 / 读写方向 / 目标地址 / 出错模块 / 模块内偏移 / 对应文件偏移
（模块内偏移 -> 文件偏移 需要 PE 节表映射，本脚本自动换算）
"""
import sys, struct, os

DEFAULT_DLL = r'D:\SteamLibrary\steamapps\common\Obscure\version.dll'


def parse_dump(p):
    d = open(p, 'rb').read()
    assert d[:4] == b'MDMP', 'not a minidump'
    nstreams, dirrva = struct.unpack_from('<II', d, 8)
    streams = {}
    for i in range(nstreams):
        st, dsz, rva = struct.unpack_from('<III', d, dirrva + i * 12)
        streams[st] = (dsz, rva)
    out = {}
    if 6 in streams:
        _, rva = streams[6]
        tid, _ = struct.unpack_from('<II', d, rva)
        code, flags, _erec, eaddr, nparam, _u = struct.unpack_from('<IIQQII', d, rva + 8)
        params = struct.unpack_from('<15Q', d, rva + 8 + 32)
        out.update(tid=tid, code=code, flags=flags, addr=eaddr, params=params[:nparam])
    if 4 in streams:
        _, rva = streams[4]
        n, = struct.unpack_from('<I', d, rva)
        off = rva + 4
        mods = []
        for _ in range(n):
            base, size, cksum, tds, namerva = struct.unpack_from('<QIIII', d, off)
            ln, = struct.unpack_from('<I', d, namerva)
            name = d[namerva + 4:namerva + 4 + ln].decode('utf-16-le', 'replace')
            mods.append((base, size, name))
            off += 108
        out['mods'] = mods
    return out


def rva_to_file(dll, rva):
    d = open(dll, 'rb').read()
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    n = struct.unpack_from('<H', d, pe + 6)[0]
    optsz = struct.unpack_from('<H', d, pe + 20)[0]
    first = pe + 24 + optsz
    for i in range(n):
        o = first + i * 40
        vs, va, rs, ra = struct.unpack_from('<IIII', d, o + 8)
        if va <= rva < va + max(vs, rs):
            return ra + (rva - va)
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    dump = sys.argv[1]
    dll = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DLL
    r = parse_dump(dump)
    print('dump     :', os.path.basename(dump))
    print('exception: 0x%08X  flags=0x%X  thread=%d' % (r['code'], r['flags'], r['tid']))
    acc = {0: 'read', 1: 'WRITE', 8: 'execute'}.get(r['params'][0], '?') if r['params'] else '?'
    print('access   : %s   target addr = 0x%X' % (acc, r['params'][1] if len(r['params']) > 1 else 0))
    print('eip      : 0x%016X' % r['addr'])
    hit = None
    for base, size, name in r['mods']:
        if base <= r['addr'] < base + size:
            hit = (base, size, name)
            break
    if not hit:
        print('faulting module: NOT IN ANY MODULE  -> 跳到野地址/栈被破坏')
        return 0
    base, size, name = hit
    off = r['addr'] - base
    print('module   : %s' % name)
    print('base     : 0x%X  size=0x%X  offset=+0x%X' % (base, size, off))
    if os.path.normcase(os.path.abspath(dll)) == os.path.normcase(os.path.abspath(name)):
        fo = rva_to_file(dll, off)
        if fo is not None:
            print('file off : 0x%X   (在同一份 DLL 里按此偏移反汇编)' % fo)
    print('ASLR     : %s' % ('重定位生效（base != 0x10000000）'
                             if base != 0x10000000 else '未重定位'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
