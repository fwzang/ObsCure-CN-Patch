# -*- coding: utf-8 -*-
"""高速轮询注入器：游戏会自重启，必须盯住**真正渲染的那个** Obscure.exe 进程。
用法: fastinject.py <dll路径> [超时秒]
策略: 每 40ms 扫进程列表, 找出新出现的 Obscure.exe, 立刻注入。"""
import ctypes, ctypes.wintypes as w, time, sys, os

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
TH32CS_SNAPPROCESS = 2

TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10
MAX_PATH = 260
MAX_MODULE_NAME32 = 255


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', w.LONG),
                ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * MAX_PATH)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('th32ModuleID', w.DWORD), ('th32ProcessID', w.DWORD),
                ('GlblcntUsage', w.DWORD), ('ProccntUsage', w.DWORD),
                ('modBaseAddr', ctypes.POINTER(ctypes.c_ubyte)), ('modBaseSize', w.DWORD),
                ('hModule', w.HMODULE), ('szModule', w.WCHAR * MAX_PATH),
                ('szExePath', w.WCHAR * MAX_PATH)]


k32.CreateToolhelp32Snapshot.restype = w.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
k32.Process32FirstW.restype = w.BOOL
k32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.restype = w.BOOL
k32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Module32FirstW.restype = w.BOOL
k32.Module32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
k32.Module32NextW.restype = w.BOOL
k32.Module32NextW.argtypes = [w.HANDLE, ctypes.POINTER(MODULEENTRY32W)]

k32.OpenProcess.restype = w.HANDLE
k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD, w.DWORD]
k32.WriteProcessMemory.restype = w.BOOL
k32.WriteProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.CreateRemoteThread.restype = w.HANDLE
k32.CreateRemoteThread.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.c_void_p, ctypes.c_void_p, w.DWORD,
                                   ctypes.POINTER(w.DWORD)]
k32.GetProcAddress.restype = ctypes.c_void_p
k32.GetProcAddress.argtypes = [w.HMODULE, ctypes.c_char_p]
k32.GetModuleHandleW.restype = w.HMODULE
k32.GetModuleHandleW.argtypes = [w.LPCWSTR]
k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k32.CloseHandle.argtypes = [w.HANDLE]
k32.VirtualFreeEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD]
k32.VirtualFreeEx.restype = w.BOOL

PROCESS_ALL = 0x1F0FFF


def find_procs(name):
    h = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out = []
    if h == -1 or h == 0xFFFFFFFFFFFFFFFF:
        return out
    pe = PROCESSENTRY32W(); pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    if k32.Process32FirstW(h, ctypes.byref(pe)):
        while True:
            if pe.szExeFile.lower() == name.lower():
                out.append((pe.th32ProcessID, pe.th32ParentProcessID))
            if not k32.Process32NextW(h, ctypes.byref(pe)):
                break
    k32.CloseHandle(h)
    return out


def has_module(pid, mod):
    h = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if h == -1 or h == 0xFFFFFFFFFFFFFFFF:
        return False
    me = MODULEENTRY32W(); me.dwSize = ctypes.sizeof(MODULEENTRY32W)
    found = False
    if k32.Module32FirstW(h, ctypes.byref(me)):
        while True:
            if me.szModule.lower() == mod.lower():
                found = True; break
            if not k32.Module32NextW(h, ctypes.byref(me)):
                break
    k32.CloseHandle(h)
    return found


INJECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hook', 'inject.exe')

def inject(pid, dll):
    """必须用 32 位 inject.exe：64 位 python 取到的 kernel32 地址在 WOW64 目标进程里是错的"""
    import subprocess
    try:
        r = subprocess.run([INJECTOR, str(pid), dll], capture_output=True, text=True, timeout=20)
        return True, (r.stdout or '').strip()[:120]
    except Exception as e:
        return False, str(e)
    h = k32.OpenProcess(PROCESS_ALL, False, pid)
    if not h:
        return False, 'OpenProcess'
    n = (len(dll) + 1) * 2
    mem = k32.VirtualAllocEx(h, None, n, 0x3000, 0x04)   # MEM_COMMIT|RESERVE, PAGE_READWRITE
    if not mem:
        k32.CloseHandle(h); return False, 'VirtualAllocEx'
    buf = ctypes.create_unicode_buffer(dll)
    done = ctypes.c_size_t(0)
    k32.WriteProcessMemory(h, ctypes.c_void_p(mem), ctypes.byref(buf), n, ctypes.byref(done))
    _ll = k32.GetProcAddress(k32.GetModuleHandleW('kernel32.dll'), b'LoadLibraryW')
    ll = ctypes.c_void_p(_ll if _ll else 0)
    t = k32.CreateRemoteThread(h, None, 0, ll, ctypes.c_void_p(mem), 0, None)
    if not t:
        k32.CloseHandle(h); return False, 'CreateRemoteThread'
    k32.WaitForSingleObject(t, 5000)
    code = w.DWORD(0)
    k32.GetExitCodeThread(t, ctypes.byref(code)) if hasattr(k32, 'GetExitCodeThread') else None
    k32.CloseHandle(t); k32.VirtualFreeEx(h, ctypes.c_void_p(mem), 0, 0x8000)
    k32.CloseHandle(h)
    return True, hex(code.value)


def main():
    dll = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    dll = os.path.abspath(dll)
    print('watching for Obscure.exe -> inject', dll)
    seen = set()
    t0 = time.time()
    while time.time() - t0 < timeout:
        for pid, par in find_procs('Obscure.exe'):
            if pid in seen:
                continue
            # 进程刚创建时加载器锁被占, LoadLibraryW 会失败 -> 延迟并重试
            ok, info = False, ''
            for attempt in range(6):
                time.sleep(0.35)
                ok, info = inject(pid, dll)
                if ok and info != '0x0':
                    break
            seen.add(pid)
            print(f'  +{time.time()-t0:5.1f}s pid={pid} parent={par} 注入={ok} {info}')
        time.sleep(0.04)
    print('done, injected pids =', seen)


if __name__ == '__main__':
    main()
