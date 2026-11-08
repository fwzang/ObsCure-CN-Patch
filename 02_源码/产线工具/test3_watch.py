# -*- coding: utf-8 -*-
"""
#3 字体切换竞态 · 四条路径记录器（RegisterHotKey 修正版）
====================================================================
★ 关键修正（这版才是对的）：
  1. 打标记用 **RegisterHotKey 全局热键**，在游戏里直接按，不需要切到控制台
     （旧版 input()/msvcrt 都要控制台焦点 -> 一按游戏掉焦点 -> 可能自己制造乱码）
  2. 四条路径各有独立热键，不用再输入 1-4：
        F9  = P1 进入新场景
        F10 = P2 返回暂停页
        F11 = P3 返回主菜单
        F12 = P4 离开房间再进入
     停止：控制台里按 ESC，或创建 test3_watch_stop.flag

★ 判据修正（这版才是对的）：
  分析目标是对比「正常切换」与「乱码切换」的
       [SWAP#] 增量   +   [RESET] 时序
  不要用「[3B] 是否重触发」当判据 —— [3B] 本来就不会重触发，那个判据永远落空。

输出:
  hzp_tools/out/test3_watch/watch3.log      时间线（标记 + 亮值突变 + hook 事件）
  hzp_tools/out/test3_watch/shots/*.png     标记/突变时刻截图

用法:
  python test3_watch.py              # 一直跑
  python test3_watch.py --minutes 40
"""
import os, sys, time, ctypes, re, argparse
from ctypes import wintypes as wt

BASE    = r'E:/SteamLibrary/steamapps/common/Obscure/hzp_tools'
OUT     = os.path.join(BASE, 'out', 'test3_watch')
SHOTS   = os.path.join(OUT, 'shots')
LOGF    = os.path.join(OUT, 'watch3.log')
HOOKLOG = os.path.join(BASE, 'out', 'x_hook.log')
STOPF   = os.path.join(BASE, 'out', 'test3_watch_stop.flag')
os.makedirs(SHOTS, exist_ok=True)

u   = ctypes.windll.user32
k32 = ctypes.windll.kernel32

# ---- 四条路径热键 ----
# (主键, 备选键1, 备选键2) —— 主键被占用时自动回退，保证每条路径都能打标记
_CAND = {
    1: ((0x78, 'F9'),  (0x77, 'F8'),  (0x76, 'F7')),
    2: ((0x79, 'F10'), (0x75, 'F6'),  (0x74, 'F5')),
    3: ((0x7A, 'F11'), (0x73, 'F4'),  (0x72, 'F3')),
    4: ((0x7B, 'F12'), (0x71, 'F2'),  (0x70, 'F1')),
}
_DESC = {1: 'P1 进入新场景', 2: 'P2 返回暂停页',
         3: 'P3 返回主菜单', 4: 'P4 离开房间再进入'}
PATHS = {}          # hotkey_id -> (vk, kname, desc)，启动时填充

MOD_NOMOD = 0x0000
WM_HOTKEY = 0x0312
VK_ESCAPE = 0x1B

# ---- 只看这三类 hook 事件（SWAP# 增量 / RESET 时序）----
HOOK_PAT = re.compile(r'\[(RESET[^\]]*|SWAP#?\d*|FONT-CONFIRM[^\]]*)\]')

from PIL import ImageGrab


def now():
    return time.strftime('%H:%M:%S')


def log(msg):
    line = '[%s] %s' % (now(), msg)
    print(line, flush=True)
    with open(LOGF, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def biggest_obscure():
    """返回 (hwnd, rect) — 最大可见 Obscure 窗口"""
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    best = [None, 0, None]

    def cb(h, l):
        b = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, b, 256)
        if 'Obscure' in b.value and u.IsWindowVisible(h):
            r = wt.RECT(); u.GetWindowRect(h, ctypes.byref(r))
            a = (r.right - r.left) * (r.bottom - r.top)
            if a > best[1]:
                best[0], best[1], best[2] = h, a, (r.left, r.top, r.right, r.bottom)
        return True
    u.EnumWindows(EnumProc(cb), 0)
    return best[0], best[2]


def bright(rect):
    try:
        im = ImageGrab.grab(bbox=rect).convert('L')
        h = im.histogram()
        return sum(h[40:])
    except Exception:
        return None


def hook_tail(pos):
    """读 x_hook.log 从 pos 起的新增事件（只留 RESET / SWAP# / FONT-CONFIRM）"""
    try:
        sz = os.path.getsize(HOOKLOG)
    except OSError:
        return '', pos
    if sz < pos:
        pos = 0
    if sz == pos:
        return '', pos
    with open(HOOKLOG, 'rb') as f:
        f.seek(pos)
        data = f.read(min(sz - pos, 4 * 1024 * 1024))
    try:
        txt = data.decode('utf-8', 'replace')
    except Exception:
        txt = data.decode('latin1', 'replace')
    evs = [l.strip()[:160] for l in txt.splitlines() if HOOK_PAT.search(l)]
    return '\n'.join(evs), sz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=int, default=0)
    args = ap.parse_args()

    for pid, cands in sorted(_CAND.items()):
        got = None
        for vk, kname in cands:
            if u.RegisterHotKey(None, pid, MOD_NOMOD, vk):
                got = (vk, kname); break
        if got:
            PATHS[pid] = (got[0], got[1], _DESC[pid])
            log('  热键就绪: %-4s = %s' % (got[1], _DESC[pid]))
        else:
            log('  !! 路径%d 所有候选键都被占用，该路径无法标记' % pid)

    log('=' * 66)
    log('#3 四条路径记录器启动（RegisterHotKey 版 — 游戏里直接按，不用切窗口）')
    log('  停止：控制台按 ESC  或  创建 test3_watch_stop.flag')
    log('  判据：[SWAP#] 增量 + [RESET] 时序（不是 [3B]）')
    log('=' * 66)

    last_bright = None
    last_sample = 0
    last_hook   = 0
    hook_pos    = 0
    t0          = time.time()
    shot_n      = 0
    msg         = wt.MSG()

    while True:
        # 非阻塞取 WM_HOTKEY（RegisterHotKey 投递到本线程，游戏不失焦）
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):   # PM_REMOVE
            if msg.message == WM_HOTKEY and msg.wParam in PATHS:
                vk, kname, desc = PATHS[msg.wParam]
                hwnd, rect = biggest_obscure()
                shot_n += 1
                f = os.path.join(SHOTS, 'mark_%03d.png' % shot_n)
                try:
                    if rect:
                        ImageGrab.grab(bbox=rect).save(f)
                except Exception:
                    pass
                b = bright(rect) if rect else None
                log('★标记 %s [%s]  bright=%s  截图=%s'
                    % (kname, desc, b, os.path.basename(f)))

        time.sleep(0.2)

        if args.minutes and (time.time() - t0) > args.minutes * 60:
            log('--minutes 到期'); break
        if os.path.exists(STOPF):
            log('stop flag 检测到'); break
        if (u.GetAsyncKeyState(VK_ESCAPE) & 0x0001) and \
           u.GetForegroundWindow() == k32.GetConsoleWindow():
            log('控制台 ESC'); break

        # 每 2 秒采样亮值
        if time.time() - last_sample < 2:
            continue
        last_sample = time.time()

        hwnd, rect = biggest_obscure()
        if not hwnd:
            continue
        b = bright(rect)
        if b is None:
            continue

        if last_bright is not None and abs(b - last_bright) > max(25000, last_bright * 0.25):
            shot_n += 1
            f = os.path.join(SHOTS, 'spike_%03d.png' % shot_n)
            try:
                ImageGrab.grab(bbox=rect).save(f)
            except Exception:
                pass
            log('亮值突变 %d -> %d  截图 %s' % (last_bright, b, os.path.basename(f)))
        last_bright = b

        # hook 事件增量（每 8 秒）
        if time.time() - last_hook >= 8:
            last_hook = time.time()
            evs, hook_pos = hook_tail(hook_pos)
            if evs:
                log('--- hook 事件 ---\n' + evs)

    for pid in PATHS:
        u.UnregisterHotKey(None, pid)
    log('记录器退出')


if __name__ == '__main__':
    main()
