#include <windows.h>
#include <stdio.h>
#include <stdarg.h>

typedef HRESULT (WINAPI *PFN_Reset)(void*, void*);

static FILE* g_log = NULL;
static void LOG(const char* fmt, ...) {
    if (!g_log) fopen_s(&g_log, "E:\\SteamLibrary\\steamapps\\common\\Obscure\\hzp_tools\\out\\d3dhook.log", "a");
    if (!g_log) return;
    char buf[2048];
    va_list ap; va_start(ap, fmt);
    _vsnprintf_s(buf, sizeof(buf), _TRUNCATE, fmt, ap);
    va_end(ap);
    fputs(buf, g_log); fflush(g_log);
}

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


/* ---- 入口 ---- */
static void PatchIAT(void) {
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
                LOG("[*] IAT patched: Direct3DCreate9 %p -> %p\n", (void*)iat->u1.Function, (void*)Hook_Create9);
                iat->u1.Function = (DWORD)Hook_Create9;
                return;
            }
        }
    }
    LOG("[!] d3d9!Direct3DCreate9 not found in IAT\n");
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        LOG("\n==== attach pid=%lu ====\n", (unsigned long)GetCurrentProcessId());
        PatchIAT();
    }
    return TRUE;
}
