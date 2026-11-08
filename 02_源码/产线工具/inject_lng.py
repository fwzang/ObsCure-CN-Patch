# -*- coding: utf-8 -*-
"""把final_zh.tsv 的中文译文注入 Steam 版 en.lng 结构，产出 injected_en.lng。

结构（已3078 条闭环验证）：
    24 字节文件头（照抄 Steam 原版）
    记录: [u16 BE L][body: L 字节][6 字节 meta]
    末条记录无 6 字节 tail  <- 关键，注入时必须复现

=== body 编码格式（实测三版交叉确认）===
  单字节 ASCII:00 <ASCII> 00            （首尾各一个 \x00）
  UTF-16LE 中文: 01 <UTF-16LE> 00 00    （首字节 \x01，末尾 \x00\x00）
  零售版中文记录实测 = 01 ... 00 00
  成品 en.lng_成品 实测 = 01 ... 00 00
  ⚠️ 缺标记会导致引擎按单字节解析 -> 乱码。这是 L2 抓到的真实缺陷。

meta 来源策略：
  有对齐映射的槽位-> 照抄零售版 meta（retail_idx = sidLCS 映射）
  Steam 独有/未映射-> 保留 Steam 原版 meta
  可用 --meta-mode=steam 强制全部保留 Steam 原 meta（L3 崩溃时的回退方案）

body来源策略：
  final_zh.tsv 有译文 -> UTF-16LE 编码
  哨兵 KEEP_RAW      -> 原样保留 Steam 原始 body 字节
  无译文             -> 原样保留 Steam 原始 body 字节

验证阶梯：
  L0 本脚本闭环验证（记录数 + 零残留 + meta 完整性）
  L1 独立复解析（--verify-only）
  L2 抽样 diff
"""
import argparse
import csv
import os
import struct
import sys

HEAD = 24
TAIL = 6
KEEP_RAW = '\x00KEEP_RAW\x00'


def parse_lng(raw, tail=TAIL, last_no_tail=True):
    """返回 [(idx, off, L, meta, body)]；失败抛异常。"""
    recs = []
    off = HEAD
    idx = 0
    n = len(raw)
    while off + 2 <= n:
        if off + 2 + tail > n:
            if last_no_tail and off + 2 <= n:
                break
            raise ValueError('记录 #%d @0x%x 头部越界' % (idx, off))
        L = int.from_bytes(raw[off:off + 2], 'big')
        if L <= 0:
            break
        if off + 2 + L == n and last_no_tail:
            body = raw[off + 2:off + 2 + L]
            recs.append((idx, off, L, b'', body))
            off += 2 + L
            idx += 1
            break
        if off + 2 + L + tail > n:
            raise ValueError('记录 #%d @0x%x 越界 L=%d 剩余=%d'
                             % (idx, off, L, n - off))
        body = raw[off + 2:off + 2 + L]
        meta = raw[off + 2 + L:off + 2 + L + tail]
        recs.append((idx, off, L, meta, body))
        off += 2 + L + tail
        idx += 1
    return recs, off


def load_tsv(path):
    with open(path, encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def inject(steam_en, final_zh, align, retail, out_path,
           meta_mode='retail', verbose=True):
    """核心注入逻辑，供 CLI 与 export_variants.py 复用。

    返回 True 表示成功。参数含义见 CLI 的同名选项。
    """
    raw = open(steam_en, 'rb').read()
    recs, end = parse_lng(raw)
    if verbose:
        print('=== 源文件 ===')
        print('Steam en.lng  size=%d  记录数=%d  闭环残留=%d'
              % (len(raw), len(recs), len(raw) - end))
    if len(recs) != 3078:
        print('❌ 记录数 %d != 3078' % len(recs))
        return False

    final = {int(r['idx']): r.get('zh', r.get('最终译文', ''))
             for r in load_tsv(final_zh)}
    amap = {int(r['steam_idx']): int(r['retail_idx'])
            for r in load_tsv(align)}
    rmeta = {}
    for r in load_tsv(retail):
        h = r['meta_hex']
        if h:
            rmeta[int(r['idx'])] = bytes.fromhex(h)

    out = bytearray(raw[:HEAD])
    st = {'zh': 0, 'keep': 0, 'empty': 0, 'm_retail': 0, 'm_steam': 0}

    for sidx, off, L, meta, body in recs:
        txt = final.get(sidx, '')
        if not txt.strip():
            new_body = body
            st['empty'] += 1
        elif txt == KEEP_RAW:
            new_body = body
            st['keep'] += 1
        else:
            # 必须是 01 <UTF-16LE> 00 00，与零售版/成品文件一致
            new_body = b'\x01' + txt.encode('utf-16-le') + b'\x00\x00'
            st['zh'] += 1

        ri = amap.get(sidx)
        if meta_mode == 'retail' and ri is not None and ri in rmeta:
            new_meta = rmeta[ri]
            st['m_retail'] += 1
        else:
            new_meta = meta
            st['m_steam'] += 1

        out += struct.pack('>H', len(new_body))
        out += new_body
        out += new_meta

    with open(out_path, 'wb') as f:
        f.write(bytes(out))

    if verbose:
        print()
        print('=== 注入 ===')
        print('中文注入   : %d 条' % st['zh'])
        print('原样保留   : %d 条（哨兵 KEEP_RAW %d + 无译文 %d）'
              % (st['keep'] + st['empty'], st['keep'], st['empty']))
        print('meta 来源  : 零售版 %d / Steam 原版 %d（mode=%s）'
              % (st['m_retail'], st['m_steam'], meta_mode))
        print('输出       : %s' % out_path)
        print('大小       : %d（原 %d，delta %+d）'
              % (len(out), len(raw), len(out) - len(raw)))
        print()
    return True


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument('--steam-en', default=os.path.join(
        here, 'out', 'hvptool_test', 'patch_probe3', '_texts', 'en.lng'))
    ap.add_argument('--final-zh', default=os.path.join(
        here, 'out', 'corpus', 'final_zh.tsv'))
    ap.add_argument('--align', default=os.path.join(
        here, 'out', 'corpus', 'alignment_map.tsv'))
    ap.add_argument('--retail', default=os.path.join(
        here, 'out', 'corpus', 'retail_zh.tsv'))
    ap.add_argument('--out', default=os.path.join(
        here, 'out', 'injected_en.lng'))
    ap.add_argument('--meta-mode', choices=('retail', 'steam'), default='retail',
                    help='meta 来源：retail=照抄零售版（默认），steam=全保留Steam 原版')
    ap.add_argument('--verify-only', action='store_true')
    args = ap.parse_args()

    if args.verify_only:
        return _verify(args.out)
    if not inject(args.steam_en, args.final_zh, args.align, args.retail,
                  args.out, args.meta_mode):
        return 1
    return _verify(args.out)


def _verify(path):
    """L0/L1：闭环 + meta 完整性 + 汉字统计 + L2 抽样 diff。"""
    raw = open(path, 'rb').read()
    print('=== L0/L1 闭环验证 ===')
    try:
        recs, end = parse_lng(raw)
    except Exception as e:
        print('❌ %s' % e)
        return 1
    resid = len(raw) - end
    calc = HEAD + sum(2 + r[2] + (0 if i == len(recs) - 1 else TAIL)
                      for i, r in enumerate(recs))
    print('记录数     : %d' % len(recs))
    print('闭环       : 消耗 %d / 文件 %d  残留 %d  -> %s'
          % (end, len(raw), resid, 'OK' if resid == 0 else 'FAIL'))
    print('算式核对   : %d vs %d  -> %s'
          % (calc, len(raw), 'OK' if calc == len(raw) else 'FAIL'))
    hz = sum(1 for _, _, _, _, b in recs
             for c in b.decode('utf-16-le', 'ignore')
             if '\u4e00' <= c <= '\u9fff')
    print('汉字总数   : %d' % hz)
    bad_meta = sum(1 for i, (_, _, _, m, _) in enumerate(recs)
                   if i < len(recs) - 1 and len(m) != TAIL)
    print('meta 长度异常: %d' % bad_meta)
    if resid != 0 or calc != len(raw) or len(recs) != 3078:
        print('❌ 闭环未通过')
        return 1
    print('✅ L0 通过')
    print()

    print('=== L2 抽样 diff ===')
    # 编码判据：body[0]==0x01 -> UTF-16LE；body[0]==0x00 且 body[1]==0x00 -> 单字节
    for i in (0, 4, 5, 9, 2000, 2001, 2003, 2006, 3069):
        if i >= len(recs):
            continue
        _, _, L, m, b = recs[i]
        if b[:1] == b'\x01':
            enc = 'UTF16'
            t = b[1:-2].decode('utf-16-le', 'replace') if L > 3 else ''
        elif b[:1] == b'\x00':
            enc = 'ASCII'
            t = b[1:-1].decode('latin-1', 'replace') if L > 2 else ''
        else:
            enc = 'RAW  '
            t = b.decode('latin-1', 'replace')
        print('  #%-5d len=%-4d meta=%s  %s | %s'
              % (i, L, m.hex(), enc, t[:44]))
    print()

    # 编码标记完整性检查：每条 body 必须符合两种合法格式之一
    bad_enc = []
    for i, (_, _, L, m, b) in enumerate(recs):
        if b[:1] == b'\x01':
            if not b.endswith(b'\x00\x00'):
                bad_enc.append((i, 'UTF16 缺尾部 0000'))
        elif b[:1] == b'\x00':
            if b[-1:] != b'\x00':
                bad_enc.append((i, 'ASCII 缺尾部 00'))
        else:
            bad_enc.append((i, '未知首字节 %02x' % b[0]))
    print('=== L2b 编码标记完整性 ===')
    print('异常 body: %d %s' % (len(bad_enc), bad_enc[:6]))
    if bad_enc:
        print('❌ 编码标记不完整')
        return 1
    print('✅ 全部 %d 条 body 标记合法' % len(recs))
    return 0


if __name__ == '__main__':
    sys.exit(main())