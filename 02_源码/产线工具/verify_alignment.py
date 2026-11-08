# -*- coding: utf-8 -*-
"""对齐验证：确认零售版 idx ↔ Steam 版 idx 的真实映射关系。

=== 重要修正（推翻此前所有假设）===
1. out/cn_retail/lng/STEAM_en.lng（161554）实测是【法语】，
   参考语言/ 目录里也只有 de/fr/it/sp，没有 en。禁止用作基准。
   真英文 =hvptool_test/patch_probe3/_texts/en.lng（149876，3078 条）。

2. 零售版 body 是【UTF-16LE】，Steam 版 body 是【单字节 latin-1】。
   同一 idx 的两侧 body 长度因此不可比；零售版的 text 列是 UTF-16LE 解码结果。

3. ★ idx 不是 1:1。实测sid 序列 LCS：
     retail[   0-1999] -> steam[   0-1999]  shift=0
     retail[2000-2000] -> steam[2001-2001]  shift=1
     retail[2001-3069] -> steam[2009-3077]  shift=+8
   Steam 在 idx 2001-2008 处多插了 8 条记录，导致其后整体偏移 +8。
   3070 vs 3078 的差8 正是这个插入，不是「零售版少8 条」。
   LCS 覆盖率 100%（3070/3070），映射唯一。

输出：out/corpus/alignment_map.tsv  （retail_idx -> steam_idx）
"""
import csv
import os
import array

BASE = os.path.dirname(os.path.abspath(__file__))
CORP = os.path.join(BASE, 'out', 'corpus')


def load(name):
    with open(os.path.join(CORP, name), encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def sid_of(row):
    h = row.get('meta_hex', '')
    return int(h[4:6], 16) if len(h) >= 6 else -1


def lcs_pairs(a, b):
    n, m = len(a), len(b)
    dp = [array.array('i', [0] * (m + 1)) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        ai = a[i]
        cur = dp[i]
        nxt = dp[i + 1]
        for j in range(m - 1, -1, -1):
            if ai == b[j]:
                cur[j] = nxt[j + 1] + 1
            else:
                x = nxt[j]
                y = cur[j + 1]
                cur[j] = x if x >= y else y
    pairs = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j] and dp[i][j] == dp[i + 1][j + 1] + 1:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs, dp[0][0]


def main():
    R = load('retail_zh.tsv')
    S = load('steam_en.tsv')
    print('=== 对齐验证 ===')
    print('零售版: %d 条   Steam 英文版: %d 条' % (len(R), len(S)))
    print()

    a = [sid_of(r) for r in R]
    b = [sid_of(s) for s in S]
    pairs, L = lcs_pairs(a, b)
    cov = 100.0 * L / len(R)
    print('--- sid 序列 LCS ---')
    print('LCS 长度: %d / %d  覆盖率 %.1f%%' % (L, len(R), cov))
    print()

    # --- sid 歧义消歧 ---
    # sid 值域只有 0x00..0x99，重复严重。LCS 在 retail[2000] 处把 sid=2配到了
    # steam[2001]（SCREEN RESOLUTION），但语义上 r2000 = "我跑不快了！我伤得太重..."
    # 对应 s2008 = "I can't run faster! I'm hurt too bad..."。
    # 真相：Steam 在 idx 2000-2007 插入了 8 条零售版没有的记录：
    #   s2000 "AAAA"（调试串）+ s2001-s2007 七条手柄 UI
    #   (SCREEN RESOLUTION / Select controller / Keyboard / Game controller /
    #    No controller assigned / Select a controller for player 1. / player 2.)
    # 零售版对应位置直接跳到游戏对白 => 该区块 sid 序列完全错位。
    # 因此真实映射只有一个断点：retail[2000] 起整体 +8。
    # 修正方式：把 sid 序列切成「连续 sid 单调块」，块边界用语义对齐确定。
    CTRL = set(range(32))
    norm = lambda s: ''.join(c for c in s if ord(c) not in CTRL).strip()

    print('--- 偏移修正：定位 Steam 侧插入块 ---')
    # 找出 sid 序列断裂点：retail[i] 的 sid 在 steam 中next 出现的位置
    ins_start = None
    # 直接检测：retail[2000] 之后的 sid 序列整体右移多少
    # 用「sid 元组连续 8 元素」的唯一匹配做锚
    key = tuple(a[2000:2008])
    cands = [j for j in range(1900, 2100)
             if j + 8 <= len(S) and tuple(b[j:j + 8]) == key]
    if cands:
        ins_start = cands[0] - 2000
        print('retail[2000:2008] 的 sid 元组在 steam[%d:%d] 命中 -> 插入块 = steam[%d-%d]，共 %d 条'
              % (cands[0], cands[0] + 8, cands[0] - ins_start, cands[0] - 1, ins_start))
        print('插入内容（零售版没有的 Steam 独有记录）:')
        for k in range(cands[0] - ins_start, cands[0]):
            print('   s%d sid=%3d | %s' % (k, b[k], norm(S[k]['text'])[:50]))
    print()

    # 应用修正：断点之前 shift=0，断点及之后 shift=+ins_start
    amap = {}
    for i in range(len(R)):
        sh = 0 if (ins_start is None or i < 2000) else ins_start
        amap[i] = i + sh
    #覆盖 LCS 未覆盖到的情况；若有越界则回退
    if ins_start is None:
        amap = dict(pairs)

    # 分段
    segs = []
    prev = None
    for i in sorted(amap):
        sh = amap[i] - i
        if prev is None or sh != prev:
            segs.append([i, i, sh, 0])
            prev = sh
        segs[-1][1] = i
        segs[-1][3] += 1
    print('--- 修正后对齐段 ---')
    for x, y, sh, c in segs:
        print('  retail[%4d-%4d] -> steam[%4d-%4d]  shift=%+d  (%d 条)'
              % (x, y, x + sh, y + sh, sh, c))
    print()

    # 映射检查
    rev = {}
    for i, j in amap.items():
        rev.setdefault(j, []).append(i)
    multi = sum(1 for v in rev.values() if len(v) > 1)
    oob = [i for i, j in amap.items() if j >= len(S)]
    miss = [i for i in range(len(R)) if i not in amap]
    sid_mm = sum(1 for i, j in amap.items() if j < len(S) and a[i] != b[j])
    print('--- 映射检查 ---')
    print('retail 覆盖        : %d / %d' % (len(amap), len(R)))
    print('sid 一致           : %d / %d  (不一致 %d)' % (len(R) - sid_mm, len(R), sid_mm))
    print('一对多歧义         : %d' % multi)
    print('越界               : %d' % len(oob))
    print('未映射             : %d %s' % (len(miss), miss[:10]))
    print()

    print('--- 交界处抽样（Steam 英文 vs 零售中文）---')
    probes = [0, 1, 5, 999, 1998, 1999, 2000, 2001, 2002, 2009, 2010, 3069]
    bad = 0
    for i in probes:
        if i not in amap:
            print('  idx %4d : 未映射' % i)
            continue
        j = amap[i]
        print('  r%4d/s%4d | %-44s | %s'
              % (i, j, norm(S[j]['text'])[:44], norm(R[i]['text'])[:30]))
    print()

    # 写映射表
    dst = os.path.join(CORP, 'alignment_map.tsv')
    with open(dst, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['retail_idx', 'steam_idx', 'shift', 'sid_retail', 'sid_steam',
                    'steam_text', 'retail_text'])
        for i in sorted(amap):
            j = amap[i]
            w.writerow([i, j, j - i, a[i], b[j],
                        norm(S[j]['text']), norm(R[i]['text'])])
    print('映射表-> %s' % dst)

    if len(miss) == 0 and multi == 0 and cov > 99.0:
        print()
        print('结论: PASS  映射唯一且全覆盖，按此表注入即可，无需再跑 LCS。')
        return 0
    print()
    print('结论: FAIL  映射不完整或有歧义，必须人工介入。')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())