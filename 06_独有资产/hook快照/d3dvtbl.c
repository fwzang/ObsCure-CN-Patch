/* d3dvtbl.c — 32 位独立程序：创建 D3D9 device，dump vtable 偏移
 * 编译: python -m ziglang cc -target x86-windows-gnu -O2 -fno-sanitize=undefined -o d3dvtbl.exe d3dvtbl.c
 */
#include <windows.h>
#include <stdio.h>

#define D3D_SDK_VERSION 32
unsigned long GetModuleFileNameSize(HMODULE h);

typedef struct {
    unsigned long BackBufferWidth, BackBufferHeight, BackBufferFormat, BackBufferCount;
    unsigned long MultiSampleType, MultiSampleQuality, SwapEffect;
    void* hDeviceWindow;
    unsigned long Windowed, EnableAutoDepthStencil, AutoDepthStencilFormat, Flags;
    unsigned long FullScreen_RefreshRateInHz, PresentationInterval;
} D3DPP;

typedef long (WINAPI *PFN_CreateDevice)(void*, unsigned long, unsigned long, void*,
                                        unsigned long, void*, void**);

int main(void) {
    HMODULE hD3D9 = LoadLibraryA("d3d9.dll");
    if (!hD3D9) { printf("LoadLibrary d3d9 failed\n"); return 1; }
    unsigned long base = (unsigned long)hD3D9;
    unsigned long end = base + GetModuleFileNameSize(hD3D9);
    printf("d3d9.dll base=0x%08lX size=0x%08lX\n", base, end - base);

    typedef void* (WINAPI *PFN_Create9)(unsigned int);
    PFN_Create9 create9 = (PFN_Create9)GetProcAddress(hD3D9, "Direct3DCreate9");
    void* d3d9 = create9(32);
    if (!d3d9) { printf("Direct3DCreate9 failed\n"); return 1; }
    printf("d3d9 obj=0x%08lX\n", (unsigned long)d3d9);

    /* 隐藏窗口 */
    WNDCLASSA wc = {0}; wc.lpfnWndProc = DefWindowProcA; wc.lpszClassName = "d3dvtbl";
    RegisterClassA(&wc);
    HWND hwnd = CreateWindowExA(0, "d3dvtbl", "", WS_POPUP, 0, 0, 1, 1, NULL, NULL, NULL, NULL);

    /* 创建 device */
    void** vt9 = *(void***)d3d9;
    PFN_CreateDevice CreateDevice = (PFN_CreateDevice)vt9[16];
    D3DPP pp; ZeroMemory(&pp, sizeof(pp));
    pp.Windowed = 1; pp.SwapEffect = 1; pp.BackBufferFormat = 21; pp.hDeviceWindow = hwnd;
    void* dev = NULL;
    long hr = CreateDevice(d3d9, 0, 1 /*HAL*/, hwnd, 0x20 /*SW*/, &pp, &dev);
    printf("CreateDevice hr=0x%08lX dev=0x%08lX\n", (unsigned long)hr, (unsigned long)dev);
    if (!dev) {
        hr = CreateDevice(d3d9, 0, 3 /*NULLREF*/, hwnd, 0x20, &pp, &dev);
        printf("CreateDevice(NULLREF) hr=0x%08lX dev=0x%08lX\n", (unsigned long)hr, (unsigned long)dev);
    }
    if (!dev) return 1;

    void** vt = *(void***)dev;
    printf("\n=== IDirect3DDevice9 vtable (offset from d3d9.dll base) ===\n");
    printf("%-6s %-14s %-10s %s\n", "index", "address", "offset", "in_d3d9");
    for (int i = 0; i <= 119; i++) {
        void* fp = vt[i];
        unsigned long addr = (unsigned long)fp;
        int in = (addr >= base && addr < end);
        /* 标出 CreateTexture(22) 和 SetTexture(61) 候选 */
        const char* tag = "";
        if (i == 22) tag = " ← CreateTexture?";
        if (i == 23) tag = " ← CreateTexture? (alt)";
        if (i == 61) tag = " ← SetTexture?";
        if (i == 65) tag = " ← SetTexture? (alt)";
        if (in || i == 22 || i == 23 || i == 61 || i == 65)
            printf("vt[%3d] 0x%08lX  0x%08lX  %s%s\n", i, addr, in ? addr - base : 0, in ? "Y" : "N", tag);
    }

    /* 也 dump IDirect3D9 vtable */
    printf("\n=== IDirect3D9 vtable ===\n");
    for (int i = 0; i <= 20; i++) {
        void* fp = vt9[i];
        unsigned long addr = (unsigned long)fp;
        int in = (addr >= base && addr < end);
        printf("vt9[%2d] 0x%08lX  0x%08lX  %s%s\n", i, addr, in ? addr - base : 0,
               in ? "Y" : "N", i == 16 ? " ← CreateDevice?" : "");
    }

    /* 获取 512x128 纹理描述 */
    printf("\n=== 尝试通过 CreateTexture 创建 512x128 测试纹理 ===\n");
    if (dev) {
        void** vt_dev = *(void***)dev;
        /* CreateTexture 在 vtable 某处；尝试索引 22 */
        typedef long (WINAPI *PFN_CTex)(void*, unsigned long, unsigned long, unsigned long,
                                        unsigned long, unsigned long, unsigned long, void**);
        PFN_CTex CreateTexture = (PFN_CTex)vt_dev[22];
        void* tex = NULL;
        long r = CreateTexture(dev, 512, 128, 1, 0, 21, 2 /*MANAGED*/, &tex);
        printf("CreateTexture(vt[22]) 512x128 hr=0x%08lX tex=0x%08lX\n", (unsigned long)r, (unsigned long)tex);
        if (!tex) {
            CreateTexture = (PFN_CTex)vt_dev[23];
            r = CreateTexture(dev, 512, 128, 1, 0, 21, 2, &tex);
            printf("CreateTexture(vt[23]) 512x128 hr=0x%08lX tex=0x%08lX\n", (unsigned long)r, (unsigned long)tex);
        }
    }

    /* 保持窗口 10 秒 */
    Sleep(10000);
    return 0;
}

unsigned long GetModuleFileNameSize(HMODULE h) {
    char path[MAX_PATH];
    GetModuleFileNameA(h, path, MAX_PATH);
    /* 用 SizeOfImage 从 PE 头读 */
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)h;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)((char*)h + dos->e_lfanew);
    return nt->OptionalHeader.SizeOfImage;
}
