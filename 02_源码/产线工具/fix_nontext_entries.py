#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""非文本条目修复：数字 / 符号 / 按键标签等不应被清空或汉化。

问题（2026-10-11 实测发现）：
  合并零售官方译文后，421-430（数字 0-9）、503-507（符号 - . + / *）、
  387（À 重音符号）这些「游戏代码/标签类」槽位最终 zh 为空。
  build_zh_text 会把空槽写成 `01 00 00`，Hook 注入后
  游戏里这些 HUD 标签会直接消失（数字读数、按键提示符号）。

判定口径（只认「不该动」，不猜「该译成什么」）：
  A类 数字 0-9 / 单符号  - . + / *      -> 保留原字符
  B类 按键标签 F1-F12 / NumLock / A-Z/ Up -> 保留原字符（零售官方也未翻译）
  C类 ON / NO / OK / À 等单 token       -> 保留原字符
  D类 英文侧本身为空185 条              -> 保持空（本来就空）
  E类 PS2/Xbox/存档 UI 提示（PC 不触发） -> 保持空
  idx 2000 控制序列                      -> 哨兵 KEEP_RAW（管线已处理）

用法:
  python fix_nontext_entries.py            # 预览
  python fix_nontext_entries.py --apply
"""
import csv
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORP = os.path.join(HERE, 'out', 'corpus')
FINAL = os.path.join(CORP, 'final_zh.tsv')
STEAM_EN = os.path.join(CORP, 'steam_en.tsv')

# 单字符数字 / 符号：任何语言都不翻译
RE_DIGIT = re.compile(r'^[0-9]$')
RE_SYMBOL = re.compile(r'^[-.+/*×÷]$')
# 按键标签：功能键 / 锁定键 / 单字母 / 方向
RE_KEYLABEL = re.compile(
    r'^(F[1-9]|F1[0-2]|NumLock|Num\s?Lock|'
    r'[A-Z]|Up|Down|Left|Right|ON|NO|OK)$', re.I)
# 重音 / 符号类单 token（Latin-1 补充字母、注册商标等）
RE_LATIN1 = re.compile(r'^[À-ÿ®™©]$')


def clean(t):
    """去掉 steam_en.tsv 里的 \\x00 包裹与控制标记，得到可读文本。"""
    t = (t or '').replace('\x00', '')
    return re.sub(r'[\x01-\x1f]', '', t).strip()


def classify(t):
    if not t:
        return 'en_empty'
    if RE_DIGIT.match(t):
        return 'digit'
    if RE_SYMBOL.match(t):
        return 'symbol'
    if RE_KEYLABEL.match(t):
        return 'keylabel'
    if RE_LATIN1.match(t):
        return 'latin1'
    return None


def main():
    apply = '--apply' in sys.argv
    en = {}
    with open(STEAM_EN, encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            en[int(r['idx'])] = r['text']

    with open(FINAL, encoding='utf-8') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    rows.sort(key=lambda r: int(r['idx']))

    fixes = []
    stats = {}
    for r in rows:
        idx = int(r['idx'])
        zh = (r.get('zh') or '').strip()
        if zh:
            continue                     # 有译文的不动
        t = clean(en.get(idx, ''))
        k = classify(t)
        if k is None:
            continue
        stats[k] = stats.get(k, 0) + 1
        if k == 'en_empty':
            continue                     # 本来就空，保持空
        fixes.append((idx, k, t))

    print('=== 非文本槽位定性 ===')
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print('  %-9s %d 条' % (k, v))
    print()
    print('需恢复原字符 : %d 条' % len(fixes))
    for idx, k, t in fixes:
        print('  idx %-5d %-9s %r' % (idx, k, t))

    if not apply:
        print()
        print('（预览模式，加 --apply 写入）')
        return 0

    bak = FINAL + '.pre_nontext'
    if not os.path.exists(bak):
        shutil.copy2(FINAL, bak)
        print('备份 -> %s' % bak)

    fmap = {i: t for i, _k, t in fixes}
    with open(FINAL, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['idx', 'zh', 'changed'])
        for r in rows:
            idx = int(r['idx'])
            if idx in fmap:
                w.writerow([idx, fmap[idx], '0'])
            else:
                w.writerow([idx, r['zh'], r['changed']])
    print('已写入 %s（%d 条恢复原字符，changed=0 不计汉化）'
          % (FINAL, len(fixes)))
    return 0


if __name__ == '__main__':
    sys.exit(main())