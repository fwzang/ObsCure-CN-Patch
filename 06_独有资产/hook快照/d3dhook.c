/* D3D9 字体纹理 Hook —— 探测版
 * 目标: 拦截 Direct3DCreate9, patch device vtable,
#include <stdarg.h>
 *       记录 512x128 字体纹理的创建与上传方式(CreateTexture/UpdateTexture/LockRect)。
 */
#include <windows.h>
#include <stdio.h>
#include <d3d9.h>

static FILE* g_log = NULL;
static void LOG(const char* fmt, ...) {
    if (!g_log) {
        g_log = NULL;
        fopen_s(&g_log, "E:\\SteamLibrary\\steamapps\\common\\Obscure\\hzp_tools\\out\\d3dhook.log", "a");
        if (!g_log) return;
    }
    char buf[1024];
    va_list ap; va_start(ap, fmt);
    int n = _vsnprintf_s(buf, sizeof(buf), _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n > 0) { fputs(buf, g_log); fflush(g_log); }
}

/* ---------------- vtable hooks ---------------- */
typedef HRESULT (WINAPI *PFN_CreateTexture)(IDirect3DDevice9*, UINT, UINT, UINT, D3DUSAGE, D3DFORMAT, D3DPOOL, IDirect3DTexture9**);
typedef HRESULT (WINAPI *PFN_UpdateTexture)(IDirect3DDevice9*, IDirect3DResource*, IDirect3DResource* const*);
typedef HRESULT (WINAPI *PFN_LockRect)(IDirect3DDevice9*, IDirect3DResource*, D3DLOCKED_RECT*, const D3DRECT*);

static PFN_CreateTexture   Real_CreateTexture   = NULL;
static PFN_UpdateTexture   Real_UpdateTexture   = NULL;
static PFN_LockRect        Real_LockRect        = NULL;
static IDirect3DDevice9*   g_dev = NULL;
static LPDIRECT3DTEXTURE9  g_fontTex = NULL;
static int                g_texCount = 0;

static HRESULT WINAPI Hook_CreateTexture(IDirect3DDevice9* dev, UINT W, UINT H, UINT Levels,
                                         D3DUSAGE Usage, D3DFORMAT Fmt, D3DPOOL Pool,
                                         IDirect3DTexture9** out) {
    if (W == 512 && H == 128) {
        LOG("[CreateTexture] %ux%u levels=%u usage=%08X fmt=%d pool=%d  <== 疑似字体纹理\n",
            W, H, Levels, Usage, (int)Fmt, (int)Pool);
    } else if (g_texCount < 40) {
        LOG("[CreateTexture] %ux%u levels=%u usage=%08X fmt=%d pool=%d\n",
            W, H, Levels, Usage, (int)Fmt, (int)Pool);
    }
    g_texCount++;
    return Real_CreateTexture(dev, W, H, Levels, Usage, Fmt, Pool, out);
}

static HRESULT WINAPI Hook_UpdateTexture(IDirect3DDevice9* dev, IDirect3DResource* dst,
                                         IDirect3DResource* const* src) {
    if (dst == (IDirect3DResource*)g_fontTex) {
        LOG("[UpdateTexture] 命中字体纹理! src=%p\n", src ? *src : NULL);
    }
    return Real_UpdateTexture(dev, dst, src);
}

static HRESULT WINAPI Hook_LockRect(IDirect3DDevice9* dev, IDirect3DResource* res,
                                    D3DLOCKED_RECT* pRect, const D3DRECT* pRct) {
    if (res == (IDirect3DResource*)g_fontTex) {
        LOG("[LockRect] 命中字体纹理!\n");
    }
    return Real_LockRect(dev, res, pRect, pRct);
}

static void PatchVTable(IDirect3DDevice9* dev) {
    void** vt = *(void***)dev;
    /* D3D9 vtable 序号: CreateTexture=19, UpdateTexture=36, LockRect=42 */
    LOG("[*] device vtable=%p dev=%p\n", vt, dev);
    Real_CreateTexture = (PFN_CreateTexture)vt[19]; vt[19] = Hook_CreateTexture;
    Real_UpdateTexture = (PFN_UpdateTexture)vt[36]; vt[36] = Hook_UpdateTexture;
    Real_LockRect      = (PFN_LockRect)vt[42];      vt[42] = Hook_LockRect;
    g_dev = dev;
    LOG("[*] vtable patched\n");
}

typedef IDirect3D9* (WINAPI *PFN_Direct3DCreate9)(UINT);
static IDirect3D9* WINAPI Hook_Create9(UINT SDK) {
    HMODULE d3d = GetModuleHandleW(L"d3d9.dll");
    PFN_Direct3DCreate9 orig = (PFN_Direct3DCreate9)GetProcAddress(d3d, "Direct3DCreate9");
    LOG("[*] Direct3DCreate9(%u) called\n", SDK);
    IDirect3D9* d3d9 = orig(SDK);
    if (d3d9) {
        typedef HRESULT (WINAPI *PFN_Reset)(IDirect3D9*, D3DPRESENT_PARAMETERS*);
        /* 无法直接 hook Reset(虚表在 IDirect3D9 上), 改为 hook d3d9->Reset 虚表 */
        void** vt9 = *(void***)d3d9;
        /* Reset = 17 */
        static PFN_Reset realReset = NULL;
        realReset = (PFN_Reset)vt9[17];
        (void)realReset;
        LOG("[*] IDirect3D9=%p vtable=%p\n", d3d9, vt9);
    }
    return d3d9;
}

/* IAT patch: 替换主模块导入的 Direct3DCreate9 */
static void PatchIAT(void) {
    HMODULE exe = GetModuleHandleW(NULL);
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)exe;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)((BYTE*)exe + dos->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)((BYTE*)exe + rva);
    for (; imp->Name; imp++) {
        const char* dll = (const char*)((BYTE*)exe + imp->Name);
        if (_stricmp(dll, "d3d9.dll") != 0) continue;
        IMAGE_THUNK_DATA* t = (IMAGE_THUNK_DATA*)((BYTE*)exe + imp->FirstThunk);
        for (; t->u1.Function; t++) {
            IMAGE_IMPORT_BY_NAME* ibn = (IMAGE_IMPORT_BY_NAME*)t->u1.Function;
            if (strcmp((const char*)ibn->Name, "Direct3DCreate9") == 0) {
                LOG("[*] IAT patched at %p (was %p)\n", (void*)&t->u1.Function, (void*)t->u1.Function);
                t->u1.Function = (DWORD)Hook_Create9;
                return;
            }
        }
    }
    LOG("[!] d3d9.dll / Direct3DCreate9 not found in IAT\n");
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        LOG("\n==== DLL attached pid=%lu ====\n", (unsigned long)GetCurrentProcessId());
        PatchIAT();
    }
    return TRUE;
}
