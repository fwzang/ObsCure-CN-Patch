/* d3dfont.c — 阶段一：inline hook d3d9.dll 的 SetTexture/CreateTexture（纯观察）
 *
 * vtable 偏移（已通过 32 位独立程序 d3dvtbl.exe 确认）：
 *   CreateTexture = d3d9_base + 0x000E4F80  (vt[22])
 *   SetTexture    = d3d9_base + 0x0013CB30  (vt[61])
 *
 * 不需要创建 dummy device、不需要经过 Steam wrapper。
 */
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>

#define LOGF "E:\\SteamLibrary\\steamapps\\common\\Obscure\\hzp_tools\\out\\fontlog.log"

/* d3d9.dll 内偏移（由 d3dvtbl.exe 确认） */
#define OFF_CREATETEXTURE 0x000E4F80
#define OFF_SETTEXTURE    0x0013CB30

#define RETADDR() __builtin_return_address(0)

static void LOG(const char* fmt, ...) {
    char buf[2048];
    va_list ap; va_start(ap, fmt);
    _vsnprintf_s(buf, sizeof(buf), _TRUNCATE, fmt, ap);
    va_end(ap);
    FILE* f = NULL;
    if (fopen_s(&f, LOGF, "a")) return;
    fputs(buf, f);
    fclose(f);
}

/* ---- inline hook 基础设施 ---- */
static void* g_orig_SetTex    = NULL;  /* trampoline 地址 */
static void* g_orig_CreateTex = NULL;
static int   g_hooked = 0;

static int write_jmp(void* target, void* hook) {
    DWORD old;
    if (!VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old)) return 0;
    unsigned char* p = (unsigned char*)target;
    long rel = (long)((char*)hook - ((char*)target + 5));
    p[0] = 0xE9;
    p[1] = (unsigned char)rel;
    p[2] = (unsigned char)(rel >> 8);
    p[3] = (unsigned char)(rel >> 16);
    p[4] = (unsigned char)(rel >> 24);
    VirtualProtect(target, 5, old, &old);
    return 1;
}

static void* make_trampoline(void* target, int copy_bytes) {
    void* mem = VirtualAlloc(NULL, 32, 0x3000 /*COMMIT|RESERVE*/, 0x40 /*EXEC_RW*/);
    if (!mem) return NULL;
    DWORD old;
    VirtualProtect(mem, 32, PAGE_EXECUTE_READWRITE, &old);
    /* 复制原始字节 */
    for (int i = 0; i < copy_bytes; i++)
        ((unsigned char*)mem)[i] = ((unsigned char*)target)[i];
    /* 追加 jmp 回 target+copy_bytes */
    long back = (long)((char*)target + copy_bytes - ((char*)mem + copy_bytes + 5));
    unsigned char* t = (unsigned char*)mem;
    t[copy_bytes + 0] = 0xE9;
    t[copy_bytes + 1] = (unsigned char)back;
    t[copy_bytes + 2] = (unsigned char)(back >> 8);
    t[copy_bytes + 3] = (unsigned char)(back >> 16);
    t[copy_bytes + 4] = (unsigned char)(back >> 24);
    return mem;
}

/* ---- hook 函数 ---- */
typedef long (WINAPI *PFN_SetTexture)(void*, unsigned long, void*);
typedef long (WINAPI *PFN_CreateTexture)(void*, unsigned long, unsigned long, unsigned long,
                                         unsigned long, unsigned long, unsigned long, void**);
typedef long (WINAPI *PFN_GetLevelDesc)(void*, unsigned long, void*);

typedef struct { unsigned long Fmt; long W, H; } SURF_DESC;

static long WINAPI hk_SetTexture(void* dev, unsigned long stage, void* tex) {
    static int count = 0;
    count++;
    if (tex && count <= 30) {
        void** vt = *(void***)tex;
        if (vt && vt[3]) {
            SURF_DESC d; ZeroMemory(&d, sizeof(d));
            PFN_GetLevelDesc f = (PFN_GetLevelDesc)vt[3];
            if (f(tex, 0, &d) >= 0)
                LOG("[SetTex] dev=%p stage=%lu tex=%p %lux%lu fmt=%lu caller=%p\n",
                    dev, stage, tex, d.W, d.H, d.Fmt, RETADDR());
        }
    }
    typedef long (WINAPI *FN)(void*, unsigned long, void*);
    return ((FN)g_orig_SetTex)(dev, stage, tex);
}

static long WINAPI hk_CreateTexture(void* dev, unsigned long W, unsigned long H,
                                    unsigned long Levels, unsigned long Usage,
                                    unsigned long Fmt, unsigned long Pool, void** out) {
    static int count = 0;
    count++;
    HRESULT hr = ((PFN_CreateTexture)g_orig_CreateTex)(dev, W, H, Levels, Usage, Fmt, Pool, out);
    if (W == 512 && H == 128)
        LOG("[FONT-Create] %lux%lu lv=%lu use=%lux fmt=%lux pool=%lux -> %p hr=%08lX caller=%p\n",
            W, H, Levels, Usage, Fmt, Pool, out ? *out : NULL, (unsigned long)hr, RETADDR());
    return hr;
}

/* ---- worker ---- */
static DWORD WINAPI worker(LPVOID unused) {
    (void)unused;
    Sleep(8000);   /* 等游戏完全加载 */

    HMODULE hD3D9 = GetModuleHandleW(L"d3d9.dll");
    if (!hD3D9) { LOG("[!] d3d9.dll not loaded\n"); return 1; }
    unsigned long base = (unsigned long)hD3D9;
    LOG("[*] d3d9.dll base = 0x%08lX\n", base);

    void* pSetTex    = (void*)(base + OFF_SETTEXTURE);
    void* pCreateTex = (void*)(base + OFF_CREATETEXTURE);
    LOG("[*] SetTexture    = %p (base+0x%08X)\n", pSetTex, OFF_SETTEXTURE);
    LOG("[*] CreateTexture = %p (base+0x%08X)\n", pCreateTex, OFF_CREATETEXTURE);

    /* 验证地址在 d3d9.dll 代码段内（前几个字节应该是可 hot-patch 的函数序言） */
    unsigned char* st = (unsigned char*)pSetTex;
    LOG("[*] SetTexture 前 8 字节: %02X %02X %02X %02X %02X %02X %02X %02X\n",
        st[0], st[1], st[2], st[3], st[4], st[5], st[6], st[7]);

    /* 安装 hook */
    g_orig_SetTex = make_trampoline(pSetTex, 7);
    if (!g_orig_SetTex) { LOG("[!] make_trampoline SetTex failed\n"); return 1; }
    if (!write_jmp(pSetTex, (void*)hk_SetTexture)) { LOG("[!] write_jmp SetTex failed\n"); return 1; }

    g_orig_CreateTex = make_trampoline(pCreateTex, 7);
    if (!g_orig_CreateTex) { LOG("[!] make_trampoline CreateTex failed\n"); return 1; }
    if (!write_jmp(pCreateTex, (void*)hk_CreateTexture)) { LOG("[!] write_jmp CreateTex failed\n"); return 1; }

    LOG("[*] hooks installed! 等待纹理调用...\n");

    /* 定期输出心跳 */
    for (int r = 0; r < 120; r++) {
        Sleep(5000);
        LOG("[tick %d] still alive\n", r);
    }
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    (void)h; (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        LOG("\n######## attach pid=%lu ########\n", (unsigned long)GetCurrentProcessId());
        HANDLE t = CreateThread(NULL, 0, worker, NULL, 0, NULL);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
