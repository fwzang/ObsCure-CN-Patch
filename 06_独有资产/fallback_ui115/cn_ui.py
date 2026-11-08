# -*- coding: utf-8 -*-
"""核心 UI 汉化 v2：12px 点阵（容量 168 字），覆盖尽可能多的界面。"""
import struct, zlib, os, io, shutil, collections, sys
from PIL import Image, ImageDraw, ImageFont

BASE = r'E:\SteamLibrary\steamapps\common\Obscure'
IMG = 0x400000
GH, GAP = 24, 1
SZ = int(os.environ.get("CSZ", "12"))

# ---- 界面译文（记录号 -> 中文）。字符集需 <= 168 ----
UI = {
    # 主菜单
    1901: '开始新游戏', 1902: '读取进度', 1903: '游戏设置',
    1956: '奖励内容', 2000: '成就一览', 1915: '退出游戏',
    # 设置页
    1904: '亮度', 1905: '音效', 1906: '单声道', 1910: '振动',
    1911: '字幕', 1912: '手柄', 1989: '控制器设置',
    1960: '应用并退出', 1999: '载入设置', 1996: '保存新设置？',
    # 难度页
    1951: '难度', 1952: '简单', 1953: '普通', 1954: '困难',
    # 退出确认
    1916: '确定要退出吗？', 1917: '是', 1918: '否', 1908: '是', 1909: '否',
    # 读档 / 存档
    1919: '读取', 1920: '记忆卡槽位一', 1921: '记忆卡槽位二',
    1961: '暂无存档', 69: '请选择记忆卡槽位',
    42: '读取中', 43: '读取成功', 44: '读取失败',
    46: '保存成功', 47: '保存失败', 48: '保存进度？', 80: '读取进度？',
    # 暂停 / 游戏内
    1913: '暂停', 1914: '继续游戏', 1939: '暂停', 1930: '回到集合点',
    1941: '取消／切换目标', 1947: '物品栏', 1948: '武器栏',
    144: '武器菜单', 151: '换弹', 405: '换弹', 407: '保存',
    393: '跟我来', 141: '跟我来！', 395: '设置', 555: '地下室',
    134: '暂停／取消', 1982: '按 X 返回主菜单',
    # 读档/记录界面
    1992: '行动记录／队友', 1993: '战斗记录', 1994: '其他',
    1995: '按任意键…', 1997: '默认控制', 1998: '保存设置？',
    2002: '选择手柄', 2004: '游戏手柄',
}


def parse_lng(data):
    recs = []; p = 24; n = len(data)
    while p < n:
        L = struct.unpack_from('>H', data, p)[0]
        body = data[p+2:p+2+L]; be = p+2+L
        meta = b'' if be == n else data[be:be+6]
        recs.append([len(recs), body, meta]); p = n if be == n else be+6
    return recs


def build_lng(recs):
    o = bytearray(recs and b'' or b'')
    return o


def main():
    cp = open(os.path.join(BASE, r'hzp_tools\out\cachpack.hvp.bak'), 'rb').read()
    raw = zlib.decompress(cp[0x15b0b43:0x15b0b43+32133])
    W, H = 512, 128
    atlas = Image.new('L', (W, H), 0)
    for y in range(H):
        for x in range(W):
            atlas.putpixel((x, H-1-y), raw[(y*W+x)*4+3])
    d0 = open(os.path.join(BASE, r'hzp_tools\out\Obscure.exe.bak'), 'rb').read()
    recs = [list(struct.unpack_from('<5H', d0, 0x293F80+10*k)) for k in range(150)]

    # 保留 ASCII + 西语最高频 12 个重音
    SRC = os.path.join(BASE, r'hzp_tools\out\hvptool_test\unpack_test\_texts')
    freq = collections.Counter()
    for f in ['sp.lng', 'en.lng']:
        data = open(os.path.join(SRC, f), 'rb').read(); q = 24; n = len(data)
        while q < n:
            L = struct.unpack_from('>H', data, q)[0]; body = data[q+2:q+2+L]; be = q+2+L
            np = n if be == n else be+6
            if body[0] == 0:
                for b in body[1:-1]:
                    if b > 127: freq[b] += 1
            else:
                for c in body[2:-2].decode('utf-16le', 'ignore'):
                    if ord(c) > 127: freq[ord(c)] += 1
            q = np
    nonascii = sorted([r for r in recs if r[0] >= 128], key=lambda r: -freq.get(r[0], 0))
    keep_NA = {r[0] for r in nonascii[:12]}
    keep = [r for r in recs if 32 <= r[0] < 127] + [r for r in nonascii if r[0] in keep_NA]
    print(f'保留 {len(keep)} 条 (ASCII {len(keep)-len(keep_NA)} + 重音 {len(keep_NA)})')

    # 需要的汉字
    need = []
    for v in UI.values():
        for c in v:
            if ord(c) > 127 and c not in need:
                need.append(c)
    print(f'UI 译文需要汉字 {len(need)} 个: {"".join(need)}')
    if len(need) > 200:
        print('!! 超出容量, 请精简'); return

    # 放置：24px 行带，字形画在行带下半部分（12px），整带清空 -> 无残影
    occ = [[False]*W for _ in range(H)]
    for ch, x, y, w, adv in keep:
        for yy in range(y, min(y+GH, H)):
            for xx in range(x, min(x+adv+2, W)):
                occ[yy][xx] = True
    placed = []
    SZW = SZ
    for band in range(0, H - GH + 1, GH):
        gy = band + (GH - SZW)          # 字形放在行带下半
        x = 0
        while x < W and len(placed) < len(need):
            if any(occ[y][x] for y in range(band, band+GH)):
                x += 1; continue
            x2 = x
            while x2 < W and not any(occ[y][x2] for y in range(band, band+GH)):
                x2 += 1
            for k in range((x2-x)//(SZW+GAP)):
                if len(placed) >= len(need): break
                gx = x + k*(SZW+GAP)
                placed.append((need[len(placed)], gx, gy, SZW, SZW+1))
                for yy in range(band, band+GH):
                    for xx in range(gx, min(gx+SZW+GAP, W)):
                        occ[yy][xx] = True
            x = x2
    print(f'放置 {len(placed)} 个汉字 (12px, 24px 行带, 无残影)')
    miss = [c for c in need if c not in [q[0] for q in placed]]
    if miss: print('!! 未放下:', miss)

    font = ImageFont.truetype(r'C:\Windows\Fonts\simsun.ttc', SZ)
    for ch, gx, y0, w, adv in placed:
        tile = Image.new('L', (SZ, SZ), 0)
        td = ImageDraw.Draw(tile)
        bb = td.textbbox((0, 0), ch, font=font)
        td.text(((SZ-(bb[2]-bb[0]))//2-bb[0], (SZ-(bb[3]-bb[1]))//2-bb[1]), ch, fill=255, font=font)
        for yy in range(y0-12, min(y0-12+GH, H)):
            for xx in range(gx, min(gx+w, W)):
                atlas.putpixel((xx, yy), 0)
        atlas.paste(tile, (gx, y0))

    p = atlas.load(); buf = bytearray(W*H*4)
    for fy in range(H):
        vy = H-1-fy
        for x in range(W):
            a = p[x, vy]; o = (fy*W+x)*4
            buf[o]=a; buf[o+1]=a; buf[o+2]=a; buf[o+3]=a
    tga = struct.pack('<BBBHHBHHHHBB',0,0,2,0,0,0,0,0,W,H,32,0x08)+bytes(buf)+b'\x00'*26
    comp = zlib.compress(tga, 9)
    print(f'图集 zlib {len(comp)} / 32133')
    assert len(comp) <= 32133
    out = bytearray(cp); out[0x15b0b43:0x15b0b43+32133] = comp + b'\x00'*(32133-len(comp))
    open(os.path.join(BASE, 'cachpack.hvp'), 'wb').write(bytes(out))

    # exe
    tbl = bytearray()
    for r in keep: tbl += struct.pack('<5H', *r)
    for ch, x, y, w, adv in placed: tbl += struct.pack('<5H', ord(ch), x, y, w, adv)
    cnt = len(tbl)//10
    ex = bytearray(d0)
    e = struct.unpack_from('<I', ex, 0x3C)[0]
    nsec = struct.unpack_from('<H', ex, e+6)[0]
    optsz = struct.unpack_from('<H', ex, e+20)[0]; opt = e+24
    sa = struct.unpack_from('<I', ex, opt+32)[0]; fa = struct.unpack_from('<I', ex, opt+36)[0]
    sec0 = opt+optsz
    last = max(struct.unpack_from('<II', ex, sec0+i*40+8)[::-1] for i in range(nsec))[0] if False else 0
    last = 0
    for i in range(nsec):
        vs, va, rs, rw = struct.unpack_from('<IIII', ex, sec0+i*40+8)
        last = max(last, va+max(vs, rs))
    newva = (last+sa-1)//sa*sa
    nraw = (len(ex)+fa-1)//fa*fa
    ex += b'\x00'*(nraw-len(ex)); ex += bytes(tbl); ex += b'\x00'*((-len(ex))%fa)
    h = bytearray(40); h[0:6] = b'.wfont'
    struct.pack_into('<I',h,8,len(tbl)); struct.pack_into('<I',h,12,newva)
    struct.pack_into('<I',h,16,len(tbl)); struct.pack_into('<I',h,20,nraw)
    struct.pack_into('<I',h,36,0xC0000040)
    ex[sec0+nsec*40:sec0+nsec*40+40] = h
    struct.pack_into('<H',ex,e+6,nsec+1)
    struct.pack_into('<I',ex,opt+56,(newva+len(tbl)+sa-1)//sa*sa)
    struct.pack_into('<I',ex,0x5AB89,IMG+newva)
    struct.pack_into('<I',ex,0x5AB90,cnt)
    open(os.path.join(BASE,'Obscure.exe'),'wb').write(bytes(ex))
    print(f'exe: 表@{IMG+newva:#x} {cnt} 条')

    # 文本
    PATCH = os.path.join(BASE, r'hzp_tools\out\hvptool_test\patch_ui2\_texts')
    if os.path.exists(PATCH): shutil.rmtree(PATCH)
    os.makedirs(PATCH)
    for f in ['en.lng','sp.lng','de.lng','fr.lng','it.lng']:
        data = open(os.path.join(SRC, f), 'rb').read()
        rr = parse_lng(data)
        for r in rr:
            if r[0] in UI:
                r[1] = b'\x01\x00' + UI[r[0]].encode('utf-16-le') + b'\x00\x00'
        o = bytearray(data[:24])
        for i, body, meta in rr:
            o += struct.pack('>H', len(body)) + body + meta
        open(os.path.join(PATCH, f), 'wb').write(bytes(o))
    print(f'已汉化 {len(UI)} 条界面文本 -> {PATCH}')
    open(os.path.join(BASE, r'hzp_tools\out\ui_chars.txt'),'w',encoding='utf-8').write(''.join(need))


if __name__ == '__main__':
    main()
