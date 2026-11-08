# -*- coding: utf-8 -*-
"""批16 收官版图集（全量 3078 条）：512 x 4096（POT；阶段1 纪律：18px 固定）
4096 为当前正式工作高度（覆盖 big_atlas_c.raw/table_c.bin）

与 make_atlas_c15.py（批15 版）的区别：
  1. 字符来源 TSV：batch_015.tsv -> batch_016.tsv（全量 2823 条可写：全量 3078 条中可写部分；4096 基线）
  2. 新增 charset.txt 输出（全量去重清单，首次全量 / 后续追加）
其余机制完全不变（宽 512、原区保护、18px、pitch 21、keep 表头）。

产出：
  out/big_atlas_c.raw   512*2048*4 字节 A8R8G8B8（4MB）
  out/table_c.bin       新字形表（101 原表 + 新排字符）
  out/charset.txt       字符集清单（全量去重）

★ 2026-10-25 颜色定稿反转：米白 #E8E0D0 实测发灰，用户裁定改用纯白 #FFFFFF。
  预乘口径不变（RGB = 白×alpha/255 + int() 截断）→ 纯白下即 RGB = alpha，
  边缘像素仍随 alpha 变暗，不会出现"直通色"的光晕感（引擎按 RGB 亮度渲染）。
  hzp_tools 已删除，输入路径改指交付包内真源（可用环境变量覆盖）。

★ 2026-10-25 复现性告警（本次交叉验证实查）：
  装机在跑版图集实为 24px 时代构建（placed 记录 w=26/adv=27 共 1472 条，
  y 150..2118），与本脚本当前输入（18px 纪律 + batch_016.tsv 快照，1532 条，
  w=20）字符集不同：163 字仅旧版有、223 字仅重生成有。直接重跑会得到一份
  不同的图集——若要整体替换装机版，必须先核对字符集差异并重走装机验收。
  只改颜色请走就地路径（仅动 RGB、alpha 与字形表不动），那是单变量安全变更。
"""
import struct, zlib, os, csv, importlib.util
from PIL import Image, ImageDraw, ImageFont

PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACH = os.environ.get(
    'BIGCACH', os.path.join(PKG, '04_基线与回滚', 'cachpack.hvp.orig'))
TBLASCII = os.environ.get(
    'BIGTBL', os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'table_ascii.bin'))
TSV = os.environ.get(
    'BIGTSV', os.path.join(PKG, '05_hook源码与译文', 'translation_source',
                           'batch_016.tsv'))
OUT = os.environ.get(
    'BIGOUT', os.path.join(PKG, '05_hook源码与译文', 'payload_loose'))

AW, AH = 512, 4096        # ★ 正式工作高度 4096（POT）
GH     = 24               # grp1 的采样行带高
ORIG_H = 128              # 原区保护高度
SZ     = int(os.environ.get('BIGSZ', '18'))

# ★ 2026-10-25：用户定稿反转——米白 #E8E0D0 实测发灰，改用纯白 #FFFFFF。
#   端黑 Bold 不变；无光晕/描边/横增重。
FONT_PATH = os.environ.get(
    'BIGFONT', r'D:\Basic\ChillDuanSans_Bold.otf')
WHITE_R, WHITE_G, WHITE_B = 255, 255, 255      # #FFFFFF

# ★ 2026-10-11：不再排除任何idx —— 零售官方译文用到「余」「浏」等字
#   （原在 PS2 记忆卡提示 58-62/88-89 内）。PC 版虽不触发，字形表保持完整。
SKIP = set()

OFF, CSZ = 0x15B0B43, 32133


def load_orig_atlas():
    """★ 2026-10-11 修正：原图集是【双通道】设计，必须完整保留 RGBA。

    旧实现只取 alpha（body[...+3]）写回四通道=alpha，导致：
      - 原区 RGB 可见图案被抹平（17 个功能图标变白圆盘）
      - 西文字形丢灰度柔边
    实测（对照装机版原区）：
      通道 R/G/B 非零 18147/18106/17954（可见内容）
      通道 A       非零 24585（形状标记，不参与外观）
    修正后原区四通道与 cachpack 原图集逐字节一致（差异 0）。
    """
    raw = zlib.decompress(open(CACH, 'rb').read()[OFF:OFF + CSZ])
    idlen = raw[0]
    w, h = struct.unpack_from('<HH', raw, 12)
    px = 18 + idlen
    buf = bytearray(w * h * 4)
    for r in range(h):
        frow = h - 1 - r# TGA 原点在左下，需翻转成 top-down
        src = px + frow * w * 4
        dst = r * w * 4
        buf[dst:dst + w * 4] = raw[src:src + w * 4]
    img = Image.frombytes('RGBA', (w, h), bytes(buf))
    return img, (w, h)


def load_table():
    d = open(TBLASCII, 'rb').read()
    return [list(struct.unpack_from('<5H', d, 10 * k)) for k in range(len(d) // 10)]


def collect_need():
    """从 batch_002.tsv 收集全量非 ASCII 字符（保序去重）。"""
    need = []
    n_src = 0
    for r in csv.reader(open(TSV, encoding='utf-8'), delimiter='\t'):
        if not r or not r[0].isdigit():
            continue
        idx = int(r[0])
        if r[3] not in ('已翻译', '已有译文') or idx in SKIP or not r[2].strip():
            continue
        n_src += 1
        for c in r[2]:
            if ord(c) >= 128 and c not in need:
                need.append(c)
    return need, n_src


def main():
    atlas0, origsz = load_orig_atlas()
    print('原图集 %s' % (atlas0.size,))
    recs = load_table()
    print('原表 %d 条' % len(recs))

    keep = [r for r in recs if r[0] < 0x2E80]
    keep_codes = set(r[0] for r in keep)
    print('保留原表 %d 条（含 %d 个非ASCII符号）' % (
        len(keep), sum(1 for r in keep if r[0] >= 0x80)))

    need_all, n_src = collect_need()
    need = [c for c in need_all if ord(c) not in keep_codes]
    print('来源 TSV 可写条目 %d 条；非ASCII字符 %d 个；扣除原表已覆盖 %d 个 -> 需新排 %d 个'
          % (n_src, len(need_all), len(need_all) - len(need), len(need)))

    # 大画布：原图集完整 RGBA 贴左上角（原区双通道必须保留）
    atlas = Image.new('RGBA', (AW, AH), (0, 0, 0, 0))
    atlas.paste(atlas0, (0, 0))

    occ = [[False] * AW for _ in range(AH)]
    for ch, x, y, w, adv in keep:
        for yy in range(y, min(y + GH, AH)):
            for xx in range(x, min(x + adv + 2, AW)):
                occ[yy][xx] = True
    for yy in range(0, ORIG_H):
        for xx in range(0, AW):
            occ[yy][xx] = True

    TILE = SZ + 2
    PITCH = SZ + 3
    placed = []
    bands = list(range(ORIG_H, AH - GH + 1, GH))
    # ★ 24px 时 TILE=26 > GH=24，首行带 gy=band+(24-26)=126 会压进原区(y<128)。
    #   引擎按字形表的 y 取行带起点，必须保证新字全部落在 y>=ORIG_H。
    #   故把第一个可用行带整体下移一行（y 步进仍为 PITCH，避免行间裁切）。
    if bands and bands[0] + (GH - TILE) < ORIG_H:
        bands = [ORIG_H + (GH - TILE)] + bands[1:]
    cap = len(bands) * (AW // PITCH)
    print('行带 %d 个，每带 %d 位，容量 %d >= 需求 %d %s'
          % (len(bands), AW // PITCH, cap, len(need), 'OK' if cap >= len(need) else '!! 不足'))
    for band in bands:
        if len(placed) >= len(need):
            break
        gy = band + (GH - TILE)
        x = 0
        while x < AW and len(placed) < len(need):
            if any(occ[y][x] for y in range(band, band + GH)):
                x += 1
                continue
            x2 = x
            while x2 < AW and not any(occ[y][x2] for y in range(band, band + GH)):
                x2 += 1
            for k in range((x2 - x) // PITCH):
                if len(placed) >= len(need):
                    break
                gx = x + k * PITCH
                placed.append((need[len(placed)], gx, gy, TILE, PITCH))
                for yy in range(band, band + GH):
                    for xx in range(gx, min(gx + PITCH, AW)):
                        occ[yy][xx] = True
            x = x2
    print('已排布 %d 个字符 (字号 %dpx)' % (len(placed), SZ))
    if len(placed) < len(need):
        miss = [c for c in need if c not in [p[0] for p in placed]]
        print('!! 未放下 %d 个: %s' % (len(miss), ''.join(miss[:50])))
        raise SystemExit(1)

    # ---- 渲染新字：端黑 Bold + 纯白 #FFFFFF，RGB 与 alpha 分离 ----
    # 双通道口径（与原图集一致）：RGB = 可见灰度，alpha = 形状标记。
    # 2026-10-25 定稿（用户裁定米白发灰）：RGB = 255×alpha/255 = alpha，
    #   alpha 峰值 255；预乘 + int() 截断口径不变，边缘不过亮。
    font = ImageFont.truetype(FONT_PATH, SZ)
    for ch, gx, y0, w, adv in placed:
        tile = Image.new('L', (TILE, TILE), 0)
        td = ImageDraw.Draw(tile)
        bb = td.textbbox((0, 0), ch, font=font)
        td.text(((TILE - (bb[2] - bb[0])) // 2 - bb[0],
                 (TILE - (bb[3] - bb[1])) // 2 - bb[1]), ch, fill=255, font=font)
        tp = tile.load()
        ap = atlas.load()
        for yy in range(y0, min(y0 + TILE, AH)):
            for xx in range(gx, min(gx + w, AW)):
                g = tp[xx - gx, yy - y0]
                if g:
                    # ★ 2026-10-25 定稿：纯白预乘（用户裁定米白 #E8E0D0 发灰）。
                    #   RGB = 255×alpha/255 = alpha；取整口径必须 int() 截断
                    #   （与旧脚本一致），保证边缘像素随 alpha 变暗不过亮。
                    ap[xx, yy] = (int(WHITE_R * g / 255.0),
                                  int(WHITE_G * g / 255.0),
                                  int(WHITE_B * g / 255.0), g)
                else:
                    ap[xx, yy] = (0, 0, 0, 0)

    open(os.path.join(OUT, 'big_atlas_c.raw'), 'wb').write(atlas.tobytes())
    print('写出 big_atlas_c.raw (%dx%d, %d 字节)'
          % (AW, AH, os.path.getsize(os.path.join(OUT, 'big_atlas_c.raw'))))

    tbl = bytearray()
    for r in keep:
        tbl += struct.pack('<5H', *r)
    for ch, x, y, w, adv in placed:
        tbl += struct.pack('<5H', ord(ch), x, y, w, adv)
    open(os.path.join(OUT, 'table_c.bin'), 'wb').write(bytes(tbl))
    print('写出 table_c.bin  %d 条 (%d 字节)' % (len(tbl) // 10, len(tbl)))

    # ---- charset.txt（全量去重清单：首次全量 / 后续追加）----
    cs_path = os.path.join(OUT, 'charset.txt')
    old = set()
    if os.path.exists(cs_path):
        for line in open(cs_path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#'):
                old.add(line)
    if not old:
        with open(cs_path, 'w', encoding='utf-8') as f:
            f.write('# Obscure 汉化 · 字符集清单（全量去重）\n')
            f.write('# 口径：写入 en.lng 译文的全部非 ASCII 字符中、需新排入图集的部分\n')
            f.write('# 不含原表已有符号（ASCII 及 Ã Ð × … 等 13 个）；每行一个字符，按加入顺序\n')
            for c in need:
                f.write(c + '\n')
        print('写出 charset.txt（首次全量 %d 字）' % len(need))
    else:
        new_cs = [c for c in need if c not in old]
        with open(cs_path, 'a', encoding='utf-8') as f:
            for c in new_cs:
                f.write(c + '\n')
        print('charset.txt 追加 %d 字（累计 %d 字）' % (len(new_cs), len(old) + len(new_cs)))

    # ---- 自检 ----
    print()
    print('--- 自检 ---')
    # ASCII 覆盖检查：译文用到的所有 ASCII 必须已在原表（否则无法渲染）
    ascii_used = set()
    for r in csv.reader(open(TSV, encoding='utf-8'), delimiter='\t'):
        if not r or not r[0].isdigit():
            continue
        idx = int(r[0])
        if r[3] not in ('已翻译', '已有译文') or idx in SKIP or not r[2].strip():
            continue
        ascii_used |= {c for c in r[2] if ord(c) < 128}
    ascii_missing = [c for c in sorted(ascii_used) if ord(c) not in keep_codes]
    print('  %s 译文 ASCII 用字 %d 个，原表缺失 %d 个 %s'
          % ('OK' if not ascii_missing else '!!', len(ascii_used), len(ascii_missing),
             ascii_missing[:20] if ascii_missing else ''))
    # 原区自检：必须与 cachpack 原图集逐字节一致（含 RGB 双通道）
    ref, _ = load_orig_atlas()
    rp = atlas.crop((0, 0, AW, ORIG_H))
    same = (rp.tobytes() == ref.tobytes())
    nz_rgb = sum(1 for y in range(ORIG_H) for x in range(AW)
                 if atlas.getpixel((x, y))[0] > 0)
    nz_a = sum(1 for y in range(ORIG_H) for x in range(AW)
               if atlas.getpixel((x, y))[3] > 0)
    print('  %s 原区 y<%d 与原图集逐字节一致（RGB非零 %d / alpha非零 %d）'
          % ('OK' if same else '!!', ORIG_H, nz_rgb, nz_a))
    bad = [(c, x, y) for c, x, y, w, a in placed if y < ORIG_H]
    print('  %s 全部 %d 个新字都落在 y>=%d（越界 %d 个）'
          % ('OK' if not bad else '!!', len(placed), ORIG_H, len(bad)))
    # 逐字符覆盖自检：TSV 需求全部被 表覆盖
    new_codes = keep_codes | set(ord(c) for c, x, y, w, a in placed)
    uncovered = [c for c in need_all if ord(c) not in new_codes]
    print('  %s 需求 %d 字符全部被新表覆盖（未覆盖 %d 个）'
          % ('OK' if not uncovered else '!!', len(need_all), len(uncovered)))
    if uncovered:
        print('     ', uncovered[:40])
    # 新字区墨迹与样式自检
    reg = atlas.crop((0, ORIG_H, AW, AH))
    px = reg.load()
    peak = [0, 0, 0]
    ink = 0
    ink_min_y, ink_max_y = 9999, -1
    for y in range(reg.size[1]):
        for x in range(reg.size[0]):
            r, g, b, a = px[x, y]
            if a:
                ink += 1
                peak = [max(peak[0], r), max(peak[1], g), max(peak[2], b)]
                if r > 0:
                    ink_min_y = min(ink_min_y, y)
                    ink_max_y = max(ink_max_y, y)
    print('  %s 新字区RGB 峰值 %s（期望 [%d %d %d]）'
          % ('OK' if peak == [WHITE_R, WHITE_G, WHITE_B] else '!!',
             peak, WHITE_R, WHITE_G, WHITE_B))
    print('     alpha 非零 %d · 垂直墨迹 y %d..%d（相对原区，全高 %d）'
          % (ink, ink_min_y, ink_max_y, reg.size[1]))

    prev = Image.eval(reg.convert('L'), lambda v: 255 - v)
    prev.save(os.path.join(OUT, '_atlas_new_preview16.png'))
    print('  预览: _atlas_new_preview16.png (%dx%d)' % prev.size)


if __name__ == '__main__':
    main()
