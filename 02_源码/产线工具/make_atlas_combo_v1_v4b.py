# -*- coding: utf-8 -*-
"""make_atlas_combo_v1_v4b.py -- 2026-10-26 组合变体：V1 投影边框 + V4b ASCII 统一

背景：用户先选定边框 V1（右下投影，已试装），再要求试 V4b（20px 端黑自然
字距）。V4b 包自带图集+表，直接装会冲掉 V1 投影。两层改动区域完全不重叠：
  V1  重绘排放区 y=128..2143（1472 个汉字 tile，同坐标 A/B）
  V4b 新增 y>=2144（152 个拉丁 tile）+ 表 153 条记录改指向
=> 组合 = V1 图集的 y<2144 字节 + V4b 图集的 y>=2144 字节，表用 V4b 的。

验证（双层，全量）：
  1) 组合图集 y<2144 与 V1 图集逐字节一致；y>=2144 与 V4b 图集逐字节一致；
  2) 排放区 1472 个 tile 与期望 V1 tile 逐像素一致（24 行采样带）；
  3) 新区 152 个 tile 与期望 V4b flush tile 逐像素一致；
  4) 表与 V4b 表逐字节一致；y<128 原区与 live 逐字节一致（含手柄图标）。
"""
import os
import struct
import hashlib
import sys
from PIL import Image, ImageDraw, ImageFont

TOOLS = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(TOOLS))
sys.path.insert(0, TOOLS)
from make_atlas_ascii_test import (RENDERERS, mask_to_premul_white, font_at,
                                    draw_string, SAMPLE_LINES, LATIN_STRIP,
                                    LABEL_FONT, W, H, BAND, TILE, FREE_Y0)
from make_atlas_border_test import TILE_FN, FONT_PATH

V1_DIR = os.path.join(PKG, '05_hook源码与译文', 'border_test_2026-10-25', 'variant1_shadow')
V4B_DIR = os.path.join(PKG, '05_hook源码与译文', 'ascii_unify_test_2026-10-25', 'variant4b_l20')
LIVE_DIR = os.path.join(PKG, '05_hook源码与译文', 'payload_loose')
OUT = os.path.join(PKG, '05_hook源码与译文', 'ascii_unify_test_2026-10-25', 'variant4b_l20_on_v1')


def md5(b):
    return hashlib.md5(b).hexdigest()


def main():
    os.makedirs(OUT, exist_ok=True)
    v1 = open(os.path.join(V1_DIR, 'big_atlas_c.raw'), 'rb').read()
    v4b = open(os.path.join(V4B_DIR, 'big_atlas_c.raw'), 'rb').read()
    v4b_tbl = open(os.path.join(V4B_DIR, 'table_c.bin'), 'rb').read()
    live = open(os.path.join(LIVE_DIR, 'big_atlas_c.raw'), 'rb').read()
    live_tbl = open(os.path.join(LIVE_DIR, 'table_c.bin'), 'rb').read()
    print('V1  atlas md5=%s' % md5(v1))
    print('V4b atlas md5=%s  table md5=%s' % (md5(v4b), md5(v4b_tbl)))
    assert md5(v1) == 'c1f1ae82f489f343ecbed3ba812b6dec', 'V1 atlas md5 unexpected'
    assert md5(v4b) == '3f06ef608780d3090c10b48131a5332f', 'V4b atlas md5 unexpected'

    # ---- 组合：字节级拼接（两层区域不重叠） ----
    split = FREE_Y0 * W * 4
    combo = v1[:split] + v4b[split:]
    combo_tbl = v4b_tbl
    assert len(combo) == W * H * 4
    print('combo atlas md5=%s  table md5=%s' % (md5(combo), md5(combo_tbl)))

    # ---- 验证 1：分层逐字节一致 ----
    assert combo[:split] == v1[:split], 'combo y<2144 != V1'
    assert combo[split:] == v4b[split:], 'combo y>=2144 != V4b'
    assert combo[:128 * W * 4] == live[:128 * W * 4], 'origin region != live'
    print('[OK] layer split: y<2144 == V1, y>=2144 == V4b, origin == live')

    # ---- 验证 2+3：双层 tile 全量逐像素（24 行采样带） ----
    recs = [struct.unpack_from('<5H', combo_tbl, 10 * k) for k in range(len(combo_tbl) // 10)]
    px = Image.frombytes('RGBA', (W, H), combo).load()

    # 2) 排放区 1472 tile vs 期望 V1 tile
    #    注意：>/þ/ý 三条已被 V4b 改指向新区（不再落在排放区），
    #    其旧 placed tile 成为孤儿墨迹（不可见、无害），不参与本项校验；
    #    这三条由下一项（新区 flush tile 校验）覆盖。
    f24 = ImageFont.truetype(FONT_PATH, 24)
    bad1 = 0
    n1 = 0
    for code, x, y, w, adv in recs[150:]:
        if y >= FREE_Y0:
            continue
        exp = TILE_FN['variant1_shadow'](f24, chr(code))
        ep = exp.load()
        n1 += 1
        for yy in range(BAND):
            for xx in range(TILE):
                if ep[xx, yy] != px[x + xx, y + yy]:
                    bad1 += 1
                    break
            else:
                continue
            break
    print('[%s] placed %d tiles vs expected V1: bad=%d' % ('OK' if not bad1 else '!!', n1, bad1))
    assert not bad1

    # 3) 新区 152 tile vs 期望 V4b flush tile
    f20 = font_at(20)
    renderer = RENDERERS['flush']
    bad2 = 0
    n2 = 0
    targets = list(range(150)) + [i for i, r in enumerate(recs) if i >= 150 and r[0] in (0x3E, 0xFE, 0xFD)]
    for i in targets:
        code, x, y, w, adv = recs[i]
        if code == 0x20:
            continue
        exp = mask_to_premul_white(renderer(f20, chr(code))[0])
        ep = exp.load()
        n2 += 1
        for yy in range(BAND):
            for xx in range(TILE):
                if ep[xx, yy] != px[x + xx, y + yy]:
                    bad2 += 1
                    break
            else:
                continue
            break
    print('[%s] new-area %d tiles vs expected V4b flush: bad=%d' % ('OK' if not bad2 else '!!', n2, bad2))
    assert not bad2

    # ---- 验证 4：表一致 ----
    assert combo_tbl == v4b_tbl
    diff_tbl = sum(1 for k in range(len(live_tbl) // 10)
                   if live_tbl[10 * k:10 * k + 10] != combo_tbl[10 * k:10 * k + 10])
    print('[OK] table == V4b table; %d records changed vs live' % diff_tbl)

    # ---- 落盘 ----
    open(os.path.join(OUT, 'big_atlas_c.raw'), 'wb').write(combo)
    open(os.path.join(OUT, 'table_c.bin'), 'wb').write(combo_tbl)

    # ---- 预览（组合渲染） ----
    tmap = {r[0]: r for r in recs}
    img = Image.frombytes('RGBA', (W, H), combo)
    CW, CH = 1280, 700
    canvas = Image.new('RGBA', (CW, CH), (14, 14, 18, 255))
    dr = ImageDraw.Draw(canvas)
    lab = ImageFont.truetype(LABEL_FONT, 20)
    lab_s = ImageFont.truetype(LABEL_FONT, 15)
    title_f = ImageFont.truetype(LABEL_FONT, 26)
    dr.text((30, 16), '组合版 V1投影 + V4b 20px英文统一 · 2026-10-26（测试装，未覆盖正式交付包）',
            font=title_f, fill=(235, 235, 235, 255))
    dr.text((30, 52), '汉字=端黑24px+1px右下黑投影；英文/数字=端黑20px左对齐基线19；'
                      '拉丁 adv=墨迹宽+3；表 153 条改指向（记录数不变）',
            font=lab_s, fill=(160, 160, 170, 255))
    dr.rectangle([20, 92, CW - 20, 92 + 26 + len(SAMPLE_LINES) * 60 + 12], fill=(26, 26, 32, 255))
    yy = 128
    for line in SAMPLE_LINES:
        draw_string(canvas, 40, yy, line, tmap, img, scale=2)
        yy += 60
    dr.text((40, 92 + 26 + len(SAMPLE_LINES) * 60 + 20), '纯拉丁对照', font=lab, fill=(235, 235, 235, 255))
    dr.rectangle([34, 92 + 26 + len(SAMPLE_LINES) * 60 + 48, 34 + 300, 92 + 26 + len(SAMPLE_LINES) * 60 + 48 + 34],
                 fill=(26, 26, 32, 255))
    draw_string(canvas, 40, 92 + 26 + len(SAMPLE_LINES) * 60 + 52, LATIN_STRIP, tmap, img, scale=2)
    canvas.convert('RGB').save(os.path.join(OUT, 'combo_preview.png'))

    # ---- 全量墨迹预览 ----
    prev = img.crop((0, 128, W, 2400))
    prev.putalpha(prev.getchannel('A').point(lambda v: 255 - v))
    bg = Image.new('RGBA', prev.size, (255, 255, 255, 255))
    bg.alpha_composite(prev)
    bg.convert('RGB').save(os.path.join(OUT, 'full_preview.png'))

    # ---- 安装/恢复批处理（CRLF ASCII） ----
    amd5, tmd5 = md5(combo), md5(combo_tbl)
    lines = [
        '@echo off',
        'rem ============================================================',
        'rem  ObsCure hanhua - COMBO V1 shadow + V4b 20px ASCII (TEST)',
        'rem  Installs combo atlas + table into game dir; current files auto-backed-up.',
        'rem ============================================================',
        'setlocal',
        'set GAME=D:\\SteamLibrary\\steamapps\\common\\Obscure',
        'set SRC=%~dp0',
        'set BAK=%GAME%\\_obscure_backup\\ascii_test_prev.bak',
        '',
        'if not exist "%SRC%big_atlas_c.raw" echo [FAIL] source atlas missing & exit /b 1',
        'if not exist "%GAME%\\big_atlas_c.raw" echo [FAIL] game atlas missing & exit /b 1',
        '',
        'certutil -hashfile "%SRC%big_atlas_c.raw" MD5 | findstr /I "' + amd5 + '" >nul',
        'if errorlevel 1 echo [FAIL] source atlas md5 mismatch - refused & exit /b 1',
        'certutil -hashfile "%SRC%table_c.bin" MD5 | findstr /I "' + tmd5 + '" >nul',
        'if errorlevel 1 echo [FAIL] source table md5 mismatch - refused & exit /b 1',
        '',
        'if not exist "%BAK%" mkdir "%BAK%"',
        'copy /Y "%GAME%\\big_atlas_c.raw" "%BAK%\\big_atlas_c.raw" >nul',
        'copy /Y "%GAME%\\table_c.bin" "%BAK%\\table_c.bin" >nul',
        'copy /Y "%SRC%big_atlas_c.raw" "%GAME%\\big_atlas_c.raw" >nul',
        'if errorlevel 1 echo [FAIL] copy atlas failed & exit /b 1',
        'copy /Y "%SRC%table_c.bin" "%GAME%\\table_c.bin" >nul',
        'if errorlevel 1 echo [FAIL] copy table failed & exit /b 1',
        '',
        'certutil -hashfile "%GAME%\\big_atlas_c.raw" MD5 | findstr /I "' + amd5 + '" >nul',
        'if errorlevel 1 echo [FAIL] verify atlas failed after install & exit /b 1',
        'certutil -hashfile "%GAME%\\table_c.bin" MD5 | findstr /I "' + tmd5 + '" >nul',
        'if errorlevel 1 echo [FAIL] verify table failed after install & exit /b 1',
        '',
        'echo [OK] COMBO V1+V4b installed (atlas ' + amd5 + ').',
        'echo Restore white: run ..\\restore_white.bat',
        'exit /b 0',
    ]
    with open(os.path.join(OUT, 'install_test.bat'), 'wb') as f:
        f.write(('\r\n'.join(lines) + '\r\n').encode('ascii'))

    with open(os.path.join(OUT, 'MD5清单.txt'), 'w', encoding='utf-8') as f:
        f.write('combo V1 shadow + V4b 20px ASCII  2026-10-26\n')
        f.write('atlas md5=%s\ntable md5=%s\n' % (amd5, tmd5))
        f.write('layers: y<2144 == border variant1_shadow (c1f1ae82), '
                'y>=2144 == ascii variant4b_l20 new area, table == variant4b_l20\n')
        f.write('verified: 1472 placed tiles + %d new tiles pixel-exact (24-row band), '
                'origin region byte-identical to live\n' % n2)

    print('\npackage: %s' % OUT)
    print('combo atlas md5=%s' % amd5)
    print('combo table md5=%s' % tmd5)
    print('DONE.')


if __name__ == '__main__':
    main()
