/* binkw32 转发包装 + D3D9 探测 hook
 * 14 个导出用普通 stdcall 包装, 参数个数按 @N 装饰名给出, 参数类型不重要(原样透传)
 */
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>

static HMODULE g_real = NULL;
static void* g_f[14];
static int g_init = 0;

static const char* g_names[14] = {
    "_BinkOpen@8","_BinkSetSoundTrack@8","_BinkSetSoundSystem@8","_BinkSetSoundOnOff@8",
    "_BinkGoto@12","_BinkGetKeyFrame@12","_BinkCopyToBuffer@28","_BinkClose@4",
    "_BinkOpenDirectSound@4","_BinkWait@4","_BinkDoFrame@4","_BinkNextFrame@4",
    "_BinkSetVolume@12","_BinkPause@8"
};

static void init_real(void) {
    if (g_init) return;
    g_init = 1;
    g_real = LoadLibraryW(L"binkw32_o.dll");
    if (g_real) for (int i = 0; i < 14; i++) g_f[i] = (void*)GetProcAddress(g_real, g_names[i]);
}

#define WRAP1(name, idx)                               \
    __declspec(dllexport) void* __stdcall name(void* a) {                    \
        init_real(); if(!g_f[idx]) return 0;            \
        return ((void*(__stdcall*)(void*))g_f[idx])(a); }
#define WRAP2(name, idx)                                \
    __declspec(dllexport) void* __stdcall name(void* a, void* b) {            \
        init_real(); if(!g_f[idx]) return 0;            \
        return ((void*(__stdcall*)(void*,void*))g_f[idx])(a,b); }
#define WRAP3(name, idx)                                \
    __declspec(dllexport) void* __stdcall name(void* a, void* b, void* c) {   \
        init_real(); if(!g_f[idx]) return 0;            \
        return ((void*(__stdcall*)(void*,void*,void*))g_f[idx])(a,b,c); }
#define WRAP7(name)                                     \
    __declspec(dllexport) void* __stdcall name(void* a,void* b,void* c,void* d,void* e,void* f,void* g){ \
        init_real(); if(!g_f[6]) return 0;              \
        return ((void*(__stdcall*)(void*,void*,void*,void*,void*,void*,void*))g_f[6])(a,b,c,d,e,f,g); }

/* 索引与 g_names 对应 */
WRAP2(_BinkOpen, 0)            /* 2 args */
WRAP2(_BinkSetSoundTrack, 1)
WRAP2(_BinkSetSoundSystem, 2)
WRAP2(_BinkSetSoundOnOff, 3)
WRAP3(_BinkGoto, 4)
WRAP3(_BinkGetKeyFrame, 5)
WRAP7(_BinkCopyToBuffer)       /* 7 args -> index 6 */
WRAP1(_BinkClose, 7)              /* 1 arg  -> WRAP1 固定用 g_f[0] */
WRAP1(_BinkOpenDirectSound, 8)
WRAP1(_BinkWait, 9)
WRAP1(_BinkDoFrame, 10)
WRAP1(_BinkNextFrame, 11)
WRAP3(_BinkSetVolume, 12)
WRAP2(_BinkPause, 13)

/* ---- D3D9 探测 hook ---- */
static FILE* g_log = NULL;
static void LOG(const char* fmt, ...) {
    if (!g_log) fopen_s(&g_log, "E:\\SteamLibrary\\steamapps\\common\\Obscure\\hzp_tools\\out\\d3dhook.log", "a");
    if (!g_log) return;
    char b[2048]; va_list ap; va_start(ap, fmt);
    _vsnprintf_s(b, sizeof(b), _TRUNCATE, fmt, ap); va_end(ap);
    fputs(b, g_log); fflush(g_log);
}

void* WINAPI Hook_Create9(unsigned int sdk);
static void PatchD3D(void) {
    HMODULE exe = GetModuleHandleW(NULL);
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)exe;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)((BYTE*)exe + dos->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)((BYTE*)exe + rva);
    for (; imp->Name; imp++) {
        const char* dll = (const char*)((BYTE*)exe + imp->Name);
        if (_stricmp(dll, "d3d9.dll") != 0) continue;
        IMAGE_THUNK_DATA* names = (IMAGE_THUNK_DATA*)((BYTE*)exe + (imp->OriginalFirstThunk ? imp->OriginalFirstThunk : imp->FirstThunk));
        IMAGE_THUNK_DATA* iat = (IMAGE_THUNK_DATA*)((BYTE*)exe + imp->FirstThunk);
        for (; names->u1.Function; names++, iat++) {
            if (names->u1.Function & 0x80000000) continue;
            const char* nm = (const char*)((BYTE*)exe + names->u1.Function + 2);
            if (strcmp(nm, "Direct3DCreate9") == 0) {
                LOG("[*] D3D IAT patched %p -> %p\n", (void*)iat->u1.Function, (void*)Hook_Create9);
                iat->u1.Function = (DWORD)Hook_Create9;
                return;
            }
        }
    }
    LOG("[!] d3d9 IAT not found\n");
}

typedef HRESULT (WINAPI *PFN_Reset)(void*, void*);


static void* g_realReset = NULL;
static int g_dumped = 0;

static HRESULT WINAPI Hook_Reset(void* dev, void* ppp) {
    if (!g_dumped) {
        g_dumped = 1;
        void** vt = *(void***)dev;
        LOG("[*] device=%p vtable=%p\n", dev, vt);
        HMODULE d3d = GetModuleHandleW(L"d3d9.dll");
        LOG("[*] d3d9.dll base=%p\n", d3d);
        for (int i = 0; i < 100; i++) {
            void* f = vt[i];
            unsigned long base = (unsigned long)(ULONG_PTR)d3d;
            int inDll = ((ULONG_PTR)f >= base) && ((ULONG_PTR)f < base + 0x200000);
            LOG("vt[%02d] = %p %s\n", i, f, inDll ? "(d3d9)" : "");
        }
    }
    return ((PFN_Reset)g_realReset)(dev, ppp);
}

typedef void* (WINAPI *PFN_D3DCreate9)(unsigned int);
void* WINAPI Hook_Create9(unsigned int sdk) {
    HMODULE d3d = GetModuleHandleW(L"d3d9.dll");
    PFN_D3DCreate9 orig = (PFN_D3DCreate9)GetProcAddress(d3d, "Direct3DCreate9");
    LOG("[*] Direct3DCreate9(%u)\n", sdk);
    void* d3d9 = orig(sdk);
    if (d3d9) {
        void** vt9 = *(void***)d3d9;
        g_realReset = vt9[16];
        vt9[16] = Hook_Reset;
        LOG("[*] IDirect3D9=%p  Reset hooked (vtable[16])\n", d3d9);
    }
    return d3d9;
}


BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        init_real();
        LOG("\n==== binkhook attach pid=%lu ====\n", (unsigned long)GetCurrentProcessId());
        PatchD3D();
    }
    return TRUE;
}
