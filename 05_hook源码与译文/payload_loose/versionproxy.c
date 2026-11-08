/* versionproxy.c — version.dll 透明代理（门槛③ 新注入向量试验）
 * 背景：本机实测「游戏目录存在任何 d3d9.dll（连系统原样副本都一样）必崩」，
 *       所以改用 version.dll 作为注入向量 —— 不碰 d3d9 加载路径。
 * 所有导出用 x86 尾跳转桩转发到系统 C:\Windows\SysWOW64\version.dll。
 * DllMain 里立即 resolve（IAT 在进程加载时就会绑定桩，不能懒解析）。
 */
#include <windows.h>

#define LOGF "D://SteamLibrary//steamapps//common//Obscure//hzp_tools//out//versionproxy.log"
#define REAL_DLL "C://Windows//SysWOW64//version.dll"

static void* p_GetFileVersionInfoA        __asm__("pGetFileVersionInfoA");
static void* p_GetFileVersionInfoByHandle __asm__("pGetFileVersionInfoByHandle");
static void* p_GetFileVersionInfoExA      __asm__("pGetFileVersionInfoExA");
static void* p_GetFileVersionInfoExW      __asm__("pGetFileVersionInfoExW");
static void* p_GetFileVersionInfoSizeA    __asm__("pGetFileVersionInfoSizeA");
static void* p_GetFileVersionInfoSizeExA  __asm__("pGetFileVersionInfoSizeExA");
static void* p_GetFileVersionInfoSizeExW  __asm__("pGetFileVersionInfoSizeExW");
static void* p_GetFileVersionInfoSizeW    __asm__("pGetFileVersionInfoSizeW");
static void* p_GetFileVersionInfoW        __asm__("pGetFileVersionInfoW");
static void* p_VerFindFileA               __asm__("pVerFindFileA");
static void* p_VerFindFileW               __asm__("pVerFindFileW");
static void* p_VerInstallFileA            __asm__("pVerInstallFileA");
static void* p_VerInstallFileW            __asm__("pVerInstallFileW");
static void* p_VerLanguageNameA           __asm__("pVerLanguageNameA");
static void* p_VerLanguageNameW           __asm__("pVerLanguageNameW");
static void* p_VerQueryValueA             __asm__("pVerQueryValueA");
static void* p_VerQueryValueW             __asm__("pVerQueryValueW");

__attribute__((naked)) void Stub_GetFileVersionInfoA(void)        { __asm__("jmp *pGetFileVersionInfoA"); }
__attribute__((naked)) void Stub_GetFileVersionInfoByHandle(void) { __asm__("jmp *pGetFileVersionInfoByHandle"); }
__attribute__((naked)) void Stub_GetFileVersionInfoExA(void)      { __asm__("jmp *pGetFileVersionInfoExA"); }
__attribute__((naked)) void Stub_GetFileVersionInfoExW(void)      { __asm__("jmp *pGetFileVersionInfoExW"); }
__attribute__((naked)) void Stub_GetFileVersionInfoSizeA(void)    { __asm__("jmp *pGetFileVersionInfoSizeA"); }
__attribute__((naked)) void Stub_GetFileVersionInfoSizeExA(void)  { __asm__("jmp *pGetFileVersionInfoSizeExA"); }
__attribute__((naked)) void Stub_GetFileVersionInfoSizeExW(void)  { __asm__("jmp *pGetFileVersionInfoSizeExW"); }
__attribute__((naked)) void Stub_GetFileVersionInfoSizeW(void)    { __asm__("jmp *pGetFileVersionInfoSizeW"); }
__attribute__((naked)) void Stub_GetFileVersionInfoW(void)        { __asm__("jmp *pGetFileVersionInfoW"); }
__attribute__((naked)) void Stub_VerFindFileA(void)               { __asm__("jmp *pVerFindFileA"); }
__attribute__((naked)) void Stub_VerFindFileW(void)               { __asm__("jmp *pVerFindFileW"); }
__attribute__((naked)) void Stub_VerInstallFileA(void)            { __asm__("jmp *pVerInstallFileA"); }
__attribute__((naked)) void Stub_VerInstallFileW(void)            { __asm__("jmp *pVerInstallFileW"); }
__attribute__((naked)) void Stub_VerLanguageNameA(void)           { __asm__("jmp *pVerLanguageNameA"); }
__attribute__((naked)) void Stub_VerLanguageNameW(void)           { __asm__("jmp *pVerLanguageNameW"); }
__attribute__((naked)) void Stub_VerQueryValueA(void)             { __asm__("jmp *pVerQueryValueA"); }
__attribute__((naked)) void Stub_VerQueryValueW(void)             { __asm__("jmp *pVerQueryValueW"); }

static HMODULE g_real = NULL;

static void LOG(const char* s) {
    HANDLE h = CreateFileA(LOGF, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD n = 0; WriteFile(h, s, lstrlenA(s), &n, NULL); CloseHandle(h);
}

static void resolve_all(void) {
    if (g_real) return;
    g_real = LoadLibraryA(REAL_DLL);
    if (!g_real) { LOG("[VERPROXY] FATAL: cannot load real version.dll\r\n"); return; }
    p_GetFileVersionInfoA        = (void*)GetProcAddress(g_real, "GetFileVersionInfoA");
    p_GetFileVersionInfoByHandle = (void*)GetProcAddress(g_real, "GetFileVersionInfoByHandle");
    p_GetFileVersionInfoExA      = (void*)GetProcAddress(g_real, "GetFileVersionInfoExA");
    p_GetFileVersionInfoExW      = (void*)GetProcAddress(g_real, "GetFileVersionInfoExW");
    p_GetFileVersionInfoSizeA    = (void*)GetProcAddress(g_real, "GetFileVersionInfoSizeA");
    p_GetFileVersionInfoSizeExA  = (void*)GetProcAddress(g_real, "GetFileVersionInfoSizeExA");
    p_GetFileVersionInfoSizeExW  = (void*)GetProcAddress(g_real, "GetFileVersionInfoSizeExW");
    p_GetFileVersionInfoSizeW    = (void*)GetProcAddress(g_real, "GetFileVersionInfoSizeW");
    p_GetFileVersionInfoW        = (void*)GetProcAddress(g_real, "GetFileVersionInfoW");
    p_VerFindFileA               = (void*)GetProcAddress(g_real, "VerFindFileA");
    p_VerFindFileW               = (void*)GetProcAddress(g_real, "VerFindFileW");
    p_VerInstallFileA            = (void*)GetProcAddress(g_real, "VerInstallFileA");
    p_VerInstallFileW            = (void*)GetProcAddress(g_real, "VerInstallFileW");
    p_VerLanguageNameA           = (void*)GetProcAddress(g_real, "VerLanguageNameA");
    p_VerLanguageNameW           = (void*)GetProcAddress(g_real, "VerLanguageNameW");
    p_VerQueryValueA             = (void*)GetProcAddress(g_real, "VerQueryValueA");
    p_VerQueryValueW             = (void*)GetProcAddress(g_real, "VerQueryValueW");
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    (void)h; (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        char b[256], t[32];
        DisableThreadLibraryCalls(h);
        b[0] = 0;
        lstrcatA(b, "\r\n#### VERPROXY LOADED pid=");
        wsprintfA(t, "%lu", (unsigned long)GetCurrentProcessId());
        lstrcatA(b, t); lstrcatA(b, "\r\n");
        LOG(b);
        resolve_all();
        int miss = 0;
        void** slots[] = { &p_GetFileVersionInfoA, &p_GetFileVersionInfoByHandle,
            &p_GetFileVersionInfoExA, &p_GetFileVersionInfoExW, &p_GetFileVersionInfoSizeA,
            &p_GetFileVersionInfoSizeExA, &p_GetFileVersionInfoSizeExW, &p_GetFileVersionInfoSizeW,
            &p_GetFileVersionInfoW, &p_VerFindFileA, &p_VerFindFileW, &p_VerInstallFileA,
            &p_VerInstallFileW, &p_VerLanguageNameA, &p_VerLanguageNameW,
            &p_VerQueryValueA, &p_VerQueryValueW };
        for (int i = 0; i < 17; i++) if (!*slots[i]) miss++;
        b[0] = 0;
        lstrcatA(b, "[VERPROXY] resolve done, 未解析="); wsprintfA(t, "%d", miss);
        lstrcatA(b, t); lstrcatA(b, "\r\n");
        LOG(b);
    }
    return TRUE;
}
