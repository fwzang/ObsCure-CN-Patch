#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_mask_state.py  ·  2026-10-26
校验「切屏黑屏遮罩」是否开启。可查：裸 DLL / O1SETUP2 安装器 exe / zip 包。

判据（唯一可信的机械判据）
  version.dll 的 file 0x37954 处两字节：
      73 24 = jae  -> 遮罩【开】（Clear 受门控，正常）
      EB 24 = jmp  -> 遮罩【关】（曾被改成无条件跳过 Clear）
  位置由 .text 映射 file = RVA - 0xC00 得到（RVA 0x38554）。

用法
  python verify_mask_state.py <文件...>
  文件可以是：version.dll / 任意安装器*.exe / 稳定保底版*.zip
  全部为「遮罩开」时退出码 0，任一项为「关」或无法判定时退出码 1。
"""
import sys, os, struct, hashlib, zipfile

MASK_FO = 0x37954
GATE_ON = bytes.fromhex('7324')
GATE_OFF = bytes.fromhex('eb24')
FOOTER_LEN, STRIDE = 8080, 84


def check_dll(data, label):
    md5 = hashlib.md5(data).hexdigest()
    gate = data[MASK_FO:MASK_FO + 2]
    if gate == GATE_ON:
        verdict, ok = '遮罩【开】', True
    elif gate == GATE_OFF:
        verdict, ok = '遮罩【关】', False
    else:
        verdict, ok = '异常字节 %s（无法判定）' % gate.hex(' ').upper(), False
    print('  %-34s md5=%s  gate=%-5s %s'
          % (label, md5, gate.hex(' ').upper(), verdict))
    return ok


def payload_from_installer(path):
    total = os.path.getsize(path)
    with open(path, 'rb') as f:
        f.seek(total - FOOTER_LEN)
        tail = f.read()
        if tail[:8] != b'O1SETUP2':
            return None
        ver, n = struct.unpack_from('<II', tail, 8)
        for i in range(n):
            o = 16 + i * STRIDE
            nm = tail[o:o + 64].split(b'\x00')[0].decode('latin1')
            if nm == 'bin/version.dll':
                off, size, crc = struct.unpack_from('<QQI', tail, o + 64)
                f.seek(off)
                return f.read(size)
    return None


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    allok = True
    for p in argv[1:]:
        if not os.path.exists(p):
            print('  [缺失] %s' % p)
            allok = False
            continue
        name = os.path.basename(p)
        if p.lower().endswith('.zip'):
            z = zipfile.ZipFile(p)
            if 'version.dll' not in z.namelist():
                print('  %-34s 无 version.dll' % name)
                allok = False
            else:
                allok &= check_dll(z.read('version.dll'), name + ' :version.dll')
            z.close()
        elif p.lower().endswith('.exe'):
            blob = payload_from_installer(p)
            if blob is None:
                print('  %-34s 不是 O1SETUP2 或未找到 bin/version.dll' % name)
                allok = False
            else:
                allok &= check_dll(blob, name + ' :bin/version.dll')
        else:
            allok &= check_dll(open(p, 'rb').read(), name)
    print()
    print('结论：', '全部为「遮罩开」，可以发布' if allok else '存在「遮罩关」或异常，禁止发布')
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
