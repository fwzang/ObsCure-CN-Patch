# -*- coding: utf-8 -*-
"""
中文 .lng 重建器（安全版）
==========================
以 3078 条记录为基准，逐条：
  - 原样保留 meta（6 字节）
  - 保留 EN 记录里的 PS2 手柄符号 / 内联样式码
  - 根据中文内容【自行选择】type 0 (8-bit GBK) 或 type 1 (UTF-16LE)
  - 不继承 EN 的 type

★ 本脚本【不修改 datapack.hvp】，只生成 out/lng/zh.lng 供人工检查。
  写包是独立步骤，需显式调用 --write 才执行。

用法:
  python build_zh_lng.py                 # 只生成 zh.lng，不碰包
  python build_zh_lng.py --verify        # 额外做往返校验
  python build_zh_lng.py --write         # ⚠ 写入 datapack.hvp（需事先恢复原版）
"""
from __future__ import print_function
import sys, os, io, struct, zlib, hashlib, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from FINAL_PARSE_FIXED import parse, HEADER_SIZE

LNG_DIR  = os.path.join(HERE, 'out', 'lng')
FILLED   = 'D:/todo_translate_filled.tsv'
BAK      = os.path.join(HERE, 'out', 'datapack.hvp.bak')
PACK     = os.path.abspath(os.path.join(HERE, '..', 'datapack.hvp'))
SP_OFF   = 0x2976BBA3        # sp.lng 在包内偏移
SP_SLOT  = 53757             # sp.lng 槽位大小
IT_FIELD = 0x117EC + 11      # it.lng 目录项中记录 sp.lng 压缩大小的字段

# 内联样式码：这些码在 UTF-16 条目里以 U+FE0x 出现，必须原样保留
STYLE_CODES = '︃︁︄'

# PS2 手柄符号 / 商标符号：原版 EN 里就是单字节，GBK 不含，需保留为单字节
PS2_MAP = {
    'Ã': 0xC3,   # 左摇杆
    'Ð': 0xD0,   # 右摇杆
    'Æ': 0xC6,   # 上
    'Ê': 0xCA,   # 下
    'Ç': 0xC7,   # 左
    'Â': 0xC2,   # 右
    '®': 0xAE,   # (R)
    'À': 0xC0,   # Select
    'Å': 0xC5,   # Start
    'Þ': 0xDE,   # L1
    'Î': 0xCE,   # R1
    'ð': 0xF0,   # L2
    'ý': 0xFD,   # R2
    'õ': 0xF5,   # R3
    'þ': 0xFE,   # L3
    'É': 0xC9,
    'á': 0xE1,   # ·
    'í': 0xED,   # ·
    'ü': 0xFC,   # ·
    'ó': 0xF3,
    'ñ': 0xF1,
}


def unesc(s):
    out = []; i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            n = s[i+1]
            if n == 'n': out.append('\n'); i += 2; continue
            if n == 'r': out.append('\r'); i += 2; continue
            if n == 't': out.append('\t'); i += 2; continue
            if n == '\\': out.append('\\'); i += 2; continue
        out.append(s[i]); i += 1
    return ''.join(out)


def text_of(rec):
    """从原版记录取出纯文本。"""
    b = rec['body']; t = rec['type']
    if t == 0:
        return b[1:-1].decode('latin-1', 'replace') if len(b) >= 2 else ''
    if t == 1:
        raw = b[2:-2] if len(b) >= 4 else b''
        if len(raw) % 2:
            raw = raw[:-1]
        return raw.decode('utf-16-le', 'replace')
    return b[1:].decode('latin-1', 'replace')


def load_filled():
    """载入用户填好的 913 条译文。返回 {idx: text}"""
    out = {}
    for line in io.open(FILLED, encoding='utf-8'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').rstrip('\r').split('\t')
        if len(f) != 5:
            continue
        out[int(f[0])] = unesc(f[4])
    return out


def load_old_zh():
    """载入旧译文 2165 条。返回 {idx: text}"""
    out = {}
    p = os.path.join(LNG_DIR, 'records_zh.tsv')
    for line in io.open(p, encoding='utf-8'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').rstrip('\r').split('\t')
        if len(f) < 6:
            continue
        if f[5] == 'OK':
            out[int(f[0])] = unesc(f[4])
    return out


def has_style(s):
    return any(c in s for c in STYLE_CODES)


def split_style(s):
    """把文本拆成 (普通段, [(样式码, 位置)]) 以便 type=1 重建。
    这里不做复杂处理：直接把整串当 UTF-16LE 写，样式码本身就是
    U+FE0x，在 UTF-16LE 里占 2 字节，与原版一致。"""
    return s


def encode_record(text, want_style):
    """把中文文本编码成 body 字节。

    返回 (body_bytes, type_value)

    规则:
      - 含内联样式码 -> 必须 type=1 (UTF-16LE)，样式码占 2 字节
      - 含 GBK 无法编码的字符 -> type=1
      - 否则 -> type=0 (GBK)，最省空间
    """
    if want_style or has_style(text):
        # type=1: [01][控制字节][UTF-16LE 文本][00 00]
        # 控制字节沿用原版习惯 0x1C；文本末尾补 00 00
        u = text.encode('utf-16-le')
        body = b'\x01\x1c' + u + b'\x00\x00'
        return body, 1

    # type=0: [00][GBK 文本][00]
    try:
        g = text.encode('gbk')
        return b'\x00' + g + b'\x00', 0
    except UnicodeEncodeError:
        pass

    # 退路1：把 PS2 符号换回单字节，其余用 GBK
    buf = []
    ok = True
    for ch in text:
        if ord(ch) in PS2_MAP:
            buf.append(PS2_MAP[ord(ch)])
        else:
            try:
                buf.extend(ch.encode('gbk'))
            except UnicodeEncodeError:
                ok = False
                break
    if ok:
        return b'\x00' + bytes(buf) + b'\x00', 0

    # 退路2：非 GBK 且无 PS2 映射 -> 用 UTF-16LE（type=1，无样式码）
    u = text.encode('utf-16-le')
    return b'\x01\x1c' + u + b'\x00\x00', 1


def build(zh_map, en_recs):
    """生成完整的 zh.lng 字节串。"""
    en_data = open(os.path.join(LNG_DIR, 'en.lng'), 'rb').read()
    out = bytearray(en_data[:HEADER_SIZE])
    stats = {'type0': 0, 'type1': 0, 'empty': 0, 'fallback': 0}

    for i, rec in enumerate(en_recs):
        meta = rec['meta']
        zh = zh_map.get(i, '')
        en_text = text_of(rec)

        if not zh.strip():
            # 无译文：原样保留英文记录（保持 type 与 meta）
            body = rec['body']
            out += struct.pack('>H', len(body)) + body + meta
            stats['type0' if rec['type'] == 0 else 'type1'] += 1
            stats['empty'] += 1
            continue

        body, tv = encode_record(zh, want_style=has_style(en_text) and has_style(zh))
        out += struct.pack('>H', len(body)) + body + meta
        stats['type%d' % tv] += 1

    return bytes(out), stats


def verify(blob, en_recs, zh_map):
    """往返校验：重新解析生成的 lng，再重建，应逐字节一致。"""
    recs = parse(blob)
    if len(recs) != len(en_recs):
        return False, '记录数 %d != %d' % (len(recs), len(en_recs))
    out = bytearray(blob[:HEADER_SIZE])
    for r in recs:
        out += struct.pack('>H', len(r['body'])) + r['body'] + r['meta']
    if bytes(out) != blob:
        return False, '重建不一致'
    # meta 必须与 EN 完全一致
    for a, b in zip(recs, en_recs):
        if a['meta'] != b['meta']:
            return False, 'meta 在 #%d 处不一致' % a['index']
    return True, '记录 %d 条，meta 全部一致，往返 PASS' % len(recs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--write', action='store_true',
                    help='写入 datapack.hvp（危险，需事先恢复原版）')
    args = ap.parse_args()

    en_data = open(os.path.join(LNG_DIR, 'en.lng'), 'rb').read()
    en_recs = parse(en_data)
    sp_recs = parse(open(os.path.join(LNG_DIR, 'sp.lng'), 'rb').read())
    print('基准: EN %d 条 / SP %d 条' % (len(en_recs), len(sp_recs)))

    new = load_filled()
    old = load_old_zh()
    print('新译文 %d 条，旧译文 %d 条' % (len(new), len(old)))

    zh_map = {}
    for i in range(len(en_recs)):
        z = new.get(i, '').strip()
        if not z:
            z = old.get(i, '').strip()
        zh_map[i] = z
    have = sum(1 for v in zh_map.values() if v)
    print('有中文: %d / %d' % (have, len(en_recs)))

    blob, stats = build(zh_map, en_recs)
    print()
    print('生成: %d 字节 (EN 原版 %d 字节)' % (len(blob), len(en_data)))
    print('  type=0 (8-bit GBK): %d' % stats['type0'])
    print('  type=1 (UTF-16LE): %d' % stats['type1'])
    print('  保留英文(无译文):   %d' % stats['empty'])

    p = os.path.join(LNG_DIR, 'zh.lng')
    open(p, 'wb').write(blob)
    print('  写出 %s' % p)
    print('  SHA256 %s' % hashlib.sha256(blob).hexdigest())

    if args.verify:
        print()
        ok, msg = verify(blob, en_recs, zh_map)
        print('往返校验: %s  %s' % ('PASS' if ok else 'FAIL', msg))
        if not ok:
            sys.exit(2)

    if args.write:
        print()
        print('!! 警告：即将写入 datapack.hvp')
        comp = zlib.compress(blob, 9)
        print('   压缩后 %d 字节，槽位 %d 字节' % (len(comp), SP_SLOT))
        if len(comp) > SP_SLOT:
            print('   ✗ 超槽位，无法写入。请先扩大槽位（A 方案）。')
            sys.exit(3)
        print('   → 槽位足够。执行写入。')
        with open(PACK, 'r+b') as g:
            g.seek(SP_OFF); g.write(comp); g.write(b'\x00' * (SP_SLOT - len(comp)))
            g.seek(IT_FIELD); g.write(len(comp).to_bytes(2, 'big'))
        print('   已写入 sp.lng + 同步 it.lng 目录项。')
        print('   请运行游戏验证；若异常立即执行 restore.bat all')
    else:
        print()
        print('（未改动 datapack.hvp。加 --verify 做往返校验，加 --write 才写包）')


if __name__ == '__main__':
    main()
