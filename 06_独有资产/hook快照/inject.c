/* 32 位 DLL 注入器: inject.exe <pid> <dllpath> */
#include <windows.h>
#include <stdio.h>

typedef HMODULE (WINAPI *LPFN_LoadLibraryW)(LPCWSTR);

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) { wprintf(L"usage: inject.exe <pid> <dll>\n"); return 2; }
    DWORD pid = (DWORD)_wtoi(argv[1]);
    HANDLE h = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
                           PROCESS_VM_OPERATION | PROCESS_VM_WRITE, FALSE, pid);
    if (!h) { wprintf(L"OpenProcess failed %lu\n", GetLastError()); return 1; }
    size_t n = (wcslen(argv[2]) + 1) * sizeof(wchar_t);
    LPVOID mem = VirtualAllocEx(h, NULL, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!mem) { wprintf(L"VirtualAllocEx failed %lu\n", GetLastError()); CloseHandle(h); return 1; }
    SIZE_T done = 0;
    WriteProcessMemory(h, mem, argv[2], n, &done);
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    LPFN_LoadLibraryW ll = (LPFN_LoadLibraryW)GetProcAddress(k32, "LoadLibraryW");
    HANDLE t = CreateRemoteThread(h, NULL, 0, (LPTHREAD_START_ROUTINE)ll, mem, 0, NULL);
    if (!t) { wprintf(L"CreateRemoteThread failed %lu\n", GetLastError()); }
    else { WaitForSingleObject(t, 5000); wprintf(L"injected, remote module = %p\n", (void*)GetExitCodeThread(t, NULL)); }
    CloseHandle(t); VirtualFreeEx(h, mem, 0, MEM_RELEASE); CloseHandle(h);
    return 0;
}
