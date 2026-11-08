# -*- coding: utf-8 -*-
"""
datapack.hvp 目录表（TOC）解析 + hash 字段语义破解
====================================================
1. 从文件头开始自动校准目录项布局（meta 长度未知，暴力候选）。
2. 导出全部目录项：name / 下一条 offset / 下一条 size / 中间 8 字节字段。
3. 用已知资源（原始压缩流 + 解压后数据）暴力匹配那 8 字节，
   判断它究竟是"内容校验"还是"无关字段"。
"""
import os, sys, struct, zlib, hashlib, binascii, json

HERE = os.path.dirname(os.path.abspath(__file__))
BAK = os.path.join(HERE, 'out', 'datapack.hvp.bak')
TOC_START = 0x35          # '_common' 的 nameLen 字段位置（由头部 hexdump 校准）

f = open(BAK, 'rb')
f.seek(0, 2); FSIZE = f.tell()


def read(off, n):
    f.seek(off); return f.read(n)


def walk(meta_len, start=TOC_START, limit=4000):
    """按 [u32 BE nameLen][name][meta_len] 走一遍，返回条目列表或 None"""
    p = start
    entries = []
    for _ in range(limit):
        if p + 4 > FSIZE:
            return None
        nl = struct.unpack_from('>I', read(p, 4))[0]
        if not (1 <= nl <= 120):
            return None
        name = read(p + 4, nl)
        try:
            name.decode('ascii')
        except UnicodeDecodeError:
            return None
        meta = read(p + 4 + nl, meta_len)
        if len(meta) < meta_len:
            return None
        entries.append((p, nl, name.decode('ascii'), meta))
        p += 4 + nl + meta_len
        if p >= FSIZE:
            return None
    return entries, p


best = None
for ml in range(16, 40):
    r = walk(ml, limit=200)
    if r:
        ents, endp = r
        names = [e[2] for e in ents]
        ok = all(('/' in n or '\\' in n or '.' in n) for n in names[:50])
        print('meta_len=%2d -> 前200项可解析, 末位=0x%X, 例: %s' % (ml, endp, names[:6]))
        if ok:
            best = ml

if best is None:
    print('!! 没找到候选，人工检查')
    sys.exit(1)

print('\n采用 meta_len = %d\n' % best)

# 完整走一遍，直到名字变垃圾或回到数据区
entries, endp = walk(best, limit=20000)
print('目录项总数: %d   TOC 结束于 0x%X (头后 %d 字节)' % (len(entries), endp, endp - TOC_START))
print('前 12 项:')
for p, nl, name, meta in entries[:12]:
    print('  0x%X  %-28s %s' % (p, name, meta.hex()))
print('...')
print('后 8 项:')
for p, nl, name, meta in entries[-8:]:
    print('  0x%X  %-28s %s' % (p, name, meta.hex()))

# 找出语言相关项
print('\n语言 / _texts / voices 相关项:')
lang_hits = []
for i, (p, nl, name, meta) in enumerate(entries):
    low = name.lower()
    if low.endswith('.lng') or '_texts' in low or 'voices.txt' in low:
        nxt_size = struct.unpack_from('>H', meta, len(meta) - 11)[0] if False else None
        lang_hits.append((p, name, meta))
        print('  idx=%-5d 0x%X  %-24s meta=%s' % (i, p, name, meta.hex()))

# 字段解剖：假设 meta = [??? ][size u16 BE][hash 8][offset u32 BE]
print('\n按 "尾部 size(2)+hash(8)+offset(4)" 假设解读:')
recs = []
for p, nl, name, meta in entries:
    if len(meta) < 14:
        continue
    tail = meta[-14:]
    size = struct.unpack_from('>H', tail, 0)[0]
    hash8 = tail[2:10]
    off = struct.unpack_from('>I', tail, 10)[0]
    recs.append((p, name, meta[:-14], size, hash8, off))
    if name.lower().endswith('.lng') or 'voices.txt' in name.lower():
        print('  %-22s head=%-10s size=%-7d hash=%s off=0x%08X %s'
              % (name, recs[-1][3-1].hex() if False else meta[:-14].hex(), size, hash8.hex(), off,
                 'IN-RANGE' if off + size <= FSIZE else 'sentinel'))

json.dump([{'off': p, 'name': n, 'head': m.hex(), 'size': s, 'hash': h.hex(), 'next': o}
           for p, n, m, s, h, o in recs],
          open(os.path.join(HERE, 'out', 'hvp_toc.json'), 'w'), indent=1, ensure_ascii=False)
print('\n完整目录 -> out/hvp_toc.json')

# ── hash 语义：拿 de/en/fr/it/sp 各自的 (size, hash, off) 试各种校验 ──
print('\n=== 8 字节 hash 字段语义测试 ===')
cands = {}
for p, n, m, s, h, o in recs:
    if n.lower().endswith('.lng') and o + s <= FSIZE and s > 0:
        cands[n.lower()] = (o, s, h)

def trials(blob):
    d = {}
    d['crc32'] = struct.pack('>I', binascii.crc32(blob) & 0xffffffff)
    d['crc32le'] = struct.pack('<I', binascii.crc32(blob) & 0xffffffff)
    d['adler32'] = struct.pack('>I', zlib.adler32(blob))
    d['md5[:8]'] = hashlib.md5(blob).digest()[:8]
    d['md5[-8:]'] = hashlib.md5(blob).digest()[-8:]
    d['sha1[:8]'] = hashlib.sha1(blob).digest()[:8]
    d['sha256[:8]'] = hashlib.sha256(blob).digest()[:8]
    d['sum64'] = struct.pack('>Q', sum(blob) & ((1 << 64) - 1))
    d['len+sum'] = struct.pack('>II', len(blob), sum(blob) & 0xffffffff)
    d['md5hex[:8]'] = hashlib.md5(blob).hexdigest()[:8].encode()
    d['sha1hex[:8]'] = hashlib.sha1(blob).hexdigest()[:8].encode()
    return d

for name, (o, s, h) in sorted(cands.items()):
    comp = read(o, s)
    raw = None
    try:
        raw = zlib.decompress(comp)
    except Exception:
        pass
    print('\n%s @0x%X size=%d  hash=%s' % (name, o, s, h.hex()))
    print('   数据头: %s' % comp[:8].hex())
    hit = False
    for label, blob in (('COMPRESSED', comp), ('DECOMPRESSED', raw)):
        if blob is None:
            continue
        for k, v in trials(blob).items():
            if v == h:
                print('   ★★★ 命中: %s 的 %s' % (label, k)); hit = True
        # 也试前 8/后 8 出现在 hash 里
        for k, v in trials(blob).items():
            if v[:4] == h[:4] or v[:4] == h[4:]:
                print('   部分命中 %s(%s) %s vs %s' % (label, k, v.hex(), h.hex()))
    if not hit:
        print('   （常见校验和均未命中 —— 可能是自定义 hash 或无关字段）')
