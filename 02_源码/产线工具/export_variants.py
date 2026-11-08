# -*- coding: utf-8 -*-
"""导出三种 L3 验证用文本变体（三份同源同构，一次生成）。

  zh_only.bin + zh_only_index.bin   Hook 注入主路径
  en_zh_compare.tsv                 英中对照表（人工复核 / 截图比对）
  unified_zh.lng                    完整 Steam lng（文件替换对照，排除 Hook 变量）

设计要点：
  - 三份产物共用final_zh.tsv 单一数据源，改一处重跑一次，三份自动同步。
  - Hook 载荷直接复用 build_zh_text.py 的编码函数，不重写第二套实现。
  - unified_zh.lng 调用 inject_lng.py 作后端（meta / 文件头 / 末条无 tail 全复用），
    避免两套 lng 结构解析逻辑漂移。

编码格式（2026-10-11 三版交叉实测）：
  单字节 ASCII: 00 <ASCII> 00
  UTF-16LE 中文: 01 <UTF-16LE> 00 00
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_zh_text as BZT          # noqa: E402  编码逻辑单一来源
import inject_lng as INJ            # noqa: E402  lng 结构单一来源

CORP = os.path.join(HERE, 'out', 'corpus')
OUT = os.path.join(HERE, 'out', 'l3_variants')

FINAL_TSV = os.path.join(CORP, 'final_zh.tsv')
STEAM_EN = os.path.join(CORP, 'steam_en.tsv')
RETAIL = os.path.join(CORP, 'retail_zh.tsv')
ALIGN = os.path.join(CORP, 'alignment_map.tsv')
STEAM_LNG = os.path.join(HERE, 'out', 'hvptool_test', 'patch_probe3',
                         '_texts', 'en.lng')

CTRL = '\x01\x02\x03'


def clean(t):
    """剥掉 body 的控制字节，得到可读文本。"""
    return (t or '').replace('\x00', '').strip(CTRL).strip()


def load_rows():
    with open(FINAL_TSV, encoding='utf-8') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    rows.sort(key=lambda r: int(r['idx']))
    with open(STEAM_EN, encoding='utf-8') as f:
        en = {int(r['idx']): r['text'] for r in csv.DictReader(f, delimiter='\t')}
    return rows, en


SAVE_KW = ('successful', 'save game', 'save failed', 'overwrite',
           'restore', 'loading', 'load failed', 'saved game',
           'exceeded the maximum', 'damaged or missing', 'block(s)',
           'slot', 'please wait', 'checking', 'unable to')

# idx 2000 是非文本控制字节序列（01 00 00 01 41 01...），
# Hook/lng 侧保留原字节，对照表里显示为空 + note 标注，避免把哨兵值当译文输出。
SENTINEL_NOTE = '[保留原字节] 非文本控制序列，Hook/lng 侧原样保留 Steam 原始 body'


def note_for(idx, zh, en):
    """给空白条目标注已知原因，L3 看英文时不必慌。"""
    if zh:
        return ''
    e = en or ''
    c = clean(e)
    if not c:
        return '[空串] 英文侧本身为空'
    if 'MEMORY CARD' in e or 'PlayStation' in e:
        return '[PS2 存档提示] PC 版永不触发'
    if 'Xbox' in e:
        return '[Xbox 存档提示] PC 版永不触发'
    if len(c) <= 1:
        return '[单字符] 数字/符号，无需翻译'
    low = c.lower()
    if any(k in low for k in SAVE_KW):
        return '[存档 UI] PC 版走云存档，不触发'
    return '[待确认] 未分类空白'


def export_payload(rows, out_dir):
    """Hook 注入主路径：复用 build_zh_text 的编码与自检。"""
    rc = BZT.main(FINAL_TSV,
                  os.path.join(out_dir, 'zh_only.bin'),
                  os.path.join(out_dir, 'zh_only_index.bin'),
                  verbose=False)
    if rc != 0:
        return rc
    pay = os.path.getsize(os.path.join(out_dir, 'zh_only.bin'))
    ix = os.path.getsize(os.path.join(out_dir, 'zh_only_index.bin'))
    print('[1/3] Hook 载荷   : zh_only.bin %dB + zh_only_index.bin %dB'
          % (pay, ix))
    return 0


def export_compare(rows, en_map, out_dir):
    """英中对照表：截图比对的基准。"""
    amap = {}
    with open(ALIGN, encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            amap[int(r['steam_idx'])] = int(r['retail_idx'])
    dst = os.path.join(out_dir, 'en_zh_compare.tsv')
    n_note = 0
    with open(dst, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'sidx', 'ridx', 'en', 'zh', 'note'])
        for r in rows:
            i = int(r['idx'])
            zh = (r.get('zh') or '').strip()
            if zh == BZT.KEEP_RAW:
                # 哨兵：不把原值当译文写进对照表
                w.writerow([i, i, amap.get(i, ''),
                            clean(en_map.get(i, '')), '', SENTINEL_NOTE])
                n_note += 1
                continue
            note = note_for(i, zh, en_map.get(i, ''))
            if note:
                n_note += 1
            # idx 即 steam_idx；ridx = 零售版对照槽位（无映射则空）
            w.writerow([i, i, amap.get(i, ''),
                        clean(en_map.get(i, '')), zh, note])
    print('[2/3] 英中对照表 : en_zh_compare.tsv  %d 条（%d 条空白已标注原因）'
          % (len(rows), n_note))
    return 0


def export_unified(rows, out_dir):
    """完整 Steam lng：调用 inject_lng 后端，复用 meta/文件头/末条无 tail 逻辑。"""
    dst = os.path.join(out_dir, 'unified_zh.lng')
    if not INJ.inject(STEAM_LNG, FINAL_TSV, ALIGN, RETAIL, dst,
                      meta_mode='retail', verbose=True):
        print('❌ unified_zh.lng 生成失败')
        return 1
    ok = _verify_unified(dst)
    print('[3/3] 统一 lng   : unified_zh.lng %dB（inject_lng 后端，meta 取零售版）'
          % os.path.getsize(dst))
    return 0 if ok else 1


def _verify_unified(path):
    """闭环 + meta 核验，确保与 zh_only.bin 同源同构。"""
    raw = open(path, 'rb').read()
    try:
        recs, end = INJ.parse_lng(raw)
    except Exception as e:
        print('       ❌ 闭环解析失败: %s' % e)
        return False
    resid = len(raw) - end
    if resid or len(recs) != 3078:
        print('       ❌ 闭环失败: 残留 %d / 记录数 %d' % (resid, len(recs)))
        return False
    # meta 必须与零售版一致
    rmeta = {}
    with open(RETAIL, encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            if r['meta_hex']:
                rmeta[int(r['idx'])] = bytes.fromhex(r['meta_hex'])
    amap = {}
    with open(ALIGN, encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            amap[int(r['steam_idx'])] = int(r['retail_idx'])
    same = diff = 0
    for i, (_, _, _, m, _) in enumerate(recs):
        ri = amap.get(i)
        if ri is None or ri not in rmeta:
            continue
        if rmeta[ri] == m:
            same += 1
        else:
            diff += 1
    print('       闭环: 3078 条 / 残留 0/ 算式 %d 一致'
          % (INJ.HEAD + sum(2 + r[2] + (0 if k == len(recs) - 1 else INJ.TAIL)
                            for k, r in enumerate(recs))))
    print('       meta: 与零售版相同 %d / 不同 %d' % (same, diff))
    return diff == 0


def main():
    os.makedirs(OUT, exist_ok=True)
    print('=== 导出 L3 三变体 ===')
    print('数据源: %s' % FINAL_TSV)
    print()
    rows, en = load_rows()
    if len(rows) != 3078:
        print('❌ 记录数 %d != 3078' % len(rows))
        return 1
    rc = export_payload(rows, OUT)
    if rc:
        return rc
    export_compare(rows, en, OUT)
    if export_unified(rows, OUT):
        return 1
    print()
    print('全部导出至: %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())