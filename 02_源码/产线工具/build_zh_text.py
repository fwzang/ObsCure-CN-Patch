# -*- coding: utf-8 -*-
"""独立中文文本生成器 —— 与 Hook 层完全解耦。

输入: final_zh.tsv（已含术语替换 + 7 条新 UI）
输出:
  zh_payload.bin  纯文本载荷，按 steam_idx 顺序拼接
                  每条 = 01 <UTF-16LE> 00 00
  zh_index.bin    索引表，每条 8B：steam_idx(4B LE) + offset(4B LE)
                  x86 Hook 可直接 memcpy，无需解析 TSV

设计约束（刻意不做的事）：
  - 不读引擎文件、不碰 meta、不处理 24 字节文件头
  - 不依赖任何 Hook 代码
  =>换 Hook 点 / 换引擎版本 / 补译 255 条空白，都只需重跑本脚本。

编码格式（2026-10-11 三版交叉实测）：
  单字节 ASCII: 00 <ASCII> 00
  UTF-16LE 中文: 01 <UTF-16LE> 00 00   ← 本脚本产出这一种

空白槽位策略：显式写 01 00 00（空终止），不留内存残留。
哨兵槽位（KEEP_RAW，idx 2000 的非文本调试序列）：写空终止，
  因为 Hook 消费的是中文文本而非原始字节序列，该槽位由 Hook 层保留原字节。
"""
import csv
import os
import struct
import sys

MARKER_UTF16 = b'\x01'
TERM_NULL = b'\x00\x00'
KEEP_RAW = '\x00KEEP_RAW\x00'

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main(final_tsv=None, out_payload=None, out_index=None, verbose=True):
    final_tsv = final_tsv or os.path.join(HERE, 'out', 'corpus', 'final_zh.tsv')
    out_payload = out_payload or os.path.join(HERE, 'out', 'zh_payload.bin')
    out_index = out_index or os.path.join(HERE, 'out', 'zh_index.bin')

    with open(final_tsv, encoding='utf-8') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))

    # 归一化列名：兼容 zh / 最终译文
    def get_text(r):
        t = r.get('zh')
        if t is None:
            t = r.get('最终译文')
        return (t or '')

    rows.sort(key=lambda r: int(r['idx']))

    payload = bytearray()
    index_entries = []
    n_blank = 0
    n_sentinel = 0
    hz = 0

    for r in rows:
        idx = int(r['idx'])
        text = get_text(r)

        if text == KEEP_RAW:
            # 哨兵：该槽位是控制字节序列，非文本 -> 空终止，由 Hook 保留原字节
            entry = MARKER_UTF16 + TERM_NULL
            n_sentinel += 1
        elif not text.strip():
            entry = MARKER_UTF16 + TERM_NULL
            n_blank += 1
        else:
            entry = MARKER_UTF16 + text.encode('utf-16-le') + TERM_NULL
            hz += sum(1 for c in text if '\u4e00' <= c <= '\u9fff')

        offset = len(payload)
        payload.extend(entry)
        index_entries.append((idx, offset))

    with open(out_payload, 'wb') as f:
        f.write(bytes(payload))
    with open(out_index, 'wb') as f:
        for idx, off in index_entries:
            f.write(struct.pack('<II', idx, off))

    if verbose:
        print('=== 独立中文文本生成器 ===')
        print('输入     : %s' % final_tsv)
        print('记录条数 : %d' % len(index_entries))
        print('载荷     : %s' % out_payload)
        print('           %d bytes' % len(payload))
        print('索引     : %s' % out_index)
        print('           %d bytes (%d 条 x 8B)'
              % (len(index_entries) * 8, len(index_entries)))
        print('空白槽位 : %d 条（已写 01 00 00 安全终止）' % n_blank)
        print('哨兵槽位 : %d 条（非文本控制序列，Hook 保留原字节）' % n_sentinel)
        print('汉字总数 : %d' % hz)

        # 自检
        errs = []
        if len(index_entries) != 3078:
            errs.append('记录数 %d != 3078' % len(index_entries))
        if index_entries[0][1] != 0:
            errs.append('首条 offset != 0')
        # 偏移单调 + 索引可反查
        off2idx = {o: i for i, o in index_entries}
        prev_end = 0
        for k, (idx, off) in enumerate(index_entries):
            if off != prev_end:
                errs.append('#%d offset 不连续 (%d != %d)' % (idx, off, prev_end))
                break
            nxt = index_entries[k + 1][1] if k + 1 < len(index_entries) else len(payload)
            if off2idx.get(off) != idx:
                errs.append('#%d offset 反查失败' % idx)
                break
            prev_end = nxt
        print()
        if errs:
            print('❌ 自检失败:')
            for e in errs:
                print('   %s' % e)
            return 1
        print('✅ 自检通过：3078 条、偏移连续、索引可反查、覆盖 %d/%d 字节'
              % (prev_end, len(payload)))

    return 0


if __name__ == '__main__':
    sys.exit(main())