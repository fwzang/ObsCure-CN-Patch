# -*- coding: utf-8 -*-
"""可靠的手柄/键盘注入：DirectInput 读扫描码状态，必须用 KEYEVENTF_SCANCODE。"""
import ctypes, time
from ctypes import wintypes

u = ctypes.windll.user32
k = ctypes.WinDLL('kernel32', use_last_error=True)

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

# (名称, VK, ScanCode)
KEYS = {
    'ENTER': (0x0D, 0x1C), 'SPACE': (0x20, 0x39), 'ESC': (0x1B, 0x01),
    'BACKSPACE': (0x08, 0x0E), 'TAB': (0x09, 0x0F),
    'W': (0x57, 0x11), 'A': (0x41, 0x1E), 'S': (0x53, 0x1F), 'D': (0x44, 0x20),
    'Q': (0x51, 0x10), 'E': (0x45, 0x12), 'R': (0x52, 0x13), 'F': (0x46, 0x21),
    'P': (0x50, 0x19), 'Z': (0x5A, 0x2C), 'X': (0x58, 0x2D), 'C': (0x43, 0x2E),
    'UP': (0x26, 0x48), 'DOWN': (0x28, 0x50), 'LEFT': (0x25, 0x4B), 'RIGHT': (0x27, 0x4D),
    'LSHIFT': (0xA0, 0x2A), 'RSHIFT': (0xA1, 0x36), 'RCTRL': (0xA3, 0x1D),
    '1': (0x31, 0x02), '2': (0x32, 0x03), '4': (0x34, 0x05), '6': (0x36, 0x07), '8': (0x38, 0x09),
}

P = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


class _U(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', ctypes.c_ulong), ('u', _U)]


def find_game():
    wins = []
    def cb(h, l):
        b = ctypes.create_unicode_buffer(256); u.GetWindowTextW(h, b, 256)
        if 'Obscure' in b.value and u.IsWindowVisible(h):
            wins.append(h)
        return True
    u.EnumWindows(P(cb), 0)
    return wins


def focus():
    w = find_game()
    if not w:
        return None, None
    h = w[0]
    u.SetForegroundWindow(h)
    u.SetFocus(h)
    time.sleep(0.35)
    fg = u.GetForegroundWindow()
    return h, fg


def key(name, hold=0.09, gap=0.30):
    vk, sc = KEYS[name.upper()]
    for down in (1, 0):
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if not down else 0)
        ki = KEYBDINPUT(vk, sc, flags, 0, None)
        u.SendInput(1, ctypes.byref(INPUT(INPUT_KEYBOARD, _U(ki=ki))), ctypes.sizeof(INPUT))
        if down:
            time.sleep(hold)
    time.sleep(gap)


def combo(names, hold=0.09, gap=0.35):
    """按住前几个键, 敲最后一个, 再释放"""
    for n in names[:-1]:
        vk, sc = KEYS[n.upper()]
        ki = KEYBDINPUT(vk, sc, KEYEVENTF_SCANCODE, 0, None)
        u.SendInput(1, ctypes.byref(INPUT(INPUT_KEYBOARD, _U(ki=ki))), ctypes.sizeof(INPUT))
        time.sleep(0.05)
    key(names[-1], hold=hold, gap=0.05)
    for n in reversed(names[:-1]):
        vk, sc = KEYS[n.upper()]
        ki = KEYBDINPUT(vk, sc, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)
        u.SendInput(1, ctypes.byref(INPUT(INPUT_KEYBOARD, _U(ki=ki))), ctypes.sizeof(INPUT))
    time.sleep(gap)


def click(x, y, hold=0.10, delay=1.2):
    w = find_game()
    if not w:
        return
    h = w[0]
    u.SetForegroundWindow(h); time.sleep(0.3)
    r = wintypes.RECT(); u.GetWindowRect(h, ctypes.byref(r))
    ax, ay = r.left + x, r.top + y
    cur = wintypes.POINT(); u.GetCursorPos(ctypes.byref(cur))
    for i in range(1, 5):
        u.SetCursorPos(cur.x + (ax-cur.x)*i//4, cur.y + (ay-cur.y)*i//4)
        time.sleep(0.04)
    for down in (1, 0):
        mi = MOUSEINPUT(0, 0, 0, (0x0002 if down else 0x0004), 0, None)
        u.SendInput(1, ctypes.byref(INPUT(0, _U(mi=mi))), ctypes.sizeof(INPUT))
        if down:
            time.sleep(hold)
    time.sleep(delay)


def shot(tag, outdir=r'E:\SteamLibrary\steamapps\common\Obscure\hzp_tools\out\shots'):
    import os
    from PIL import ImageGrab
    w = find_game()
    if not w:
        print('no window'); return None
    h = w[0]
    u.SetForegroundWindow(h); time.sleep(0.4)
    r = wintypes.RECT(); u.GetWindowRect(h, ctypes.byref(r))
    im = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    os.makedirs(outdir, exist_ok=True)
    im.save(os.path.join(outdir, tag + '.png'))
    return sum(1 for v in im.convert('L').getdata() if v > 100)
