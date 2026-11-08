# -*- coding: utf-8 -*-
"""c93b D2 触发验证：启动 → 稳定 → ShowWindow 最小化/恢复诱发 Reset → 等 60s → 截图。
全程零按键注入（用 ShowWindow，非键盘）。"""
import subprocess, time, sys, os, shutil
BASE = r'E:\SteamLibrary\steamapps\common\Obscure'
sys.path.insert(0, os.path.join(BASE, 'hzp_tools'))
import ginput
OUT = os.path.join(BASE, 'hzp_tools', 'out', 'accept_c93')
LOG = os.path.join(BASE, 'hzp_tools', 'out', 'x_hook.log')

import ctypes
from ctypes import wintypes
SW_MINIMIZE, SW_RESTORE = 6, 9

def kill():
    subprocess.run(['taskkill', '/IM', 'Obscure.exe', '/F'], capture_output=True)
    time.sleep(4)

kill()
for rnd in range(1, 6):
    if os.path.exists(LOG):
        shutil.move(LOG, os.path.join(OUT, 'd2_%02d_x_hook.log' % (rnd - 1)))
    subprocess.Popen([os.path.join(BASE, 'Obscure.exe')], cwd=BASE)
    hwnd = None
    for i in range(60):
        w = ginput.find_game()
        if w: hwnd = w[0]; break
        time.sleep(1)
    time.sleep(25)  # 稳定期
    # 诱发设备重置：最小化 -> 恢复（×3 轮换）
    for cyc in range(3):
        ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
        time.sleep(4)
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(4)
    time.sleep(60)  # 60s 空等给 D2 / 看门狗机会
    alive = ginput.find_game() is not None
    shot_p = os.path.join(OUT, 'd2_%02d.png' % rnd)
    try:
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom)).save(shot_p)
    except Exception as e:
        shot_p = 'FAIL:%s' % e
    kill()
    txt = ''
    if os.path.exists(LOG):
        txt = open(LOG, encoding='utf-8', errors='surrogateescape').read()
        shutil.move(LOG, os.path.join(OUT, 'd2_%02d_x_hook.log' % rnd))
    feats = {k: txt.count(k) for k in ['[D2]', 'Reset 返回', 'RESETFAIL', '开闸', 'WD-B0']}
    print('d2round %d alive=%s feats=%s shot=%s' % (rnd, alive, feats, shot_p), flush=True)
print('=== D2 VERIFY DONE ===')
