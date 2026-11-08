#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把零售版官方中文合并进 final_zh.tsv（官方优先，AI 补缺）。

关键修复（2026-10-11）：
  extract_retail_zh.py 用 body.decode('utf-16-le') 直接解整个 body，
  把 type 字节也当成宽字符 -> 产生 '倀牨獡' / 'ā' / 'Ã' 等伪影。
  正确布局（en_retail.raw 实测 3070 条 100% 闭环）：
      type=0 -> body[1:] 单字节 GBK/ASCII
      type=1 -> body[1] 是前缀字节，body[2:] 才是 UTF-16LE 文本
  本脚本直接重解析原始 raw，不再依赖 retail_zh.tsv 的 text 列。

合并规则（用户裁决口径）：
  1) 零售官方译文含汉字 -> 采用官方（质量最高、与官方贴图用词一致）
  2) 官方为空/无汉字     -> 保留现有 AI 译文
  3) 官方与 AI 都为空    -> 保持空（走既有 note 归类）
  4) idx 2000 哨兵       -> 原样保留 KEEP_RAW
  5) 控制标记：字面 '\\x01' 形式统一解码为真实控制字符（引擎按控制字节解析颜色/换段）

用法:
  python merge_retail_official.py            # 预览统计
  python merge_retail_official.py --apply    # 写 final_zh.tsv
"""
import csv
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORP = os.path.join(HERE, 'out', 'corpus')

RETAIL_RAW = os.path.join(HERE, 'out', 'cn_retail', 'lng', 'en_retail.raw')
ALIGN = os.path.join(CORP, 'alignment_map.tsv')
FINAL = os.path.join(CORP, 'final_zh.tsv')
RETAIL_FIXED = os.path.join(CORP, 'retail_zh_fixed.tsv')

KEEP_RAW = '\x00KEEP_RAW\x00'
HEAD = 24
TAIL = 6
CTRL_ESC = re.compile(r'\\x0[0-9A-Fa-f]')


def is_hanzi(c):
    return '\u4e00' <= c <= '\u9fff'


# ---------- 1. 重解析零售 raw（修正解码） ----------
def parse_retail(path):
    raw = open(path, 'rb').read()
    off = HEAD
    idx = 0
    rows = []
    while off + 2 <= len(raw):
        L = int.from_bytes(raw[off:off + 2], 'big')
        if L <= 0:
            break
        body = raw[off + 2:off + 2 + L]
        last = (2 + L == len(raw) - off)
        rows.append((idx, raw[off:off + 2], body, last))
        off += 2 + L + TAIL
        idx += 1
    return rows, off


def decode_body(body):
    """type 字节 + （type=1 时还有 1 个前缀字节）+ 文本。"""
    t = body[0]
    b = body[1:]
    if t == 0:
        return b.decode('gbk', 'replace').rstrip('\x00')
    return b[1:].decode('utf-16-le', 'replace').rstrip('\x00')


def retail_texts():
    rows, end = parse_retail(RETAIL_RAW)
    resid = os.path.getsize(RETAIL_RAW) - end
    out = {}
    meta = {}
    for idx, _lf, body, last in rows:
        out[idx] = decode_body(body)
    return out, meta, len(rows), resid


# ---------- 2. 控制标记字面量 -> 真实控制字符 ----------
def unescape(t):
    """'\\x01' -> '\x01'。只解 00-1F 范围的两位十六进制转义。"""
    def rep(m):
        return chr(int(m.group(0)[2:], 16))
    return CTRL_ESC.sub(rep, t or '')


# ---------- 3. 合并 ----------
def main():
    apply = '--apply' in sys.argv
    rt, _meta, nrec, resid = retail_texts()
    print('零售 raw：%d 条 / 闭环残留 %d' % (nrec, resid))

    al = {}
    with open(ALIGN, encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            al[int(r['steam_idx'])] = int(r['retail_idx'])

    with open(FINAL, encoding='utf-8') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    rows.sort(key=lambda r: int(r['idx']))
    print('final_zh：%d 行' % len(rows))

    n_official = n_keep_ai = n_both_empty = n_fixed = n_changed = 0
    out_rows = []
    new_rows = []
    for r in rows:
        idx = int(r['idx'])
        ai = unescape(r.get('zh') or '')
        ri = al.get(idx)
        off = unescape(rt.get(ri, '')) if ri is not None else ''

        if ai.strip() == KEEP_RAW.strip():
            out_rows.append({'idx': idx, 'zh': r['zh'], 'changed': r['changed'],
                             'src': 'sentinel'})
            n_both_empty += 1
            new_rows.append((idx, '', '', r['zh']))
            continue

        # 官方含汉字 -> 采用官方
        if any(is_hanzi(c) for c in off):
            src = 'retail'
            zh = off
            if ai != r['zh']:
                n_fixed += 1
        elif ai.strip():
            src = 'ai'
            zh = ai
            if ai != r['zh']:
                n_fixed += 1
        else:
            src = 'blank'
            zh = ''
            n_both_empty += 1

        if src == 'retail':
            n_official += 1
        elif src == 'ai':
            n_keep_ai += 1

        ch = '1' if (src == 'retail') else '0'
        if ch != r['changed']:
            n_changed += 1
        out_rows.append({'idx': idx, 'zh': zh, 'changed': ch, 'src': src})
        if zh != r['zh']:
            new_rows.append((idx, r['zh'], zh, src))

    print()
    print('采用官方零售译文 : %d 条' % n_official)
    print('保留 AI 译文     : %d 条' % n_keep_ai)
    print('两空/哨兵        : %d 条' % n_both_empty)
    print('实际内容变动     : %d 条' % len(new_rows))
    print('  其中转义修正    : %d 条' % sum(
        1 for i, a, b, s in new_rows if a.replace('\\x01', '\x01')
        .replace('\\x03', '\x03') == b))
    print('  changed 标记变 : %d 条' % n_changed)

    # 字集增量
    ai_chars = set()
    new_chars = set()
    for r in rows:
        for c in unescape(r.get('zh') or ''):
            if is_hanzi(c):
                ai_chars.add(c)
    for o in out_rows:
        for c in o['zh']:
            if is_hanzi(c):
                new_chars.add(c)
    added = new_chars - ai_chars
    print()
    print('汉字总数：原 %d -> 新 %d' % (len(ai_chars), len(new_chars)))
    print('需新增字形：%d 个 %s' % (len(added), ''.join(sorted(added)[:60])))

    if not apply:
        print()
        print('（预览模式，加 --apply 写入）')
        return 0

    bak = FINAL + '.ai_backup'
    if not os.path.exists(bak):
        shutil.copy2(FINAL, bak)
        print('备份 -> %s' % bak)

    with open(FINAL, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'zh', 'changed'])
        for o in out_rows:
            w.writerow([o['idx'], o['zh'], o['changed']])
    print('已写入 %s' % FINAL)

    with open(os.path.join(CORP, 'merge_official_diff.tsv'), 'w',
              encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'old_zh', 'new_zh', 'src'])
        for row in new_rows:
            w.writerow(row)
    print('差异表 -> out/corpus/merge_official_diff.tsv (%d 行)'
          % len(new_rows))
    return 0


if __name__ == '__main__':
    sys.exit(main())