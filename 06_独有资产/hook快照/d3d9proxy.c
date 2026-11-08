/* d3d9proxy.c — 100% 透明 d3d9 代理
 * 除 Direct3DCreate9 外，所有导出都用 x86 尾跳转桩: jmp [real_addr]
 * 这样参数/栈/返回值/调用约定完全不经过 C ABI，行为与系统 DLL 完全一致
 */
#include <windows.h>

#define LOGF "E://SteamLibrary//steamapps//common//Obscure//hzp_tools//out//proxy.log"
#define REAL_DLL "C://Windows//System32//d3d9.dll"

/* 真实函数地址槽（用显式 asm 名，供下面的汇编引用） */
static void* p_Create9Ex          __asm__("pCreate9Ex");
static void* p_Create9On12        __asm__("pCreate9On12");
static void* p_Create9On12Ex      __asm__("pCreate9On12Ex");
static void* p_ShaderValidator    __asm__("pShaderValidator");
static void* p_PSGPError          __asm__("pPSGPError");
static void* p_PSGPSampleTexture  __asm__("pPSGPSampleTexture");
static void* p_DebugSetLevel      __asm__("pDebugSetLevel");
static void* p_DebugSetMute       __asm__("pDebugSetMute");
static void* p_MaxWinShim         __asm__("pMaxWinShim");
static void* p_PERF_BeginEvent    __asm__("pPERFBeginEvent");
static void* p_PERF_EndEvent      __asm__("pPERFEndEvent");
static void* p_PERF_GetStatus     __asm__("pPERFGetStatus");
static void* p_PERF_QueryRepeat   __asm__("pPERFQueryRepeat");
static void* p_PERF_SetMarker     __asm__("pPERFSetMarker");
static void* p_PERF_SetOptions    __asm__("pPERFSetOptions");
static void* p_PERF_SetRegion     __asm__("pPERFSetRegion");

/* 尾跳转桩: jmp dword ptr [槽] */

/* 尾跳转桩 */
__attribute__((naked)) void Stub_Create9Ex(void) { __asm__("jmp *pCreate9Ex"); }
__attribute__((naked)) void Stub_Create9On12(void) { __asm__("jmp *pCreate9On12"); }
__attribute__((naked)) void Stub_Create9On12Ex(void) { __asm__("jmp *pCreate9On12Ex"); }
__attribute__((naked)) void Stub_ShaderValidator(void) { __asm__("jmp *pShaderValidator"); }
__attribute__((naked)) void Stub_PSGPError(void) { __asm__("jmp *pPSGPError"); }
__attribute__((naked)) void Stub_PSGPSampleTexture(void) { __asm__("jmp *pPSGPSampleTexture"); }
__attribute__((naked)) void Stub_DebugSetLevel(void) { __asm__("jmp *pDebugSetLevel"); }
__attribute__((naked)) void Stub_DebugSetMute(void) { __asm__("jmp *pDebugSetMute"); }
__attribute__((naked)) void Stub_MaxWinShim(void) { __asm__("jmp *pMaxWinShim"); }
__attribute__((naked)) void Stub_PERFBeginEvent(void) { __asm__("jmp *pPERFBeginEvent"); }
__attribute__((naked)) void Stub_PERFEndEvent(void) { __asm__("jmp *pPERFEndEvent"); }
__attribute__((naked)) void Stub_PERFGetStatus(void) { __asm__("jmp *pPERFGetStatus"); }
__attribute__((naked)) void Stub_PERFQueryRepeat(void) { __asm__("jmp *pPERFQueryRepeat"); }
__attribute__((naked)) void Stub_PERFSetMarker(void) { __asm__("jmp *pPERFSetMarker"); }
__attribute__((naked)) void Stub_PERFSetOptions(void) { __asm__("jmp *pPERFSetOptions"); }
__attribute__((naked)) void Stub_PERFSetRegion(void) { __asm__("jmp *pPERFSetRegion"); }


static HMODULE g_real = NULL;
static int g_called = 0;

static void LOG(const char* s) {
    HANDLE h = CreateFileA(LOGF, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD n = 0; WriteFile(h, s, lstrlenA(s), &n, NULL); CloseHandle(h);
}

static void resolve_all(void) {
    if (g_real) return;
    g_real = LoadLibraryA(REAL_DLL);
    if (!g_real) { LOG("[PROXY] FATAL: cannot load real d3d9\r\n"); return; }
    p_Create9Ex         = (void*)GetProcAddress(g_real, "Direct3DCreate9Ex");
    p_Create9On12       = (void*)GetProcAddress(g_real, "Direct3DCreate9On12");
    p_Create9On12Ex     = (void*)GetProcAddress(g_real, "Direct3DCreate9On12Ex");
    p_ShaderValidator   = (void*)GetProcAddress(g_real, "Direct3DShaderValidatorCreate9");
    p_PSGPError         = (void*)GetProcAddress(g_real, "PSGPError");
    p_PSGPSampleTexture = (void*)GetProcAddress(g_real, "PSGPSampleTexture");
    p_DebugSetLevel     = (void*)GetProcAddress(g_real, "DebugSetLevel");
    p_DebugSetMute      = (void*)GetProcAddress(g_real, "DebugSetMute");
    p_MaxWinShim        = (void*)GetProcAddress(g_real, "Direct3D9EnableMaximizedWindowedModeShim");
    p_PERF_BeginEvent   = (void*)GetProcAddress(g_real, "D3DPERF_BeginEvent");
    p_PERF_EndEvent     = (void*)GetProcAddress(g_real, "D3DPERF_EndEvent");
    p_PERF_GetStatus    = (void*)GetProcAddress(g_real, "D3DPERF_GetStatus");
    p_PERF_QueryRepeat  = (void*)GetProcAddress(g_real, "D3DPERF_QueryRepeatFrame");
    p_PERF_SetMarker    = (void*)GetProcAddress(g_real, "D3DPERF_SetMarker");
    p_PERF_SetOptions   = (void*)GetProcAddress(g_real, "D3DPERF_SetOptions");
    p_PERF_SetRegion    = (void*)GetProcAddress(g_real, "D3DPERF_SetRegion");
}


/* Direct3DCreate9 wrapper */
void* WINAPI Direct3DCreate9(unsigned int sdk) {
    resolve_all();
    typedef void* (*F)(unsigned int);
    F f = (F)GetProcAddress(g_real, "Direct3DCreate9");
    void* r = f ? f(sdk) : 0;
    if (!g_called++) {
        char b[512], t[64], p[MAX_PATH];
        GetModuleFileNameA(g_real, p, MAX_PATH);
        lstrcatA(b, "[PROXY] Direct3DCreate9 ENTER sdk=");
        wsprintfA(t, "%u", sdk); lstrcatA(b, t);
        lstrcatA(b, "\r\n  real="); lstrcatA(b, p);
        lstrcatA(b, "\r\n  real fn=");
        wsprintfA(t, "%p", (void*)f); lstrcatA(b, t);
        lstrcatA(b, "\r\n  RETURN=");
        wsprintfA(t, "%p", r); lstrcatA(b, t);
        if (r) { void** vt = *(void***)r; lstrcatA(b, "  vtable="); wsprintfA(t, "%p", (void*)vt); lstrcatA(b, t); }
        lstrcatA(b, "\r\n");
        LOG(b);
    }
    return r;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        char b[512], t[32], self[MAX_PATH];
        GetModuleFileNameA(h, self, MAX_PATH);
        lstrcatA(b, "\r\n######## PROXY LOADED pid=");
        wsprintfA(t, "%lu", (unsigned long)GetCurrentProcessId());
        lstrcatA(b, t);
        lstrcatA(b, "\r\n  self = "); lstrcatA(b, self); lstrcatA(b, "\r\n");
        LOG(b);
        DisableThreadLibraryCalls(h);
    }
    return TRUE;
}
