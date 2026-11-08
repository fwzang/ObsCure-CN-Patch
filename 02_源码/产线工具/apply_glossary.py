# -*- coding: utf-8 -*-
"""合并 16 份增量翻译批次 + 应用术语表，产出 final_zh.tsv。

=== batch_TSV 的真实结构（实测推翻旧假设）===
16 个文件不是副本，是【增量翻译批次】：
  batch_001 覆盖 196条
  batch_002 覆盖 374 条（新增 178）
  ...
  batch_016 覆盖 2823 条（新增 77）
每份都是 0-3077 全覆盖，但只有部分记录填了译文。
=> 合并规则：跳过「跳过」/「跳过（PS2/Xbox 遗留）」/空译文，
   同一记录号取【最后一次出现的非空译文】。
=> 汇总：2823 条有译文 / 255 条空白。

===术语表 application ===
hard批：8 条 100% 安全（人名 + 队友→同伴 + 礼堂→大剧场）
soft 批：需人工抽检，输出 diff

输出：
  out/corpus/batch_all.tsv    合并后的 3078 条
  out/corpus/final_zh.tsv     应用术语后的最终译文
  out/corpus/glossary_diff.tsv 变更日志
"""
import csv
import glob
import os

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
CORP = os.path.join(BASE, 'out', 'corpus')
BD = os.path.join(ROOT, '2026-10-10_打包', 'Obscure_汉化工作区',
                  '翻译', 'batch_TSV')

SKIP_STATUS = {'跳过', '跳过（PS2/Xbox 遗留）'}
TOTAL = 3078
KEEP_RAW = '\x00KEEP_RAW\x00'   # 哨兵：注入时原样保留 Steam 原始 body 字节


def merge_batches():
    """按记录号取最后一次出现的非空译文。"""
    best = {}
    for p in sorted(glob.glob(os.path.join(BD, 'batch_*.tsv'))):
        fn = os.path.basename(p)
        with open(p, encoding='utf-8') as f:
            for r in csv.DictReader(f, delimiter='\t'):
                k = int(r['记录号'])
                zh = r['中文译文'].strip()
                st = r['状态'].strip()
                if not zh or st in SKIP_STATUS:
                    continue
                if k not in best or best[k]['中文译文'] != zh:
                    best[k] = {
                        'idx': k,
                        'en': r['英文原文'],
                        '中文译文': zh,
                        '状态': st,
                        '来源批次': fn,
                    }
    return best


def load_glossary():
    p = os.path.join(CORP, 'glossary.tsv')
    # encoding='utf-8-sig'：剥离 BOM，否则首列名会变成 '﻿官方译名'
    with open(p, encoding='utf-8-sig') as f:
        return [r for r in csv.DictReader(f, delimiter='\t')
                if r.get('AI 现译名') and r.get('官方译名')
                and r['AI 现译名'] != r['官方译名']]


def main():
    best = merge_batches()
    print('=== 合并 16 份增量批次 ===')
    print('有译文 : %d / %d' % (len(best), TOTAL))
    print('空白   : %d' % (TOTAL - len(best)))
    print()

    # 写batch_all.tsv
    dst = os.path.join(CORP, 'batch_all.tsv')
    with open(dst, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'en', 'zh', 'status', 'batch'])
        for i in range(TOTAL):
            b = best.get(i)
            if b:
                w.writerow([i, b['en'], b['中文译文'], b['状态'], b['来源批次']])
            else:
                w.writerow([i, '', '', '未翻译', ''])
    print('合并-> %s' % dst)
    print()

    # ---------- 应用术语表 ----------
    gloss = load_glossary()
    hard = [(g['AI 现译名'], g['官方译名']) for g in gloss
            if g['替换批次'] == 'hard']
    soft = [(g['AI 现译名'], g['官方译名']) for g in gloss
            if g['替换批次'] != 'hard']
    print('=== 术语表===')
    print('hard 批 %d 条: %s' % (len(hard), ', '.join('%s→%s' % t for t in hard)))
    print('soft 批 %d 条: %s' % (len(soft), ', '.join('%s→%s' % t for t in soft)))
    print()

    # 先统计影响面
    print('--- 影响面预估 ---')
    for old, new in hard + soft:
        c = sum(1 for b in best.values() if old in b['中文译文'])
        print('  %-6s -> %-6s  命中 %4d 条' % (old, new, c))
    print()

    diff_log = []
    final = {}
    for i, b in best.items():
        txt = b['中文译文']
        orig = txt
        hits = []
        for old, new in hard:
            n = txt.count(old)
            if n:
                txt = txt.replace(old, new)
                hits.append('%s→%s x%d' % (old, new, n))
        for old, new in soft:
            n = txt.count(old)
            if n:
                txt = txt.replace(old, new)
                hits.append('%s→%s x%d' % (old, new, n))
        final[i] = txt
        if txt != orig:
            diff_log.append({
                'idx': i,
                'en': b['en'],
                'original': orig,
                'replaced': txt,
                'rule': ' ; '.join(hits),
            })

    print('=== 替换结果 ===')
    print('变更条数: %d' % len(diff_log))
    print()

    # ---------- Steam 独有槽位（idx 2000-2007，零售版无对应）----------
    # sid LCS 定位出Steam 在 idx2000-2007 插入了 8 条：
    #   2000 len=20 body=\x01\x00\x00\x01A\x01\x00\x02A\x02... 带控制字节的调试序列
    #        【不是文本，不能当字符串处理】=> 注入时必须原样保留原始 body 字节。
    #   2001-2007 七条真实手柄/显示设置 UI，body 形如 \x00SCREEN RESOLUTION\x00
    #用户裁决：2000 保留英文；2001-2007 从 glossary 的「新增UI」批取译文。
    # 注意 final_zh.tsv 用 -1 作为「原样保留 body」的哨兵值。
    print('=== Steam 独有槽位（idx 2000-2007）===')
    new_ui = dict((g['AI 现译名'], g['官方译名']) for g in gloss
                  if g.get('类别') == '新增UI')
    steam_en = {}
    with open(os.path.join(CORP, 'steam_en.tsv'), encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            steam_en[int(r['idx'])] = r['text']
    for sidx in range(2000, 2008):
        raw = steam_en.get(sidx, '')
        en = ''.join(ch for ch in raw if ord(ch) >= 0x20).strip()
        if sidx == 2000:
            final[sidx] = KEEP_RAW          # 哨兵：原样保留 body
            best[sidx] = {'中文译文': KEEP_RAW, 'en': raw}
            print('  #%d  %-46s -> 原样保留原始字节（非文本调试序列）'
                  % (sidx, repr(raw)[:46]))
            continue
        tgt = new_ui.get(en)
        if tgt:
            final[sidx] = tgt
            best[sidx] = {'中文译文': en, 'en': en}
            diff_log.append({'idx': sidx, 'en': en, 'original': en,
                             'replaced': tgt, 'rule': '新增UI 注入'})
            print('  #%d  %-46s -> %s' % (sidx, repr(en)[:46], tgt))
        else:
            print('  #%d  %-46s -> !! 术语表缺译文，保留英文' % (sidx, repr(en)[:46]))
            final[sidx] = en
            best[sidx] = {'中文译文': en, 'en': en}
    print()

    # 写 final_zh.tsv
    dst = os.path.join(CORP, 'final_zh.tsv')
    with open(dst, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'zh', 'changed'])
        for i in range(TOTAL):
            w.writerow([i, final.get(i, ''),
                        int(i in final and final[i] != best[i]['中文译文'])])
    print('最终译文 -> %s' % dst)

    # 写 diff
    dst = os.path.join(CORP, 'glossary_diff.tsv')
    with open(dst, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'en', 'original', 'replaced', 'rule'])
        for d in sorted(diff_log, key=lambda x: x['idx']):
            w.writerow([d['idx'], d['en'][:70], d['original'], d['replaced'], d['rule']])
    print('变更日志 -> %s' % dst)
    print()

    # ---------- 人工抽检样本----------
    print('=== 抽检 20 条变更 ===')
    dl = sorted(diff_log, key=lambda x: x['idx'])
    pick = []
    if dl:
        pick += dl[:5] + dl[-5:]
        mid = dl[5:-5]
        if len(mid) >= 10:
            step = max(1, len(mid) // 10)
            pick += mid[::step][:10]
    for d in pick:
        print('  #%-5d %s' % (d['idx'], d['rule']))
        print('      原: %s' % d['original'][:64])
        print('      新: %s' % d['replaced'][:64])
    print()

    # ---------- 残留检查 ----------
    print('=== 残留检查（替换后不应再出现 AI 错译）===')
    for old, new in hard:
        c = sum(1 for t in final.values() if old in t)
        flag = 'OK' if c == 0 else '残留 %d' % c
        print('  %-6s : %s' % (old, flag))
    print()
    print('=== 官方词落地统计 ===')
    for old, new in hard:
        c = sum(1 for t in final.values() if new in t)
        print('  %-6s : %d 条' % (new, c))


if __name__ == '__main__':
    main()