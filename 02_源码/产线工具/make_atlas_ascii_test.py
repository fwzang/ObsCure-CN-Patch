# -*- coding: utf-8 -*-
"""make_atlas_ascii_test.py -- 2026-10-25 ASCII/英文数字字体统一测试生成器

问题（用户报告）："发现enter等英文还是数字还是字体不一致"。
根因：英文/数字走图集原区 y<128 的原版字体（约 10px、白芯黑边浮雕风），
汉字为端黑 24px 纯白，混排必然不一致。原区 150 条记录经诊断全部是拉丁
文本字形（ASCII + 拉丁1补充 + 货币/符号），无图标槽；手柄按键等特殊图标
是 4538 个孤儿墨迹像素，全部位于记录区域之外（已连通分量+分布核验），
本包所有变体对其零改动（审计逐字节保证）。

本轮出三套统一方案供定稿，【不覆盖正式环境】。颜色暂纯白（与当前正式版
一致）；黑边/投影风格待边框方案（V1/V2/V3）定稿后同款联动重跑。

24 行采样带硬约束（重要）：引擎每字形只采样 24 行。端黑 24px 拉丁墨迹
纵跨 28 行（重音顶到降部底），物理上放不进采样带——汉字放得进是因为
CJK 字形按 em 盒设计（墨迹<=24 行）。实测 20px 是唯一能让全部 152 个
字符以统一基线恰好落带内的尺寸（极端字形顶/底正在 0/23 行），故重排
变体取 20px（拉丁 20px 配汉字 24px ≈ 0.8 倍，正是 CJK/拉丁混排标准比例）。

方案：
  V4a 原位拟合（V4-FIT）：原区 150 条记录坐标/度量全部不动——
     table_c.bin 与正式版逐字节一致（零表风险），仅重绘 tile 像素：
     端黑按单元格宽拟合字号（小字带 cap 15px，y=100 大符号带 cap 24px），
     纯白预乘（RGB==alpha），墨迹盒左缘/底缘与原字形对齐（保基线），
     写后逐像素读回校验。
  V4b 20px 自然字距（V4-L20）：20px 端黑，tile 落空闲行带 y>=2144
     （x 步进 27、每带 18 槽、带间步进 24，与排放区同布局），墨迹左对齐
     列 0 + 基线行 19；记录改指向，w=墨迹宽，adv=墨迹宽+3；表记录数不变。
  V4c 20px 汉字式字距（V4-L20K）：同 20px 端黑，但用汉字规（墨迹 26 格
     居中、w=26、adv=27）——与排放区汉字完全同节奏，字距更宽松。

转换集：原区 150 条全部 + 排放区 >(U+003E) þ(U+00FE) ý(U+00FD) 三条
（后三者本是 24px 端黑，重排后统一拉丁度量）。中文引号 U+201C/U+201D
保持汉字度量不动。已知限制：< | ` 四符原版即无字形（表不加记录，保持现状）。

写入纪律（历史 bug，勿复发）：tile 必须按 512 行距步进写入——用 PIL
paste（直拷不混合）。曾用 data[off:off+TILE*TILE*4]=tile.tobytes() 连续
写法，26x26 tile 被水平涂抹进同一行（tile 第 r 行落到 x+26r 列），边框
测试包 V1/V2/V3 曾因此整体污染（已修复重产+全量复验）。另：重排 tile 的
墨迹必须与记录 w 同约定（左对齐墨迹+ w=墨迹宽，或居中墨迹+ w=26），
混用会导致引擎裁剪只取到左半截。

审计：尺寸 / 非写入区逐字节一致 / 预乘不变量 / 表改动集合精确匹配 /
记录越界 / 新写入区原为空 / tile 抽样逐像素比对（24 行采样带）。
"""
import os
import struct
import hashlib
from PIL import Image, ImageDraw, ImageFont

PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVE_DIR = os.path.join(PKG, '05_hook源码与译文', 'payload_loose')
LIVE_ATLAS = os.path.join(LIVE_DIR, 'big_atlas_c.raw')
LIVE_TABLE = os.path.join(LIVE_DIR, 'table_c.bin')
FONT_PATH = os.environ.get('BIGFONT', r'D:\Basic\ChillDuanSans_Bold.otf')
LABEL_FONT = os.environ.get('BIGLABEL', r'D:\Basic\ChillDuanSans_Regular.otf')
OUTROOT = os.path.join(PKG, '05_hook源码与译文', 'ascii_unify_test_2026-10-25')

W, H = 512, 4096
ORIG_H = 128
TILE = 26
BAND = 24                  # 引擎采样带行数（硬约束）
BASELINE_ROW = 19          # 20px 端黑：基线行 19 时全字符集恰好落带内
N_ORIGIN = 150
FREE_Y0 = 2144             # 排放区最后一带 y=2118 + 26
SLOT_DX = 27               # 与排放区一致
SLOTS_PER_BAND = 18        # 18*27+26 = 512
BAND_DY = 24               # 与排放区一致（tile 26，带间 2 行重叠落在采样带外）
CAP_SMALL = 15             # V4a 小字带拟合上限
CAP_BIG = 24               # V4a 大符号带（w>=24）拟合上限
PLACED_EXTRA = (0x003E, 0x00FE, 0x00FD)   # > þ ý（排放区端黑，重排统一度量）
KEEP_CODES = (0x201C, 0x201D)             # 中文引号保持汉字度量

# (key, 中文标签, ASCII标签, 字号, 模式; None=原位, 'flush'=左对齐, 'cjk'=汉字规)
VARIANTS = [
    ('variant4a_fit', 'V4a 原位拟合', 'V4-FIT', None, None),
    ('variant4b_l20', 'V4b 20px 自然字距', 'V4-L20', 20, 'flush'),
    ('variant4c_l20k', 'V4c 20px 汉字式字距', 'V4-L20K', 20, 'cjk'),
]

SAMPLE_LINES = [
    '按 Enter 继续', 'HP 100%', 'F1 保存', 'Loading... 45%',
    '选项 Options', '按 ESC 返回', 'New Game', '生命 > 100',
]
LATIN_STRIP = 'ABCDEFghij 0123'
ZOOM_STRINGS = ['Enter', '100%']


# ---------------------------------------------------------------- 基础工具

def load_live():
    raw = open(LIVE_ATLAS, 'rb').read()
    tbl = open(LIVE_TABLE, 'rb').read()
    recs = [struct.unpack_from('<5H', tbl, 10 * k) for k in range(len(tbl) // 10)]
    return raw, tbl, recs


_font_cache = {}


def font_at(sz):
    if sz not in _font_cache:
        _font_cache[sz] = ImageFont.truetype(FONT_PATH, sz)
    return _font_cache[sz]


def fit_size(ch, wcap, cap):
    """端黑最大字号：墨迹宽<=wcap 且高<=24，再封顶 cap。"""
    best = 1
    for sz in range(1, 25):
        f = font_at(sz)
        bb = f.getbbox(ch)
        if bb[2] - bb[0] <= wcap and bb[3] - bb[1] <= 24:
            best = sz
    return min(best, cap)


def render_cjk_tile(font, ch):
    """汉字规：墨迹在 26 格内居中（与排放区汉字同口径）。返回 (tile, w)。"""
    tile = Image.new('L', (TILE, TILE), 0)
    td = ImageDraw.Draw(tile)
    bb = td.textbbox((0, 0), ch, font=font)
    td.text(((TILE - (bb[2] - bb[0])) // 2 - bb[0],
             (TILE - (bb[3] - bb[1])) // 2 - bb[1]),
            ch, fill=255, font=font)
    fb = tile.getbbox()
    assert fb is not None and fb[1] >= 0 and fb[3] <= BAND, \
        'glyph %r outside 24-row band: %s' % (ch, fb)
    return tile, TILE


def render_flush_tile(font, ch):
    """原版 ASCII 规：墨迹左对齐列 0 + 基线行 BASELINE_ROW。返回 (tile, ink_w)。
    20px 端黑实测：全字符集墨迹高<=22，基线行 19 时极端字形顶/底正在
    带内 0/23 行；守卫逻辑兜底平移，任何输入都不裁切。"""
    ascent, _ = font.getmetrics()
    bb = font.getbbox(ch)
    draw_y = BASELINE_ROW - ascent
    if draw_y + bb[1] < 0:                 # 顶部越界（重音帽）-> 下移
        draw_y = -bb[1]
    if draw_y + bb[3] > BAND:              # 底部越界（降部）-> 上移
        draw_y = BAND - bb[3]
    tile = Image.new('L', (TILE, TILE), 0)
    ImageDraw.Draw(tile).text((-bb[0], draw_y), ch, fill=255, font=font)
    fb = tile.getbbox()
    assert fb is not None, 'empty glyph %r' % ch
    assert fb[1] >= 0 and fb[3] <= BAND, 'glyph %r outside band: %s' % (ch, fb)
    return tile, fb[2] - fb[0]


RENDERERS = {'cjk': render_cjk_tile, 'flush': render_flush_tile}


def mask_to_premul_white(mask):
    """L 掩码 -> 纯白预乘 RGBA tile（RGB==alpha）。"""
    t = Image.new('RGBA', mask.size, (0, 0, 0, 0))
    tp = t.load()
    mp = mask.load()
    for y in range(mask.size[1]):
        for x in range(mask.size[0]):
            v = mp[x, y]
            if v:
                tp[x, y] = (v, v, v, v)
    return t


# ---------------------------------------------------------------- V4a 原位拟合

def build_v4a(live_raw, recs):
    """原区记录逐条重绘；表零改动。返回 (data, changed_regions, sizes)。"""
    img = Image.frombytes('RGBA', (W, H), live_raw)
    px = img.load()
    data = bytearray(live_raw)
    regions = []          # 写入的 (x,y,w,h) 区域（审计用）
    done_shared = set()   # 已处理的共享区域（Ê/ð/õ 三码共用 tile）
    sizes = {}
    for idx in range(N_ORIGIN):
        code, x, y, w, adv = recs[idx]
        if code == 0x20:                       # space 无墨迹，跳过
            continue
        key = (x, y, w)
        if key in done_shared:
            continue
        done_shared.add(key)
        # 原墨迹盒
        dx0 = dy0 = None
        dx1 = dy1 = -1
        for yy in range(y, y + 24):
            for xx in range(x, x + w):
                if px[xx, yy][3]:
                    if dx0 is None or xx < dx0: dx0 = xx
                    if dy0 is None or yy < dy0: dy0 = yy
                    if xx > dx1: dx1 = xx
                    if yy > dy1: dy1 = yy
        if dx1 < 0:                            # 无墨迹记录，跳过
            continue
        dx0, dy0, dx1, dy1 = dx0 - x, dy0 - y, dx1 - x, dy1 - y
        cap = CAP_BIG if w >= 24 else CAP_SMALL
        ch = chr(code)
        sz = fit_size(ch, w, cap)
        sizes[ch] = sz
        # 清空整个记录区域再重绘（黑边浮雕全部替换为纯白端黑）
        for yy in range(y, y + 24):
            for xx in range(x, x + w):
                data[(yy * W + xx) * 4:(yy * W + xx) * 4 + 4] = b'\x00\x00\x00\x00'
        # 在区域内作画：墨迹左缘对齐 dx0、底缘对齐 dy1
        reg = Image.new('L', (w, 24), 0)
        td = ImageDraw.Draw(reg)
        f = font_at(sz)
        bb = td.textbbox((0, 0), ch, font=f)
        td.text((dx0 - bb[0], dy1 - bb[3] + 1), ch, fill=255, font=f)
        rp = reg.load()
        for yy in range(24):
            for xx in range(w):
                v = rp[xx, yy]
                if v:
                    o = ((y + yy) * W + x + xx) * 4
                    data[o:o + 4] = bytes((v, v, v, v))
        regions.append((x, y, w, 24))
        # 写后即读回校验：区域像素必须与意图完全一致（行距索引正确性）
        for yy in range(24):
            for xx in range(w):
                v = rp[xx, yy]
                o = ((y + yy) * W + x + xx) * 4
                want = bytes((v, v, v, v)) if v else b'\x00\x00\x00\x00'
                if bytes(data[o:o + 4]) != want:
                    raise AssertionError('V4a write-back mismatch at (%d,%d)'
                                         % (x + xx, y + yy))
    return bytes(data), regions, sizes


# ---------------------------------------------------------------- V4b/c 重排

def build_relayout(live_raw, live_tbl, recs, size, mode):
    """新字号端黑落空闲行带，记录改指向；返回 (data, tbl, newrecs, stats)。

    行距纪律：tile 用 PIL paste 落入 (sx,sy)（直拷不混合，按 512 行距
    步进）。墨迹约定必须与记录 w 一致：'flush' 墨迹左对齐 + w=墨迹宽 +
    adv=墨迹宽+3；'cjk' 墨迹 26 格居中 + w=26 + adv=27。
    """
    data = bytearray(live_raw)
    tbl = bytearray(live_tbl)
    atlas = Image.frombytes('RGBA', (W, H), live_raw)
    font = font_at(size)
    renderer = RENDERERS[mode]
    adv_space = max(1, round(font.getlength(' ')))

    # 收集转换索引（保持表顺序）
    conv = list(range(N_ORIGIN))
    for i in range(N_ORIGIN, len(recs)):
        if recs[i][0] in PLACED_EXTRA:
            conv.append(i)

    # 新写入区必须原为空
    for yy in range(FREE_Y0, H):
        row = data[yy * W * 4:(yy + 1) * W * 4]
        if any(row):
            raise AssertionError('new area y>=%d not empty in live atlas' % FREE_Y0)

    newrecs = {}
    for n, idx in enumerate(conv):
        code = recs[idx][0]
        band, slot = divmod(n, SLOTS_PER_BAND)
        sx = slot * SLOT_DX
        sy = FREE_Y0 + band * BAND_DY
        if code == 0x20:                       # space：无 tile，仅步进
            w, adv = 0, adv_space
        else:
            ch = chr(code)
            mask, w = renderer(font, ch)
            tile = mask_to_premul_white(mask)
            atlas.paste(tile, (sx, sy))        # 行距正确：整块 26x26 落到 (sx,sy)
            if mode == 'cjk':
                adv = 27                       # 汉字规步进
            else:
                adv = w + 3                    # 自然字距：墨迹宽 + 3px 间距
        struct.pack_into('<5H', tbl, 10 * idx, code, sx, sy, w, adv)
        newrecs[idx] = (code, sx, sy, w, adv)
    data = bytearray(atlas.tobytes())
    return bytes(data), bytes(tbl), newrecs, {'size': size, 'n': len(conv)}


def spot_check_tiles(data, newrecs, size, mode, sample_step=23):
    """抽样把新 tile 的 24 行采样带与重新渲染的期望 tile 逐像素比对
    （曾抓住连续字节写入把 tile 水平涂抹的 bug）。
    只比对 24 行采样带：行带步进 24 / tile 高 26，后一带覆盖前一带
    第 24-25 行是装机图集既有语义，引擎不采样这两行。"""
    font = font_at(size)
    renderer = RENDERERS[mode]
    img = Image.frombytes('RGBA', (W, H), data)
    px = img.load()
    bad = []
    checked = 0
    for n, (idx, (code, sx, sy, w, adv)) in enumerate(sorted(newrecs.items())):
        if code == 0x20 or n % sample_step:
            continue
        exp = mask_to_premul_white(renderer(font, chr(code))[0])
        ep = exp.load()
        checked += 1
        mismatch = False
        for yy in range(BAND):                 # 引擎采样带 = 24 行
            for xx in range(TILE):
                if ep[xx, yy] != px[sx + xx, sy + yy]:
                    mismatch = True
                    break
            if mismatch:
                break
        if mismatch:
            bad.append((chr(code), sx, sy))
    return checked, bad


# ---------------------------------------------------------------- 审计

def audit_common(name, data, live_raw, written_check):
    rep = []
    ok = True

    def chk(cond, msg):
        nonlocal ok
        rep.append(('OK' if cond else '!!', msg))
        if not cond:
            ok = False

    chk(len(data) == W * H * 4, 'size=%d' % len(data))
    img = Image.frombytes('RGBA', (W, H), data)
    px = img.load()
    sub_ok, sub_msgs = written_check(px)
    for m in sub_msgs:
        rep.append(('OK' if m[0] else '!!', m[1]))
        if not m[0]:
            ok = False
    return ok, rep


def audit_v4a(data, live_raw, regions):
    def wc(px):
        msgs = []
        live = Image.frombytes('RGBA', (W, H), live_raw).load()
        inregion = [[False] * W for _ in range(H)]
        for (x, y, w, h) in regions:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    inregion[yy][xx] = True
        diff_out = 0
        bad_white = 0
        live_viol = 0
        for yy in range(H):
            for xx in range(W):
                r, g, b, a = px[xx, yy]
                if inregion[yy][xx]:
                    if a and not (r == a and g == a and b == a):
                        bad_white += 1
                else:
                    lr, lg, lb, la = live[xx, yy]
                    if (r, g, b, a) != (lr, lg, lb, la):
                        diff_out += 1
                    if lr > la or lg > la or lb > la:
                        live_viol += 1
        msgs.append((diff_out == 0,
                     'non-written pixels byte-identical (diff=%d; incl. 4538 gamepad-icon px)' % diff_out))
        msgs.append((bad_white == 0, 'written regions pure white RGB==alpha (violations=%d)' % bad_white))
        msgs.append((True, 'note: live atlas itself has %d RGB>alpha px in origin area '
                           '(original font is straight-alpha, not premultiplied)' % live_viol))
        return (diff_out == 0 and bad_white == 0), msgs
    return audit_common('variant4a_fit', data, live_raw, wc)


def audit_relayout(name, data, live_raw, live_tbl, new_tbl, newrecs):
    def wc(px):
        msgs = []
        live = Image.frombytes('RGBA', (W, H), live_raw).load()
        # 1) y<FREE_Y0 全部逐字节一致（原区含手柄图标 + 排放区均未动）
        diff_old = 0
        for yy in range(FREE_Y0):
            for xx in range(W):
                if px[xx, yy] != live[xx, yy]:
                    diff_old += 1
        msgs.append((diff_old == 0, 'y<%d byte-identical (diff=%d; incl. gamepad icons)' % (FREE_Y0, diff_old)))
        # 2) 新区纯白预乘
        bad_premul = bad_white = newpx = 0
        for yy in range(FREE_Y0, H):
            for xx in range(W):
                r, g, b, a = px[xx, yy]
                if r > a or g > a or b > a:
                    bad_premul += 1
                if a:
                    newpx += 1
                    if not (r == a and g == a and b == a):
                        bad_white += 1
        msgs.append((bad_premul == 0, 'new area premul invariant (violations=%d)' % bad_premul))
        msgs.append((bad_white == 0, 'new area pure white (violations=%d)' % bad_white))
        msgs.append((newpx > 0, 'new area ink pixels=%d' % newpx))
        return (diff_old == 0 and bad_premul == 0 and bad_white == 0), msgs

    ok, rep = audit_common(name, data, live_raw, wc)
    # 3) 表审计：改动集合精确匹配
    n = len(live_tbl) // 10
    assert len(new_tbl) == len(live_tbl), 'table size changed'
    bad_idx = []
    for i in range(n):
        a = live_tbl[10 * i:10 * i + 10]
        b = new_tbl[10 * i:10 * i + 10]
        if a != b:
            if i not in newrecs:
                bad_idx.append(i)
            else:
                code, x, y, w, adv = struct.unpack('<5H', b)
                if code != newrecs[i][0] or (x, y, w, adv) != newrecs[i][1:]:
                    bad_idx.append(i)
                if x + w > W or y + BAND > H or w > TILE:
                    bad_idx.append(i)
    missing = [i for i in newrecs if new_tbl[10 * i:10 * i + 10] == live_tbl[10 * i:10 * i + 10]]
    rep.append(('OK' if not bad_idx else '!!',
                'table diff == expected set (unexpected/missing=%s)' % (bad_idx[:5] or 'none')))
    rep.append(('OK' if not missing else '!!', 'all converted records written (%d)' % len(newrecs)))
    ok = ok and not bad_idx and not missing
    return ok, rep


# ---------------------------------------------------------------- 预览

def unpremul_tile(tile_rgba):
    w, h = tile_rgba.size
    src = tile_rgba.load()
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            if a:
                op[x, y] = (min(255, r * 255 // a), min(255, g * 255 // a),
                            min(255, b * 255 // a), a)
    return out


def draw_string(canvas, pen_x, pen_y, s, tmap, atlas_img, scale=2):
    for chx in s:
        rec = tmap.get(ord(chx))
        if rec is None:
            pen_x += (14 if chx == ' ' else 6) * scale
            continue
        code, x, y, w, adv = rec
        if w == 0:
            pen_x += adv * scale
            continue
        tile = atlas_img.crop((x, y, x + w, y + BAND))
        if scale != 1:
            tile = tile.resize((w * scale, BAND * scale), Image.NEAREST)
        canvas.alpha_composite(unpremul_tile(tile), (pen_x, pen_y))
        pen_x += adv * scale


def make_comparison(variant_imgs, tmaps, out_png):
    """tmaps: {variant_key或None: 该变体的字形表 dict}——V4b/c 改了记录坐标，
    必须用各自的表裁剪，否则画出来还是原版字形。"""
    try:
        lab = ImageFont.truetype(LABEL_FONT, 20)
        lab_s = ImageFont.truetype(LABEL_FONT, 15)
        title_f = ImageFont.truetype(LABEL_FONT, 26)
    except OSError:
        lab = lab_s = title_f = ImageFont.load_default()

    CW, CH = 1280, 1430
    canvas = Image.new('RGBA', (CW, CH), (14, 14, 18, 255))
    dr = ImageDraw.Draw(canvas)
    dr.text((30, 16), '英文/数字字体统一测试 · 2026-10-25（正式环境未动，定稿后再装）',
            font=title_f, fill=(235, 235, 235, 255))
    dr.text((30, 52), '暗背景主区 / 亮底参考 / 4 倍放大；V4a 表零改动；V4b/c 改表同记录数；'
                      '20px 为 24 行采样带内统一基线的最大字号（24px 拉丁墨迹纵跨 28 行放不下）；'
                      '颜色暂纯白，黑边风格待边框定稿联动',
            font=lab_s, fill=(160, 160, 170, 255))

    styles = [('当前纯白（对照）', None)] + [(n, k) for k, n, _, _, _ in VARIANTS]
    live_img = Image.frombytes('RGBA', (W, H), open(LIVE_ATLAS, 'rb').read())

    col_w, gap = 292, 18
    blk_h = 26 + len(SAMPLE_LINES) * 60 + 12
    for si, (label, key) in enumerate(styles):
        cx = 26 + si * (col_w + gap)
        img = live_img if key is None else variant_imgs[key]
        tmap = tmaps[key]
        dr.rectangle([cx - 6, 92, cx + col_w, 92 + blk_h], fill=(26, 26, 32, 255))
        dr.text((cx, 96), label, font=lab, fill=(255, 255, 255, 255))
        yy = 128
        for line in SAMPLE_LINES:
            draw_string(canvas, cx, yy, line, tmap, img, scale=2)
            yy += 60
        by = 92 + blk_h + 16
        dr.rectangle([cx - 6, by, cx + col_w, by + blk_h], fill=(198, 198, 202, 255))
        dr.text((cx, by + 4), label + '（亮底）', font=lab, fill=(20, 20, 24, 255))
        yy = by + 32
        for line in SAMPLE_LINES:
            draw_string(canvas, cx, yy, line, tmap, img, scale=2)
            yy += 60

    # 纯拉丁对照行
    ly = 92 + blk_h + 16 + blk_h + 20
    dr.text((26, ly), '纯拉丁对照（字号/字形一致性）', font=lab, fill=(235, 235, 235, 255))
    ly += 32
    for si, (label, key) in enumerate(styles):
        cx = 26 + si * (col_w + gap)
        img = live_img if key is None else variant_imgs[key]
        tmap = tmaps[key]
        dr.text((cx, ly - 2), label, font=lab_s, fill=(170, 170, 180, 255))
        dr.rectangle([cx - 6, ly + 20, cx + col_w, ly + 20 + 34], fill=(26, 26, 32, 255))
        draw_string(canvas, cx, ly + 24, LATIN_STRIP, tmap, img, scale=2)

    # 4 倍放大
    zy = ly + 20 + 34 + 24
    dr.text((26, zy), '4 倍放大（暗底）：Enter / 100%', font=lab, fill=(235, 235, 235, 255))
    zy += 34
    for si, (label, key) in enumerate(styles):
        cx = 26 + si * (col_w + gap)
        img = live_img if key is None else variant_imgs[key]
        tmap = tmaps[key]
        dr.text((cx, zy - 2), label, font=lab_s, fill=(170, 170, 180, 255))
        yy = zy + 20
        for zs in ZOOM_STRINGS:
            draw_string(canvas, cx, yy, zs, tmap, img, scale=4)
            yy += BAND * 4 + 12
    canvas.convert('RGB').save(out_png)
    return out_png


def make_full_preview(data, out_png, y0=ORIG_H, y1=2400):
    img = Image.frombytes('RGBA', (W, H), data).crop((0, y0, W, y1))
    img.putalpha(img.getchannel('A').point(lambda v: 255 - v))
    bg = Image.new('RGBA', img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    bg.convert('RGB').save(out_png)
    return out_png


# ---------------------------------------------------------------- 安装/恢复脚本

def write_crlf(path, lines):
    with open(path, 'wb') as f:
        f.write(('\r\n'.join(lines) + '\r\n').encode('ascii'))


INSTALL_TMPL = [
    '@echo off',
    'rem ============================================================',
    'rem  ObsCure hanhua - ASCII unify test {tag} (TEST ONLY, not final)',
    'rem  Installs {tag} atlas{and_table} into game dir; current files auto-backed-up.',
    'rem ============================================================',
    'setlocal',
    'set GAME=D:\\SteamLibrary\\steamapps\\common\\Obscure',
    'set SRC=%~dp0',
    'set BAK=%GAME%\\_obscure_backup\\ascii_test_prev.bak',
    '',
    'if not exist "%SRC%big_atlas_c.raw" echo [FAIL] source atlas missing & exit /b 1',
    'if not exist "%GAME%\\big_atlas_c.raw" echo [FAIL] game atlas missing & exit /b 1',
    '',
    'certutil -hashfile "%SRC%big_atlas_c.raw" MD5 | findstr /I "{md5}" >nul',
    'if errorlevel 1 echo [FAIL] source atlas md5 mismatch - refused & exit /b 1',
    '',
    'if not exist "%BAK%" mkdir "%BAK%"',
    'copy /Y "%GAME%\\big_atlas_c.raw" "%BAK%\\big_atlas_c.raw" >nul',
    'copy /Y "%GAME%\\table_c.bin" "%BAK%\\table_c.bin" >nul',
    'copy /Y "%SRC%big_atlas_c.raw" "%GAME%\\big_atlas_c.raw" >nul',
    'if errorlevel 1 echo [FAIL] copy atlas failed & exit /b 1',
    '{table_lines}',
    'certutil -hashfile "%GAME%\\big_atlas_c.raw" MD5 | findstr /I "{md5}" >nul',
    'if errorlevel 1 echo [FAIL] verify failed after install & exit /b 1',
    '',
    'echo [OK] {tag} installed (atlas md5 {md5}). Restart game to see the effect.',
    'echo Restore white: run ..\\restore_white.bat',
    'exit /b 0',
]

TABLE_LINES = [
    'certutil -hashfile "%SRC%table_c.bin" MD5 | findstr /I "{tmd5}" >nul',
    'if errorlevel 1 echo [FAIL] source table md5 mismatch - refused & exit /b 1',
    'copy /Y "%SRC%table_c.bin" "%GAME%\\table_c.bin" >nul',
    'if errorlevel 1 echo [FAIL] copy table failed & exit /b 1',
    'certutil -hashfile "%GAME%\\table_c.bin" MD5 | findstr /I "{tmd5}" >nul',
    'if errorlevel 1 echo [FAIL] verify table failed after install & exit /b 1',
]


# ---------------------------------------------------------------- 主流程

def main():
    os.makedirs(OUTROOT, exist_ok=True)
    live_raw, live_tbl, recs = load_live()
    live_md5 = hashlib.md5(live_raw).hexdigest()
    live_tmd5 = hashlib.md5(live_tbl).hexdigest()
    tmap = {r[0]: r for r in recs}
    print('live atlas md5=%s  table md5=%s  recs=%d' % (live_md5, live_tmd5, len(recs)))

    manifest = []
    variant_imgs = {}
    tmaps = {None: tmap}
    for key, label, tag, sz, mode in VARIANTS:
        print('\n=== %s (%s / %s) ===' % (key, label, tag))
        if mode is None:
            data, regions, sizes = build_v4a(live_raw, recs)
            new_tbl = live_tbl
            tmaps[key] = tmap          # 表未变，用 live 表
            ok, rep = audit_v4a(data, live_raw, regions)
            stats = {'regions': len(regions),
                     'sizes': '%d..%dpx' % (min(sizes.values()), max(sizes.values()))}
        else:
            data, new_tbl, newrecs, stats = build_relayout(live_raw, live_tbl, recs, sz, mode)
            new_recs = [struct.unpack_from('<5H', new_tbl, 10 * k)
                        for k in range(len(new_tbl) // 10)]
            tmaps[key] = {r[0]: r for r in new_recs}
            checked, bad = spot_check_tiles(data, newrecs, sz, mode)
            print('  [tile spot-check] %d tiles pixel-verified (24-row band), bad=%s'
                  % (checked, bad or 'none'))
            assert not bad, '%s tile spot-check failed: %s' % (key, bad[:5])
            ok, rep = audit_relayout(key, data, live_raw, live_tbl, new_tbl, newrecs)
            stats['regions'] = len(newrecs)
        for flag, msg in rep:
            print('  [%s] %s' % (flag, msg))
        assert ok, '%s audit failed' % key

        vdir = os.path.join(OUTROOT, key)
        os.makedirs(vdir, exist_ok=True)
        raw_path = os.path.join(vdir, 'big_atlas_c.raw')
        open(raw_path, 'wb').write(data)
        open(os.path.join(vdir, 'table_c.bin'), 'wb').write(new_tbl)
        md5 = hashlib.md5(data).hexdigest()
        tmd5 = hashlib.md5(new_tbl).hexdigest()
        make_full_preview(data, os.path.join(vdir, 'full_preview.png'),
                          y0=0 if mode is None else FREE_Y0,
                          y1=ORIG_H if mode is None else 2400)
        if mode is None:
            install = [l.format(tag=tag, md5=md5, and_table='', table_lines='')
                       for l in INSTALL_TMPL]
        else:
            tl = '\n'.join(l.format(tmd5=tmd5) for l in TABLE_LINES)
            install = [l.format(tag=tag, md5=md5, and_table=' + table_c.bin',
                                table_lines=tl) for l in INSTALL_TMPL]
        write_crlf(os.path.join(vdir, 'install_test.bat'), install)
        variant_imgs[key] = Image.frombytes('RGBA', (W, H), data)
        manifest.append((key, label, tag, sz, mode, md5, tmd5, len(data), stats))
        print('  -> %s  md5=%s  table md5=%s' % (raw_path, md5, tmd5))

    write_crlf(os.path.join(OUTROOT, 'restore_white.bat'), [
        '@echo off',
        'rem Restore the current WHITE atlas + original table (undo ASCII test install)',
        'setlocal',
        'set GAME=D:\\SteamLibrary\\steamapps\\common\\Obscure',
        'set WHT=%GAME%\\_obscure_backup\\colorfix_white_2026-10-25',
        'if not exist "%WHT%\\big_atlas_c.raw.white" echo [FAIL] white backup missing & exit /b 1',
        'copy /Y "%WHT%\\big_atlas_c.raw.white" "%GAME%\\big_atlas_c.raw" >nul',
        'copy /Y "%WHT%\\table_c.bin.orig" "%GAME%\\table_c.bin" >nul',
        'if errorlevel 1 echo [FAIL] copy failed & exit /b 1',
        'certutil -hashfile "%GAME%\\big_atlas_c.raw" MD5 | findstr /I "664ad2164508d8e7d4a53c27a3dc7195" >nul',
        'if errorlevel 1 echo [WARN] atlas md5 mismatch after restore & exit /b 1',
        'certutil -hashfile "%GAME%\\table_c.bin" MD5 | findstr /I "9cf562f53b48b1be130aa0453f8607b3" >nul',
        'if errorlevel 1 echo [WARN] table md5 mismatch after restore & exit /b 1',
        'echo [OK] white atlas + table restored. Restart game.',
        'exit /b 0',
    ])

    png = make_comparison(variant_imgs, tmaps, os.path.join(OUTROOT, 'ascii_unify_compare.png'))
    print('\ncomparison image: %s' % png)

    with open(os.path.join(OUTROOT, 'MD5清单.txt'), 'w', encoding='utf-8') as f:
        f.write('ascii unify test 2026-10-25  (live env untouched)\n')
        f.write('live white atlas md5: %s\n' % live_md5)
        f.write('live table md5: %s\n\n' % live_tmd5)
        for key, label, tag, sz, mode, md5, tmd5, size, stats in manifest:
            f.write('%s  %s (%s%s%s)\n' % (
                key, label, tag,
                '' if sz is None else ', %dpx' % sz,
                '' if mode is None else ', %s' % ('flush-left' if mode == 'flush' else 'cjk-style')))
            f.write('  atlas md5=%s\n  table md5=%s%s\n  size=%d  regions=%d  %s\n'
                    % (md5, tmd5,
                       ' (identical to live)' if tmd5 == live_tmd5 else '',
                       size, stats['regions'], stats.get('sizes', '')))
        f.write('\nrestore: ..\\restore_white.bat (atlas+table, md5 verified)\n')
        f.write('note: 24-row sampling band caps unified-baseline Latin at 20px '
                '(24px DuanHei Latin spans 28 ink rows and cannot fit).\n')
    print('manifest: %s' % os.path.join(OUTROOT, 'MD5清单.txt'))
    print('\nDONE. Live environment NOT modified.')


if __name__ == '__main__':
    main()
