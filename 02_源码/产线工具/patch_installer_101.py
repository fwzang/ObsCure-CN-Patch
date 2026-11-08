#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
patch_installer_101.py  ·  2026-10-26
把 O1SETUP2 安装器内嵌的 4 个改装件更新到当前保底版，并产出 v1.01。

格式（实测确认，非假设）
  footer = "O1SETUP2" + ver(u32) + n(u32) + n × record，footer 总长 8080 字节
  record = name[64] + off(u64) + size(u64) + crc(u32)      stride = 84
  crc = zlib.crc32(blob)     off = blob 在 exe 内的文件偏移
  ver = 2 是【格式版本】，不是显示版本（exe 内无 "1.00" 显示字符串）

策略
  · 复制副本再改（原 v1.00 保留）
  · 只在原 blob 尺寸完全一致时就地替换（size 校验），否则拒绝
  · 替换后同步更新该条 crc 字段，并对全部 41/61 条做 crc 复核
"""
import struct, zlib, hashlib, os, shutil, sys, time

GAME = r'D:\SteamLibrary\steamapps\common\Obscure'
PKG = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18\01_安装包'

TARGETS = {
    'bin/version.dll': 'version.dll',
    'bin/big_atlas_c.raw': 'big_atlas_c.raw',
    'bin/table_c.bin': 'table_c.bin',
    'bin/dinput8.dll': 'dinput8.dll',
}
EXPECT = {
    'bin/version.dll': '0c17dfc07b666255b04652dbf0cbe541',
    'bin/big_atlas_c.raw': '99a1244494b42d9cb9572e92335720ff',
    'bin/table_c.bin': '6a498e9d8478b48ff9b3dc0e29f5c183',
    'bin/dinput8.dll': 'b8c51891352e3e7bfc2c30f2c903b46c',
}
FOOTER_LEN = 8080
STRIDE = 84


def parse_footer(path):
    total = os.path.getsize(path)
    with open(path, 'rb') as f:
        f.seek(total - FOOTER_LEN)
        tail = f.read()
    assert tail[:8] == b'O1SETUP2', 'footer magic not found at -8080'
    ver, n = struct.unpack_from('<II', tail, 8)
    ents = {}
    for i in range(n):
        o = 16 + i * STRIDE
        nm = tail[o:o + 64].split(b'\x00')[0].decode('latin1')
        off, size, crc = struct.unpack_from('<QQI', tail, o + 64)
        ents[nm] = {'i': i, 'rec': o, 'off': off, 'size': size, 'crc': crc}
    return total, ver, n, ents


def verify_all(path, label):
    total, ver, n, ents = parse_footer(path)
    with open(path, 'rb') as f:
        bad = []
        for nm, e in ents.items():
            f.seek(e['off'])
            blob = f.read(e['size'])
            if len(blob) != e['size']:
                bad.append((nm, 'short read'))
                continue
            c = zlib.crc32(blob) & 0xFFFFFFFF
            if c != e['crc']:
                bad.append((nm, 'crc 0x%08X != 0x%08X' % (c, e['crc'])))
    print('  [%s] 全量 crc 复核：%d 条，异常 %d 条 %s'
          % (label, n, len(bad), bad[:3] if bad else ''))
    return not bad


def patch(src, dst):
    print('=== %s ===' % os.path.basename(dst))
    t0 = time.time()
    if not os.path.exists(dst):
        print('  复制中... (%.2f GB)' % (os.path.getsize(src) / 1e9))
        shutil.copy2(src, dst)
    print('  复制/就绪耗时 %.1fs' % (time.time() - t0))

    total, ver, n, ents = parse_footer(dst)
    print('  条目数 %d, ver(格式)=%d, size=%d' % (n, ver, total))

    payload = {}
    for blob_name, fn in TARGETS.items():
        p = os.path.join(GAME, fn)
        data = open(p, 'rb').read()
        md5 = hashlib.md5(data).hexdigest()
        assert md5 == EXPECT[blob_name], '%s md5 不符: %s' % (fn, md5)
        payload[blob_name] = data

    # ★★ 硬闸：version.dll 必须是【切屏黑屏遮罩】版本
    #    file 0x37954 两字节：73 24 = jae(遮罩开) / EB 24 = jmp(遮罩关)
    #    这条断言防止把任何「关掉遮罩」的实验件打进安装器。
    vdll = payload['bin/version.dll']
    gate = vdll[0x37954:0x37956]
    assert gate == bytes.fromhex('7324'), \
        'version.dll 的遮罩门控是 %s，不是 73 24 —— 拒绝把「遮罩关」的版本打进安装器' % gate.hex(' ').upper()
    print('  遮罩门控 @0x37954 = 73 24（遮罩开）✔')

    changed = []
    with open(dst, 'r+b') as f:
        for blob_name, data in payload.items():
            e = ents[blob_name]
            f.seek(e['off'])
            old = f.read(e['size'])
            if old == data:
                print('  %-22s 已是最新，跳过' % blob_name)
                continue
            assert len(data) == e['size'], \
                '%s 尺寸变化(%d vs %d)，拒绝就地替换' % (blob_name, len(data), e['size'])
            f.seek(e['off'])
            f.write(data)
            new_crc = zlib.crc32(data) & 0xFFFFFFFF
            crc_pos = (total - FOOTER_LEN) + e['rec'] + 64 + 16
            f.seek(crc_pos)
            f.write(struct.pack('<I', new_crc))
            changed.append(blob_name)
            print('  %-22s 已替换  size=%d  crc 0x%08X -> 0x%08X'
                  % (blob_name, e['size'], e['crc'], new_crc))

    # 复核：目标条目的 blob md5 + 全量 crc
    total2, ver2, n2, ents2 = parse_footer(dst)
    assert (total2, n2) == (total, n) and ver2 == ver
    ok = True
    with open(dst, 'rb') as f:
        for blob_name, data in payload.items():
            e = ents2[blob_name]
            f.seek(e['off'])
            got = f.read(e['size'])
            same = hashlib.md5(got).hexdigest() == hashlib.md5(data).hexdigest()
            print('  %-22s 复核 md5 %s' % (blob_name, 'OK' if same else 'FAIL'))
            ok &= same
    ok &= verify_all(dst, 'after')
    print('  文件大小 %d（应与原一致）' % total2)
    print('  结果：%s  changed=%s' % ('通过' if ok else '失败', changed))
    return ok


def main():
    # v1.00 源件在 _历史版本/，产物写到 精简版/ 加强版/
    hist = os.path.join(PKG, '_历史版本')
    pairs = [
        (os.path.join(hist, 'ObsCure汉化安装器_v1.00.exe'),
         os.path.join(PKG, '精简版', 'ObsCure汉化安装器_v1.01.exe')),
        (os.path.join(hist, 'ObsCure加强汉化版安装器_v1.00.exe'),
         os.path.join(PKG, '加强版', 'ObsCure加强汉化版安装器_v1.01.exe')),
    ]
    allok = True
    for p, q in pairs:
        if not os.path.exists(p):
            print('缺少源文件:', p); allok = False; continue
        allok &= patch(p, q)
        print()
    print('总体：', '全部通过' if allok else '存在失败')
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
