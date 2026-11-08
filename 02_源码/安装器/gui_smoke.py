# -*- coding: utf-8 -*-
"""GUI 冒烟：真正启动打包好的 exe。
两阶段（避免用户漏看免责声明）：
  阶段1：正常启动 → 必须弹「免责声明」确认框 → 截图证明（然后退出）。
  阶段2：设置 OBSCURE_SKIP_DISCLAIMER=1 跳过弹窗 → 验证主界面正常渲染 → 截图。
两个安装器（精简 / 加强）都会跑。
"""
import os
import sys
import time
import subprocess
import ctypes

HERE = os.path.dirname(os.path.abspath(__file__))
LITE_EXE = os.path.join(HERE, "..", "dist", "ObsCure汉化安装器_v1.00.exe")
FULL_EXE = os.path.join(HERE, "..", "..", "installer_full_v100", "dist", "ObsCure加强汉化版安装器_v1.00.exe")


def find_window(substr):
    """返回第一个标题包含 substr 的可见窗口 hwnd，找不到返回 None。"""
    hwnd = [None]
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, _):
        if ctypes.windll.user32.IsWindowVisible(h):
            n = ctypes.create_unicode_buffer(300)
            ctypes.windll.user32.GetWindowTextW(h, n, 300)
            if substr in n.value:
                hwnd[0] = h
                return False
        return True

    ctypes.windll.user32.EnumWindows(CB(cb), 0)
    return hwnd[0]


def wait_window(substr, proc=None, timeout=40.0):
    """轮询等待标题含 substr 的可见窗口（应对 onefile 冷启动慢）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            return None            # 进程已退出
        h = find_window(substr)
        if h:
            return h
        time.sleep(0.5)
    return None


def shot(hwnd, path):
    try:
        import ctypes.wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        hdc = ctypes.windll.user32.GetWindowDC(hwnd)
        mdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
        ctypes.windll.gdi32.SelectObject(mdc, bmp)
        ctypes.windll.user32.PrintWindow(hwnd, mdc, 2)

        class BI(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]

        bi = BI()
        bi.biSize = ctypes.sizeof(BI)
        bi.biWidth, bi.biHeight = w, -h
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        ctypes.windll.gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        from PIL import Image
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
        img.save(path)
        ctypes.windll.gdi32.DeleteObject(bmp)
        ctypes.windll.gdi32.DeleteDC(mdc)
        ctypes.windll.user32.ReleaseDC(hwnd, hdc)
        return img.size
    except Exception as e:
        print("  PrintWindow 失败:", e)
        return None


def phase_disclaimer(exe, tag):
    print("[%s] 阶段1：验证免责声明弹窗出现（不设置逃生口）" % tag)
    p = subprocess.Popen([exe])
    h = wait_window("免责声明", p, timeout=45.0)
    print("  弹窗 hwnd:", hex(h) if h else None)
    if not h:
        p.terminate()
        print("  [FAIL] 未检测到免责声明弹窗（alive=%s）" % (p.poll() is None))
        return False
    sz = shot(h, os.path.join(os.path.dirname(exe), "%s_disclaimer.png" % tag))
    print("  弹窗截图:", sz)
    p.terminate()
    time.sleep(0.8)
    return True


def phase_main(exe, tag):
    print("[%s] 阶段2：逃生口跳过弹窗，验证主界面渲染" % tag)
    env = dict(os.environ)
    env["OBSCURE_SKIP_DISCLAIMER"] = "1"
    p = subprocess.Popen([exe], env=env)
    h = wait_window("汉化", p, timeout=45.0)
    print("  主窗口 hwnd:", hex(h) if h else None)
    if not h:
        p.terminate()
        print("  [FAIL] 未检测到主界面（alive=%s）" % (p.poll() is None))
        return False
    sz = shot(h, os.path.join(os.path.dirname(exe), "%s_gui_smoke.png" % tag))
    print("  主界面截图:", sz)
    p.terminate()
    time.sleep(0.8)
    return True


def run(exe, tag):
    if not os.path.exists(exe):
        print("[%s] 跳过：exe 不存在 %s" % (tag, exe))
        return True
    ok1 = phase_disclaimer(exe, tag)
    ok2 = phase_main(exe, tag)
    return ok1 and ok2


def main():
    fails = []
    if not run(LITE_EXE, "lite"):
        fails.append("lite")
    if not run(FULL_EXE, "full"):
        fails.append("full")
    if fails:
        print("GUI 冒烟失败:", fails)
        return 1
    print("GUI 冒烟全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
