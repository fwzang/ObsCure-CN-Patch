/* d3dsettex.c — 阶段一：dummy device 取真实函数地址 + 观察 SetTexture
 * clang/mingw 交叉编译 32 位，不能用 __try/__except（MSVC SEH）
 */
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>

#define LOGF "E:\\SteamLibrary\\steamapps\\common\\Obscure\\hzp_tools\\out\\settex.log"
#define D3D_SDK_VERSION 32

/* ---- 最小 D3D 类型 ---- */
typedef void PDEV;
typedef void PTEX;
typedef unsigned long D3DFMT;
typedef unsigned long D3DUSAGE;
typedef unsigned long D3DPOOL;

/* D3DPRESENT_PARAMETERS 正确结构（56 字节，14 个字段） */
typedef struct {
    unsigned long BackBufferWidth;              /* 0  */
    unsigned long BackBufferHeight;             /* 4  */
    unsigned long BackBufferFormat;             /* 8  */
    unsigned long BackBufferCount;              /* 12 */
    unsigned long MultiSampleType;              /* 16 */
    unsigned long MultiSampleQuality;           /* 20 */
    unsigned long SwapEffect;                   /* 24 */
    void*         hDeviceWindow;                /* 28 ← 必须 */
    unsigned long Windowed;                     /* 32 */
    unsigned long EnableAutoDepthStencil;       /* 36 */
    unsigned long AutoDepthStencilFormat;       /* 40 */
    unsigned long Flags;                        /* 44 */
    unsigned long FullScreen_RefreshRateInHz;   /* 48 */
    unsigned long PresentationInterval;         /* 52 */
} D3DPP;  /* 56 字节 */

typedef struct { unsigned long Fmt; long W, H; } SURF_DESC;

/* ---- 日志 ---- */
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

/* ---- 全局 ---- */
static void* g_dev = NULL;

/* ---- worker：创建 dummy device，dump vtable，观察 SetTexture ---- */
static DWORD WINAPI worker(LPVOID unused) {
    (void)unused;
    Sleep(8000);   /* 等游戏加载完 695MB 数据包 + 主菜单出现 */

    LOG("[*] worker: 加载 d3d9.dll\n");
    HMODULE hD3D9 = GetModuleHandleW(L"d3d9.dll");
    if (!hD3D9) { LOG("[!] d3d9.dll not loaded\n"); return 1; }
    LOG("[*] d3d9.dll base=%p\n", hD3D9);

    typedef void* (WINAPI *PFN_D3DCreate9)(unsigned int);
    PFN_D3DCreate9 create9 = (PFN_D3DCreate9)GetProcAddress(hD3D9, "Direct3DCreate9");
    if (!create9) { LOG("[!] no Direct3DCreate9\n"); return 1; }
    void* d3d9 = create9(32);
    if (!d3d9) { LOG("[!] Direct3DCreate9 NULL\n"); return 1; }

    /* 隐藏窗口 */
    /* 用游戏自己的窗口（更可靠） */
    HWND hwnd = FindWindowW(NULL, L"Obscure");
    if (!hwnd) hwnd = GetForegroundWindow();
    LOG("[*] 游戏窗口 hwnd=%p\n", hwnd);
    if (!hwnd) { LOG("[!] 找不到游戏窗口\n"); return 1; }

    /* D3DPRESENT_PARAMETERS（窗口模式 dummy device） */
    D3DPP pp; ZeroMemory(&pp, sizeof(pp));
    pp.Windowed = 1;               /* 窗口模式 */
    pp.SwapEffect = 1;             /* D3DSWAPEFFECT_DISCARD */
    pp.BackBufferFormat = 21;      /* D3DFMT_A8R8G8B8 */
    pp.hDeviceWindow = (void*)hwnd;

    void** vt9 = *(void***)d3d9;
    /* IDirect3D9::CreateDevice = vtable[16]，D3D9 无 SoftwareVersion 参数（那是 D3D8 的） */
    typedef long (WINAPI *PFN_CreateDevice_fn)(
        void*,                                  /* this */
        unsigned long Adapter,                  /* 0 */
        unsigned long DeviceType,               /* 1 = HAL */
        void* hFocusWindow,                     /* hwnd */
        unsigned long BehaviorFlags,            /* 0x20 = SOFTWARE_VERTEXPROCESSING */
        void* pPresentationParameters,          /* &pp */
        void** ppDevice);                       /* &dev */
    PFN_CreateDevice_fn CreateDevice_fn = (PFN_CreateDevice_fn)vt9[16];
    LOG("[*] CreateDevice_fn = %p\n", (void*)CreateDevice_fn);
    /* dump IDirect3D9 vtable 前 20 项，标记是否在 d3d9.dll 范围内 */
    {
        unsigned long base = (unsigned long)hD3D9;
        unsigned long end = base + 1646592;
        LOG("[*] d3d9.dll range: %p - %p\n", (void*)base, (void*)end);
        for (int i = 0; i <= 20; i++) {
            void* fp = vt9[i];
            int in_dll = ((unsigned long)fp >= base && (unsigned long)fp < end);
            LOG("    vt9[%02d]=%p %s\n", i, fp, in_dll ? "<-- d3d9" : "");
        }
    }

    void* dev = NULL;
    long hr = CreateDevice_fn(d3d9, 0, 1, hwnd, 0, &pp, &dev);
    LOG("[*] CreateDevice hr=%08lX dev=%p\n", (unsigned long)hr, dev);
    if (hr != 0 || !dev) { LOG("[!] CreateDevice failed\n"); return 1; }

    void** vt = *(void***)dev;
    LOG("[*] dev=%p vtable=%p\n", dev, vt);
    /* dump vtable 18-24 (CreateTexture 区) 和 58-66 (SetTexture 区) */
    for (int i = 18; i <= 24; i++) LOG("    vt[%02d]=%p\n", i, vt[i]);
    for (int i = 58; i <= 66; i++) LOG("    vt[%02d]=%p\n", i, vt[i]);

    /* 只观察，不 hook */
    LOG("[*] 阶段一完成：vtable 已 dump，游戏正常运行\n");

    /* 保持进程运行 60 秒让用户观察 */
    Sleep(60000);
    LOG("[*] worker 结束\n");
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
