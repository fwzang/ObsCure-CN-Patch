/* launchinject.exe <exe> <dll> : 以挂起方式启动目标进程, 注入 DLL, 再恢复 */
#include <windows.h>
#include <stdio.h>

typedef HMODULE (WINAPI *PFN_LoadLibraryW)(LPCWSTR);

static int inject(DWORD pid, const wchar_t* dll) {
    HANDLE h = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
                           PROCESS_VM_OPERATION | PROCESS_VM_WRITE, FALSE, pid);
    if (!h) { wprintf(L"OpenProcess %lu\n", GetLastError()); return 1; }
    size_t n = (wcslen(dll) + 1) * sizeof(wchar_t);
    LPVOID mem = VirtualAllocEx(h, NULL, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!mem) { wprintf(L"VirtualAllocEx %lu\n", GetLastError()); CloseHandle(h); return 1; }
    SIZE_T done = 0;
    WriteProcessMemory(h, mem, dll, n, &done);
    LPTHREAD_START_ROUTINE ll = (LPTHREAD_START_ROUTINE)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
    HANDLE t = CreateRemoteThread(h, NULL, 0, ll, mem, 0, NULL);
    if (!t) { wprintf(L"CreateRemoteThread %lu\n", GetLastError()); }
    else {
        WaitForSingleObject(t, 5000);
        DWORD code = 0; GetExitCodeThread(t, &code);
        wprintf(L"injected, remote module=%p\n", (void*)code);
        CloseHandle(t);
    }
    VirtualFreeEx(h, mem, 0, MEM_RELEASE);
    CloseHandle(h);
    return 0;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) { wprintf(L"usage: launchinject.exe <exe> <dll>\n"); return 2; }
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    wchar_t cmd[MAX_PATH * 2];
    swprintf(cmd, MAX_PATH * 2, L"\"%s\"", argv[1]);
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_SUSPENDED, NULL, NULL, &si, &pi)) {
        wprintf(L"CreateProcess failed %lu\n", GetLastError()); return 1;
    }
    wprintf(L"launched pid=%lu (suspended)\n", pi.dwProcessId);
    inject(pi.dwProcessId, argv[2]);
    ResumeThread(pi.hThread);
    wprintf(L"resumed\n");
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return 0;
}
