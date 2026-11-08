# -*- coding: utf-8 -*-
"""make_atlas_border_test.py -- 2026-10-25 边框效果测试图集生成器

用户需求：纯白字在暗背景下偏硬/刺眼，先出三套"加边框"测试方案供定稿，
本轮只生成测试包与对比图，【不覆盖正式环境】。

三套方案（布局与正式版完全一致：同一张 table_c.bin、同一批 tile 坐标，
  只有新字区像素不同，纯 A/B 对比）：
  V1 右下角投影：白字 + 1px 纯黑阴影，偏移 (1,1)。不增字距，不错位 UI。
  V2 极细硬描边：白字 + 1px 纯黑硬边（PIL stroke_width=1），
                 字号 24 -> 23（缩小 1px 给黑边留位，防字距溢出）。
  V3 深色微光晕：白字 + 深蓝黑 (24,28,40) 2px 高斯光晕（死亡空间质感）。

双通道预乘口径（与正式版一致，用户前置要求）：
  - RGB = 颜色 x alpha/255，取整一律 int() 截断；
  - 黑边/黑影像素 RGB 恒为 0，alpha 按形状填充；
  - 白芯像素 RGB == alpha；
  - 原区 y<128 逐字节保留；合成用预乘 over（top 上层）。
写入语义与 make_atlas_retail.py 一致：按表顺序整块写 26x26 tile
  （相邻行带 2 行重叠属原有语义，落在引擎 24 行采样带之外，无影响）。

输出：05_hook源码与译文/border_test_2026-10-25/
  variant{1,2,3}_*/big_atlas_c.raw + table_c.bin + install_test.bat + full_preview.png
  border_test_compare.png（暗底主区 + 亮底参考 + 4 倍放大细节）
  restore_white.bat / MD5清单.txt
"""
import os
import struct
import hashlib
from PIL import Image, ImageDraw, ImageFont, ImageFilter

PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVE_DIR = os.path.join(PKG, '05_hook源码与译文', 'payload_loose')
LIVE_ATLAS = os.path.join(LIVE_DIR, 'big_atlas_c.raw')
LIVE_TABLE = os.path.join(LIVE_DIR, 'table_c.bin')
FONT_PATH = os.environ.get('BIGFONT', r'D:\Basic\ChillDuanSans_Bold.otf')
# 标签字体：装机图集同款（已验证 PIL 可渲染中文；06_独有资产的霞鹜文楷
# 在此环境 PIL 中渲染不出字形，勿用）
LABEL_FONT = os.environ.get('BIGLABEL', r'D:\Basic\ChillDuanSans_Regular.otf')
OUTROOT = os.path.join(PKG, '05_hook源码与译文', 'border_test_2026-10-25')

W, H = 512, 4096
ORIG_H = 128
TILE = 26                 # 与正式版一致（TILE=SZ+2, SZ=24）
SZ_BASE = 24              # 正式版字号（装机版为 24px 时代构建）
SZ_STROKE = 23            # V2：缩 1px 给 1px 黑边留位
SHADOW_OFF = (1, 1)       # V1：阴影偏移
GLOW_RGB = (24, 28, 40)   # V3：深蓝黑微光
GLOW_RADIUS = 2.0         # V3：高斯半径

# (key, 中文标签, ASCII标签, 字号)
VARIANTS = [
    ('variant1_shadow', 'V1 右下投影', 'V1-SHADOW', SZ_BASE),
    ('variant2_stroke23', 'V2 硬描边23px', 'V2-STROKE23', SZ_STROKE),
    ('variant3_glow', 'V3 深色光晕', 'V3-GLOW', SZ_BASE),
]

SAMPLE_LINES = [
    '继续游戏', '我们得离开这里', '门被锁住了', '按 Enter 继续', '危险！',
]
ZOOM_CHARS = ['中', '国']


# ---------------------------------------------------------------- 基础工具

def load_live():
    raw = open(LIVE_ATLAS, 'rb').read()
    tbl = open(LIVE_TABLE, 'rb').read()
    recs = [struct.unpack_from('<5H', tbl, 10 * k) for k in range(len(tbl) // 10)]
    return raw, tbl, recs


def render_mask(font, ch, stroke_width=0):
    """把单个字符渲染成 26x26 的 L 掩码（0..255），在 tile 内居中。
    stroke_width>0 时掩码包含 stroke_fill 的外扩边（PIL 官方描边能力）。"""
    tile = Image.new('L', (TILE, TILE), 0)
    td = ImageDraw.Draw(tile)
    bb = td.textbbox((0, 0), ch, font=font, stroke_width=stroke_width)
    td.text(((TILE - (bb[2] - bb[0])) // 2 - bb[0],
             (TILE - (bb[3] - bb[1])) // 2 - bb[1]),
            ch, fill=255, font=font,
            stroke_width=stroke_width, stroke_fill=255)
    return tile


def shift_mask(mask, dx, dy):
    """平移掩码（越界部分丢弃），返回同尺寸 L 图。"""
    out = Image.new('L', (TILE, TILE), 0)
    out.paste(mask, (dx, dy))
    return out


def over_premul(top_rgb, top_a, bot_rgb, bot_a):
    """预乘 alpha over 合成（top 在上层）。int() 截断，与项目口径一致。
    top_rgb/bot_rgb 为已预乘的 RGB（0..255）。"""
    oa = top_a + (bot_a * (255 - top_a)) // 255
    rgb = [top_rgb[c] + (bot_rgb[c] * (255 - top_a)) // 255 for c in range(3)]
    return rgb, oa


# ---------------------------------------------------------------- 三套方案的 tile 合成

def tile_v1_shadow(font, ch):
    """V1：白芯 + 右下 1px 纯黑投影（黑在下层）。"""
    core = render_mask(font, ch)
    shadow = shift_mask(core, SHADOW_OFF[0], SHADOW_OFF[1])
    cp, sp = core.load(), shadow.load()
    tile = Image.new('RGBA', (TILE, TILE), (0, 0, 0, 0))
    tp = tile.load()
    for yy in range(TILE):
        for xx in range(TILE):
            a_c, a_s = cp[xx, yy], sp[xx, yy]
            if not a_c and not a_s:
                continue
            rgb, a = over_premul((a_c, a_c, a_c), a_c, (0, 0, 0), a_s)
            tp[xx, yy] = (rgb[0], rgb[1], rgb[2], a)
    return tile


def tile_v2_stroke(font, ch):
    """V2：白芯 + 1px 纯黑硬边。两次渲染取差集得到边环；
    芯像素 RGB==alpha，边环像素 RGB 恒 0。"""
    core = render_mask(font, ch, stroke_width=0)
    ring = render_mask(font, ch, stroke_width=1)
    cp, rp = core.load(), ring.load()
    tile = Image.new('RGBA', (TILE, TILE), (0, 0, 0, 0))
    tp = tile.load()
    for yy in range(TILE):
        for xx in range(TILE):
            a_c, a_r = cp[xx, yy], rp[xx, yy]
            if a_c:
                tp[xx, yy] = (a_c, a_c, a_c, a_c)      # 白芯（预乘）
            elif a_r:
                tp[xx, yy] = (0, 0, 0, a_r)            # 黑边：RGB=0，alpha 按形状
    return tile


def tile_v3_glow(font, ch):
    """V3：白芯 + 深蓝黑微光晕（高斯模糊的形状掩码，深色预乘，垫在白芯下层）。"""
    core = render_mask(font, ch)
    glow = core.filter(ImageFilter.GaussianBlur(GLOW_RADIUS))
    cp, gp = core.load(), glow.load()
    tile = Image.new('RGBA', (TILE, TILE), (0, 0, 0, 0))
    tp = tile.load()
    for yy in range(TILE):
        for xx in range(TILE):
            a_c, a_g = cp[xx, yy], gp[xx, yy]
            if not a_c and not a_g:
                continue
            g_rgb = (GLOW_RGB[0] * a_g // 255,
                     GLOW_RGB[1] * a_g // 255,
                     GLOW_RGB[2] * a_g // 255)
            rgb, a = over_premul((a_c, a_c, a_c), a_c, g_rgb, a_g)
            tp[xx, yy] = (rgb[0], rgb[1], rgb[2], a)
    return tile


TILE_FN = {'variant1_shadow': tile_v1_shadow,
           'variant2_stroke23': tile_v2_stroke,
           'variant3_glow': tile_v3_glow}


# ---------------------------------------------------------------- 审计（用户前置要求 1）

def audit(name, data, live):
    """严格自检：尺寸/原区/预乘不变量/黑边 RGB=0/白芯 RGB=alpha。"""
    rep = []
    ok = True

    def chk(cond, msg):
        nonlocal ok
        rep.append(('OK' if cond else '!!', msg))
        if not cond:
            ok = False

    chk(len(data) == W * H * 4, 'size=%d (expect %d)' % (len(data), W * H * 4))
    chk(data[:ORIG_H * W * 4] == live[:ORIG_H * W * 4],
        'orig region y<128 byte-identical')
    bad_premul = bad_white = black_px = white_px = glow_px = 0
    for y in range(ORIG_H, H):
        row = y * W * 4
        for x in range(W):
            o = row + x * 4
            r, g, b, a = data[o], data[o + 1], data[o + 2], data[o + 3]
            if r > a or g > a or b > a:
                bad_premul += 1
            if a:
                if r == 0 and g == 0 and b == 0:
                    black_px += 1
                elif r == a and g == a and b == a:
                    white_px += 1
                elif r < a and g < a and b < a:
                    glow_px += 1          # 暗色光晕像素（RGB 深于 alpha）
                else:
                    bad_white += 1
    chk(bad_premul == 0, 'premul invariant RGB<=alpha (violations=%d)' % bad_premul)
    chk(white_px > 0, 'white core pixels RGB==alpha: %d' % white_px)
    if name in ('variant1_shadow', 'variant2_stroke23'):
        chk(black_px > 0, 'black pixels RGB=0 (edge/shadow): %d' % black_px)
    if name == 'variant2_stroke23':
        chk(glow_px == 0 and bad_white == 0,
            'V2 pure two-tone (blended=%d bad=%d)' % (glow_px, bad_white))
    if name == 'variant3_glow':
        chk(glow_px > 0, 'glow pixels (dark halo): %d' % glow_px)
    return ok, rep, {'black': black_px, 'white': white_px,
                     'glow': glow_px, 'bad': bad_premul + bad_white + (
                         glow_px if name == 'variant2_stroke23' else 0)}


# ---------------------------------------------------------------- 预览合成

def unpremul_tile(tile_rgba):
    """预乘 tile -> 直线 alpha tile（供 PIL alpha_composite 预览用）。"""
    w, h = tile_rgba.size
    src = tile_rgba.load()
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            if a:
                op[x, y] = (min(255, r * 255 // a),
                            min(255, g * 255 // a),
                            min(255, b * 255 // a), a)
    return out


def draw_string(canvas, pen_x, pen_y, s, tmap, atlas_img, scale=2):
    """按字形表把字符串画到 canvas 上（模拟引擎采样：每字 advance=adv）。
    注意：原区 ASCII 字形紧密排列（宽 3~11px），必须按记录 w 裁剪、
    高取 24 行采样带；只有新排区（w=26）才是 26px tile。"""
    for chx in s:
        rec = tmap.get(ord(chx))
        if rec is None:
            pen_x += (14 if chx == ' ' else 6) * scale
            continue
        code, x, y, w, adv = rec
        tile = atlas_img.crop((x, y, x + w, y + 24))
        if scale != 1:
            tile = tile.resize((w * scale, 24 * scale), Image.NEAREST)
        canvas.alpha_composite(unpremul_tile(tile), (pen_x, pen_y))
        pen_x += adv * scale


def make_comparison(variant_imgs, tmap, out_png):
    """一张总对比图：暗底主区 + 亮底参考 + 4 倍放大细节。"""
    try:
        lab = ImageFont.truetype(LABEL_FONT, 20)
        lab_s = ImageFont.truetype(LABEL_FONT, 15)
        title_f = ImageFont.truetype(LABEL_FONT, 26)
    except OSError:
        lab = lab_s = title_f = ImageFont.load_default()

    CW, CH = 1280, 1010
    canvas = Image.new('RGBA', (CW, CH), (14, 14, 18, 255))
    dr = ImageDraw.Draw(canvas)
    dr.text((30, 16), '边框效果测试 · 2026-10-25（正式环境未动，定稿后再装）',
            font=title_f, fill=(235, 235, 235, 255))
    dr.text((30, 52), '暗背景主区 / 亮底参考 / 4 倍放大；V2 为 23px，其余 24px；'
                      '布局与正式版一致（同表同坐标，纯 A/B）',
            font=lab_s, fill=(160, 160, 170, 255))

    styles = [('当前纯白（对照）', None)] + [(n, k) for k, n, _, _ in VARIANTS]
    live_img = Image.frombytes('RGBA', (W, H), open(LIVE_ATLAS, 'rb').read())

    col_w, gap = 292, 18
    blk_h = 26 + 5 * 60 + 12
    for si, (label, key) in enumerate(styles):
        cx = 26 + si * (col_w + gap)
        img = live_img if key is None else variant_imgs[key]
        # --- 暗底主区 ---
        dr.rectangle([cx - 6, 92, cx + col_w, 92 + blk_h], fill=(26, 26, 32, 255))
        dr.text((cx, 96), label, font=lab, fill=(255, 255, 255, 255))
        yy = 128
        for line in SAMPLE_LINES:
            draw_string(canvas, cx, yy, line, tmap, img, scale=2)
            yy += 60
        # --- 亮底参考 ---
        by = 92 + blk_h + 16
        dr.rectangle([cx - 6, by, cx + col_w, by + blk_h], fill=(198, 198, 202, 255))
        dr.text((cx, by + 4), label + '（亮底）', font=lab, fill=(20, 20, 24, 255))
        yy = by + 32
        for line in SAMPLE_LINES:
            draw_string(canvas, cx, yy, line, tmap, img, scale=2)
            yy += 60

    # --- 4 倍放大细节（暗底，每样式 2 字）---
    zy = 92 + blk_h + 16 + blk_h + 20
    dr.text((26, zy), '4 倍放大（暗底）：边缘 / 黑边 / 光晕细节', font=lab,
            fill=(235, 235, 235, 255))
    zy += 34
    for si, (label, key) in enumerate(styles):
        cx = 26 + si * (col_w + gap)
        img = live_img if key is None else variant_imgs[key]
        dr.text((cx, zy - 2), label, font=lab_s, fill=(170, 170, 180, 255))
        xx = cx
        for zc in ZOOM_CHARS:
            rec = tmap.get(ord(zc))
            if rec is None:
                continue
            code, x, y, w, adv = rec
            tile = img.crop((x, y, x + TILE, y + TILE))
            tile = tile.resize((TILE * 4, TILE * 4), Image.NEAREST)
            canvas.alpha_composite(unpremul_tile(tile), (xx, zy + 20))
            xx += 118
    canvas.convert('RGB').save(out_png)
    return out_png


def make_full_preview(data, out_png):
    """全图集新字区墨迹预览（黑字白底），供快速扫查 1472 字异常。"""
    img = Image.frombytes('RGBA', (W, H), data).crop((0, ORIG_H, W, 2200))
    img.putalpha(img.getchannel('A').point(lambda v: 255 - v))
    bg = Image.new('RGBA', img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    bg.convert('RGB').save(out_png)
    return out_png


# ---------------------------------------------------------------- 安装/恢复脚本（CRLF ASCII）

def write_crlf(path, lines):
    with open(path, 'wb') as f:
        f.write(('\r\n'.join(lines) + '\r\n').encode('ascii'))


INSTALL_TMPL = [
    '@echo off',
    'rem ============================================================',
    'rem  ObsCure hanhua - border test {tag} (TEST ONLY, not final)',
    'rem  Installs {tag} atlas into game dir; current atlas auto-backed-up.',
    'rem ============================================================',
    'setlocal',
    'set GAME=D:\\SteamLibrary\\steamapps\\common\\Obscure',
    'set SRC=%~dp0big_atlas_c.raw',
    '',
    'if not exist "%SRC%" echo [FAIL] %SRC% missing & exit /b 1',
    'if not exist "%GAME%\\big_atlas_c.raw" echo [FAIL] game atlas missing & exit /b 1',
    '',
    'certutil -hashfile "%SRC%" MD5 | findstr /I "{md5}" >nul',
    'if errorlevel 1 echo [FAIL] source md5 mismatch - refused & exit /b 1',
    '',
    'copy /Y "%GAME%\\big_atlas_c.raw" "%GAME%\\_obscure_backup\\border_test_prev.bak" >nul',
    'copy /Y "%SRC%" "%GAME%\\big_atlas_c.raw" >nul',
    'if errorlevel 1 echo [FAIL] copy failed & exit /b 1',
    '',
    'certutil -hashfile "%GAME%\\big_atlas_c.raw" MD5 | findstr /I "{md5}" >nul',
    'if errorlevel 1 echo [FAIL] verify failed after install & exit /b 1',
    '',
    'echo [OK] {tag} installed (md5 {md5}). Restart game to see the effect.',
    'echo Restore white: run ..\\restore_white.bat',
    'exit /b 0',
]


# ---------------------------------------------------------------- tile 抽检

def spot_check(key, data, recs, font, sample_step=53):
    """抽样把 placed tile 的 24 行采样带与重新渲染的期望 tile 逐像素比对。
    历史 bug：曾用 data[off:off+TILE*TILE*4]=tile.tobytes() 连续写入，
    26x26 tile 被水平涂抹进同一行（tile 第 r 行落到 x+26r 列），
    三套边框图集全部污染；本抽检即为此而立，必须全过。
    注意：只比对 24 行采样带——行带步进 24 / tile 高 26，后一带会覆盖
    前一带第 24-25 行（装机图集既有语义，引擎不采样这两行，无害）。"""
    img = Image.frombytes('RGBA', (W, H), data)
    px = img.load()
    fn = TILE_FN[key]
    bad = []
    checked = 0
    for n, (code, x, y, w, adv) in enumerate(recs[150:]):
        if n % sample_step:
            continue
        exp = fn(font, chr(code))
        ep = exp.load()
        checked += 1
        mismatch = False
        for yy in range(24):                 # 引擎采样带 = 24 行
            for xx in range(TILE):
                if ep[xx, yy] != px[x + xx, y + yy]:
                    mismatch = True
                    break
            if mismatch:
                break
        if mismatch:
            bad.append((chr(code), x, y))
    return checked, bad


# ---------------------------------------------------------------- 主流程

def main():
    os.makedirs(OUTROOT, exist_ok=True)
    live_raw, live_tbl, recs = load_live()
    live_md5 = hashlib.md5(live_raw).hexdigest()
    tmap = {r[0]: r for r in recs}
    placed = recs[150:]
    print('live atlas md5=%s  table recs=%d (placed=%d)' %
          (live_md5, len(recs), len(placed)))

    missing = set()
    for line in SAMPLE_LINES:
        for c in line:
            if ord(c) >= 128 and ord(c) not in tmap:
                missing.add(c)
    for c in ZOOM_CHARS:
        if ord(c) not in tmap:
            missing.add(c)
    if missing:
        print('!! sample chars missing from table: %s' % ''.join(sorted(missing)))

    manifest = []
    variant_imgs = {}
    for key, label, tag, sz in VARIANTS:
        font = ImageFont.truetype(FONT_PATH, sz)
        atlas = Image.frombytes('RGBA', (W, H), live_raw)
        fn = TILE_FN[key]
        for code, x, y, w, adv in placed:
            tile = fn(font, chr(code))
            atlas.paste(tile, (x, y))   # 行距正确：整块 26x26 落到 (x,y)
        out = atlas.tobytes()
        checked, bad = spot_check(key, out, recs, font)
        print('\n=== %s (%s / %s, font %dpx) ===' % (key, label, tag, sz))
        print('  [tile spot-check] %d tiles pixel-verified, bad=%s' % (checked, bad or 'none'))
        assert not bad, '%s tile spot-check failed: %s' % (key, bad[:5])
        ok, rep, stats = audit(key, out, live_raw)
        print('\n=== %s (%s / %s, font %dpx) ===' % (key, label, tag, sz))
        for flag, msg in rep:
            print('  [%s] %s' % (flag, msg))
        assert ok, '%s audit failed' % key

        vdir = os.path.join(OUTROOT, key)
        os.makedirs(vdir, exist_ok=True)
        raw_path = os.path.join(vdir, 'big_atlas_c.raw')
        open(raw_path, 'wb').write(out)
        open(os.path.join(vdir, 'table_c.bin'), 'wb').write(live_tbl)
        md5 = hashlib.md5(out).hexdigest()
        make_full_preview(out, os.path.join(vdir, 'full_preview.png'))
        write_crlf(os.path.join(vdir, 'install_test.bat'),
                   [l.format(tag=tag, md5=md5) for l in INSTALL_TMPL])
        variant_imgs[key] = Image.frombytes('RGBA', (W, H), out)
        manifest.append((key, label, tag, sz, md5, len(out),
                         stats['black'], stats['white'], stats['glow']))
        print('  -> %s  md5=%s' % (raw_path, md5))

    write_crlf(os.path.join(OUTROOT, 'restore_white.bat'), [
        '@echo off',
        'rem Restore the current WHITE atlas (undo border test install)',
        'setlocal',
        'set GAME=D:\\SteamLibrary\\steamapps\\common\\Obscure',
        'set WHT=%GAME%\\_obscure_backup\\colorfix_white_2026-10-25\\big_atlas_c.raw.white',
        'if not exist "%WHT%" echo [FAIL] white backup missing & exit /b 1',
        'copy /Y "%WHT%" "%GAME%\\big_atlas_c.raw" >nul',
        'if errorlevel 1 echo [FAIL] copy failed & exit /b 1',
        'certutil -hashfile "%GAME%\\big_atlas_c.raw" MD5 | findstr /I "664ad2164508d8e7d4a53c27a3dc7195" >nul',
        'if errorlevel 1 echo [WARN] md5 mismatch after restore & exit /b 1',
        'echo [OK] white atlas restored (md5 664ad216...). Restart game.',
        'exit /b 0',
    ])

    png = make_comparison(variant_imgs, tmap,
                          os.path.join(OUTROOT, 'border_test_compare.png'))
    print('\ncomparison image: %s' % png)

    with open(os.path.join(OUTROOT, 'MD5清单.txt'), 'w', encoding='utf-8') as f:
        f.write('border test 2026-10-25  (live env untouched)\n')
        f.write('live white atlas md5: %s\n\n' % live_md5)
        for key, label, tag, sz, md5, size, black, white, glow in manifest:
            f.write('%s  %s (%s, %dpx)\n  md5=%s\n  size=%d  '
                    'black_px=%d  white_px=%d  glow_px=%d\n'
                    % (key, label, tag, sz, md5, size, black, white, glow))
        f.write('\ntable_c.bin identical to live in all variants (md5 %s)\n'
                % hashlib.md5(live_tbl).hexdigest())
    print('manifest: %s' % os.path.join(OUTROOT, 'MD5清单.txt'))
    print('\nDONE. Live environment NOT modified.')


if __name__ == '__main__':
    main()
