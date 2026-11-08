# -*- coding: utf-8 -*-
"""把Steam 版 en.lng 解析成 TSV（latin-1 模式，已由探针 7/7 验证判别正确）。

注意：末条记录无6 字节 tail。
输出：out/corpus/steam_en.tsv
"""
import os, struct, csv

BASE = os.path.dirname(os.path.abspath(__file__))   # hzp_tools/
ROOT = os.path.dirname(BASE)
SRC   = os.path.join(BASE, 'out', 'hvptool_test', 'patch_probe3', '_texts', 'en.lng')
#注意：out/cn_retail/lng/STEAM_en.lng（161554）实测是【法语】副本
# （内容为 "Coéquipier : Josh" / "C'est une batterie."），不是英文，禁止用作对齐基准。
# 参考语言/ 目录里也只有 de/fr/it/sp，没有 en。
# 真正的英文 en.lng 位于 hvptool_test/*/_texts/en.lng（149816~149876，OBS-1 的 md5 差异集）。
DST   = os.path.join(BASE, 'out', 'corpus', 'steam_en.tsv')
HEAD  = 24


def is_hanzi(c):
    return '一' <= c <= '鿿'


def parse(raw, tail=6):
    off = HEAD
    idx = 0
    rows = []
    n = len(raw)
    while off + 2 <= n:
        L = int.from_bytes(raw[off:off + 2], 'big')
        if L <= 0:
            break
        remaining = n - off
        if 2 + L == remaining:              # 末条：无 tail，直接到文件尾
            body = raw[off + 2:off + 2 + L]
            rows.append((idx, off, L, b'', body))
            off += 2 + L
            idx += 1
            break
        if 2 + L + tail > n:
            break
        body = raw[off + 2:off + 2 + L]
        meta = raw[off + 2 + L:off + 2 + L + tail]
        rows.append((idx, off, L, meta, body))
        off += 2 + L + tail
        idx += 1
    return rows, off


def main():
    raw = open(SRC, 'rb').read()
    rows, end = parse(raw)
    resid = len(raw) - end

    # 闭环核验
    calc = HEAD + sum(2 + r[2] + (0 if i == len(rows) - 1 else 6)
                      for i, r in enumerate(rows))

    print('=== Steam en.lng 解析 ===')
    print('文件      : %s  size=%d' % (os.path.basename(SRC), len(raw)))
    print('记录数    : %d' % len(rows))
    print('闭环      : 消耗 %d / 文件 %d  残留 %d  -> %s'
          % (end, len(raw), resid, 'OK' if resid == 0 else 'FAIL'))

    ascii_cnt = 0
    hanzi_cnt = 0
    with open(DST, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'off', 'len', 'meta_hex', 'text', 'hanzi', 'is_ascii'])
        for idx, off, L, meta, body in rows:
            txt = body.decode('latin-1', 'replace')
            hz = sum(1 for c in txt if is_hanzi(c))
            asc = all(b < 0x80 for b in body)
            if asc:
                ascii_cnt += 1
            if hz:
                hanzi_cnt += 1
            w.writerow([idx, hex(off), L, meta.hex(), txt, hz, int(asc)])

    print('纯 ASCII 记录 : %d' % ascii_cnt)
    print('含汉字记录    : %d' % hanzi_cnt)
    print('输出-> %s' % DST)


if __name__ == '__main__':
    main()