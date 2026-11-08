#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""零售中文版 lng 结构化提取器

在原脚本基础上修了 3 处会导致崩溃/恒假的笔误：
  1. if name == 'main'-> if __name__ == '__main__'
  2. 'u4e00' <= c <= 'u9fff'（字符串比较）-> 0x4E00 <= ord(c) <= 0x9FFF
     原写法恒为 True（单个字符不可能 >= 9 字符的字符串）
     => hanzi 列会全是文件长度，必须改
  3. 原始 PUNCT 集合里混入了字面量 'nrt' 与引号，会把普通 ASCII 也算成标点

另外新增：
  - CLOSURE 双口径校验（后缀 6 字节 vs 4 字节），把「到底是3070 还是 3069」钉死
  - 输出 retail_anchors.tsv（纯 ASCII 锚点，供跨版本对齐）
  - 长度分位数，识别异常记录

用法:
  python extract_retail_zh.py <en_retail.raw> <outdir>
"""
import sys, os, csv

HEAD = 24
PUNCT = set('，。：；！？、（）「」《》【】…—·／＼.,:;!?()[]%/') | {'-'}
COMMON = set('的了是一不在人有我他这个们中来大为上和国地以到说时要就出会可也你对生能而子'
             '那得于着下自之年过发后作里如进同合行面起也还因都小然家从道没与法')

CTRL_OK = chr(13) + chr(10) + chr(9) + chr(0)


def is_hanzi(c):
    return '\u4e00' <= c <= '\u9fff'


def parse(raw, tail=6, strict=True):
    """按 [u16 BE L][body][tail 字节] 解析。返回 (rows, off)。

    ★末条记录可能没有 tail（实测 en_retail.raw 最后一条 L=12 直接到文件尾）。
      故末条放宽：若 2+L 已正好等于剩余字节，则不再要求 tail。
    """
    off = HEAD
    idx = 0
    rows = []
    while off + 2 <= len(raw):
        L = int.from_bytes(raw[off:off + 2], 'big')
        if L <= 0:
            break
        remaining = len(raw) - off
        # 末条特例：无 tail 直接到文件尾
        if 2 + L == remaining:
            body = raw[off + 2:off + 2 + L]
            rows.append([idx, off, L, b'', body])
            off += 2 + L
            idx += 1
            break
        if off + 2 + L + tail > len(raw):
            if strict:
                raise SystemExit(
                    '越界 @0x%x 记录#%d L=%d 剩余=%d' % (off, idx, L, remaining))
            break
        body = raw[off + 2:off + 2 + L]
        meta = raw[off + 2 + L:off + 2 + L + tail]
        rows.append([idx, off, L, meta, body])
        off += 2 + L + tail
        idx += 1
    return rows, off


def main(infile, outdir):
    raw = open(infile, 'rb').read()
    os.makedirs(outdir, exist_ok=True)

    # ---- 双口径闭环校验：先不确定后缀是几字节，先都试一遍 ----
    best = None
    diag = []
    for tail in (6, 4, 8):
        try:
            rows, off = parse(raw, tail=tail, strict=True)
        except SystemExit as e:
            diag.append('tail=%d  失败: %s' % (tail, e))
            continue
        resid = len(raw) - off
        diag.append('tail=%d  记录数=%d  残留=%d  %s'
                    % (tail, len(rows), resid, 'OK' if resid == 0 else 'FAIL'))
        if resid == 0 and (best is None or len(rows) > best[1]):
            best = (tail, len(rows), rows, off)
    print('=== 闭环口径诊断 ===')
    for d in diag:
        print('  ' + d)
    if best is None:
        print('没有任何口径能闭环，终止')
        return 1
    TAIL, nrec, rows, off = best
    print('  -> 采用 tail=%d，记录数=%d\n' % (TAIL, nrec))

    out_rows = []
    anchors = []
    for idx, roff, L, meta, body in rows:
        txt = body.decode('utf-16-le', 'replace')
        hans = [c for c in txt if is_hanzi(c)]
        clean = txt.strip(CTRL_OK + ' ')
        out_rows.append([
            idx, hex(roff), L, meta.hex(), txt, len(hans),
            int('\n' in txt or '\r' in txt),
            int(any(c in PUNCT for c in txt)),
            int(clean == ''),
        ])
        # 锚点判据（基于【原始字节】，不看UTF-16LE 解码结果）：
        #   (1) body 原始字节全ASCII（排除汉字记录）
        #   (2) 剥掉 type 字节与控制字节后，可打印 ASCII 占比 >= 0.9
        #   (3) 有效长度 >= 4
        # 注意不能用 txt 判 ASCII —— 'Phrase test number 2' 被当 UTF-16LE 解会变成
        # 「倀牨獡⁥整瑳渠浵敢」；也不能要求「解码后无 CJK」，同一原因会被全杀。
        raw_ascii = all(b < 0x80 or b in (0x0d, 0x0a, 0x09, 0x00) for b in body)
        cand = ''.join(chr(b) for b in body[1:]
                       if 0x20 <= b < 0x7f).strip()
        if raw_ascii and len(cand) >= 4:
            anchors.append([idx, cand])

    with open(os.path.join(outdir, 'retail_zh.tsv'), 'w',
              encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'off', 'len', 'meta_hex', 'text',
                    'hanzi', 'has_nl', 'has_punct', 'is_blank'])
        w.writerows(out_rows)

    with open(os.path.join(outdir, 'retail_anchors.tsv'), 'w',
              encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'text'])
        w.writerows(anchors)

    lens = sorted(r[2] for r in rows)
    n = len(lens)
    # 闭环：末条无 tail
    closure = HEAD + sum(2 + L + TAIL for _, _, L, _, _ in rows[:n - 1]) \
        + 2 + rows[n - 1][2]
    print('记录数      : %d' % n)
    print('闭环        : 24 + Σ(2+L+%d) [末条无tail] = %d  vs  文件 %d  -> %s'
          % (TAIL, closure, len(raw), 'OK' if closure == len(raw) else 'FAIL'))
    print('汉字总数    : %d' % sum(r[5] for r in out_rows))
    print('含中文记录  : %d' % sum(1 for r in out_rows if r[5] > 0))
    print('纯空白记录  : %d' % sum(1 for r in out_rows if r[8]))
    print('len 分布    : min=%d max=%d med=%d P95=%d'
          % (lens[0], lens[-1], lens[n // 2], lens[int(n * 0.95)]))
    print('可用锚点    : %d' % len(anchors))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))