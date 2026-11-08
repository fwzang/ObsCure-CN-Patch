/* version_proxy.c — 方案 X：version.dll 代理 + IAT hook + vtable 槽指针替换
 *
 * 步骤状态（原子推进，每步一个变量）:
 *   [x] 第一步  纯透明转发（17 个导出，32B 对齐桩，DllMain 立即 resolve_all）
 *   [x] 第二步  IAT hook Obscure.exe 的 d3d9!Direct3DCreate9 → 替换 IDirect3D9 的 vt[16] CreateDevice
 *   [ ] 第三步  device vt[17] Present 每帧计数
 *   [ ] 第四步  device vt[65] SetTexture 只读记录
 *
 * 纪律:
 *   - 不修改系统 d3d9.dll 的代码段（只用 IAT hook）
 *   - 不碰 Overlay（不 patch gameoverlayrenderer，只叠加在游戏拿到的对象上）
 *   - 对象 vtable 采用「克隆 + 换对象首指针」，不原地改共享 vtable，避免影响 Overlay 内部
 *
 * 编译:
 *   python -m ziglang cc -target x86-windows-gnu -O2 -fno-sanitize=undefined \
 *       -shared -o version.dll version_proxy.c version.def
 */
#include <windows.h>

/* ★★★ 实验开关（必须在使用它的 #if 之前定义）★★★ */
/*   1 = 图集 1024，所有 512x128 组宽高都改成 1024x1024
 *   2 = 图集 1024，但宽高【保持 512x128 不动】
 *   3 = 图集 1024，宽改成 1024、高保持 128
 *   4 = 图集 1024，所有组宽高改 1024，但【不换字形表】
 *   5 = 图集 1024，替换【所有】 512x128 纹理，且【不改】任何对象宽高
 *   6 = 图集 1024，替换所有 512x128 纹理 + 改所有组宽高为 1024
 *   7 = ★基线：图集 1024（内容贴左上角），替换所有字体纹理，宽高/表全不改
 *   20 = ★方案C：图集 512x1024（宽不变），纹理 512x1024，只把 +12 改 1024，
 *        +8 保持 512 不动，只有 grp1 换新表
 *   21 = 方案C 但所有组都改 +12
 *   10 = 纯诊断：什么都不改，只 dump 字体对象全字段
 */
#define EXP_MODE 21

/* ★★ c46 二分实验开关 ★★
 *   TEX_INTERVENE = 1 -> 完整干预（换纹理 + 改参数），默认
 *   TEX_INTERVENE = 0 -> 【完全不碰纹理与参数】，只保留 vtable hook 框架
 *   用途：判定 Alt-Tab 白屏是「hook 框架本身（vtable 补丁）」造成，
 *         还是「我们的纹理/参数干预」造成。二分定位，一次只变一个变量。
 */
#ifndef TEX_INTERVENE
#define TEX_INTERVENE 1
#endif

/* ★★ c93：D2 无查询强制恢复门（默认 0 = 与 c92 行为完全一致，一键退回）
 *   背景：#6（启动/暂停菜单文字不显示）根因 = Reset 后游戏用【旁路】绑定
 *   自己的字体图集，SetTexture 门禁永远等不到绑定；c45 看门狗本可处理，
 *   但 GetTexture 查询已被 c81 实证为 Reset 失败源（未配对引用泄漏），
 *   c81 起查询全关 -> 看门狗残废。
 *   D2 = 不查询 stage0，在 DrawPrimitive 入口满足全部门禁时【同帧】
 *   自绑大图集 + 打参数补丁 + 开闸（参数与纹理同进同退，c42 竖条不可能）。
 *   门禁（全部既有信号，零新增 D3D9 getter）：
 *     1) rearmPending（仅由 Reset 置位）且本窗口未尝试过（单次性）
 *     2) Reset 后出现 FONT-CONFIRM（内容哈希确认，非 CreateTexture 裸指针）
 *     3) Reset 后 bigTex 重建完成
 *     4) 距确认/重建稳定 ≥150 帧
 *     5) 最近完成的百帧窗口 DIP 增量==0（实测双峰零灰区：UI=0，3D=8300~16800）
 */
#ifndef EXP_D2_GATE
#define EXP_D2_GATE 0
#endif

/* ★★ c49 精细二分：把「创建大纹理」与「改参数/换纹理」拆开 ★★
 *   TEX_CREATE_ONLY = 1 -> 只创建 512×1024 纹理并灌数据，
 *                        【不改字体组参数、不做纹理替换】
 *   = 0 -> 完整干预
 *   用途：判定 INVALIDCALL 是「纹理存在本身」还是「参数/替换操作」引起。
 */
#define TEX_CREATE_ONLY 0

/* ★★ c69：P1 诊断探针总闸（默认 0=关闭；编译时 -DP1_DIAG_PROBES=1 可恢复）
 *   关闭内容：[RST-ST]/[VERB]/[512×128 CALLER]/[PROBE]/[STK]/[CTP] —— P1 诊断期
 *   （c56-c61）加入的【只读】探针（含 GetModuleHandleEx/GetLevelDesc/栈回溯调用）。
 *   原因（2026-10-09 深夜实验）：post-reset 重建期这些探针疑似导致主线程冻结
 *   （Reset 成功后游戏卡在 [CTP] 探针处，日志戛然而止、窗口无响应）。
 *   关闭后仍保留：[CT]/[UTD]/[FONT-CONFIRM]/[DUMP]/[VERD] 等核心日志。 */
#ifndef P1_DIAG_PROBES
#define P1_DIAG_PROBES 0
#endif

#define LOGF   "__RUNTIME__"   /* 运行时填 */
#define HOOKLOG "__RUNTIME__"  /* 运行时填 */
#define REAL_DLL "C://Windows//SysWOW64//version.dll"

/* ============================================================
 *  日志
 * ============================================================ */

/* ============================================================
 *  ★ c87 运行时路径推导（跨机器可移植，不再硬编码盘符）
 *    g_base = 本 DLL 自身所在目录 = 游戏目录
 * ============================================================ */
static char g_base[300]     = {0};   /* DLL(=游戏)目录，末尾带反斜杠 */
static char g_rawPath[340]  = {0};   /* 图集完整路径 */
static char g_tblPath[340]  = {0};   /* 字形表完整路径 */
static char g_logDir[340]   = {0};   /* 日志目录 */
static char g_tdDir[340]    = {0};   /* 贴图 dump 目录 */

static int file_exists_a(const char* p) {
    DWORD a = GetFileAttributesA(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}
static int dir_exists_a(const char* p) {
    DWORD a = GetFileAttributesA(p);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

/* 在候选目录里找 name，找到返回完整路径（写入 buf），否则返回 0 */
static const char* find_asset(const char* name, char* buf, int cap) {
    const char* dirs[3];
    int nd = 0;
    dirs[nd++] = g_base;                                            /* 交付布局 */
    dirs[nd++] = "hzp_tools\\out\\";                                 /* 开发布局 */
    dirs[nd++] = "Obscure_assets\\";                                 /* 备用 */
    for (int i = 0; i < nd; i++) {
        char full[340];
        if (i == 0) {
            /* dirs[0] 就是 g_base（已带尾反斜杠），不能再拼一次 */
            lstrcpynA(full, g_base, sizeof(full));
        } else {
            lstrcpynA(full, g_base, sizeof(full));
            lstrcatA(full, dirs[i]);
        }
        lstrcatA(full, name);
        if (file_exists_a(full)) {
            lstrcpynA(buf, full, cap);
            return buf;
        }
    }
    return 0;
}

static void init_runtime_paths(void) {
    if (g_base[0]) return;
    HMODULE hm = NULL;
    /* 用本函数地址反查自身模块 -> 最稳的"我在哪" */
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                          (LPCSTR)&init_runtime_paths, &hm)) {
        char buf[300];
        if (GetModuleFileNameA(hm, buf, sizeof(buf)) > 0) {
            lstrcpynA(g_base, buf, sizeof(g_base));
            int L = lstrlenA(g_base);
            while (L > 0 && g_base[L-1] != '\\') L--;   /* 找末尾反斜杠 */
            if (L > 0) g_base[L] = 0;                     /* 去掉文件名，留目录+反斜杠 */
        }
    }
    if (!g_base[0]) {                            /* 兜底：用 cwd */
        if (GetCurrentDirectoryA(sizeof(g_base), g_base) == 0) lstrcpyA(g_base, ".\\");
        else lstrcatA(g_base, "\\");
    }
    /* 图集 / 字形表 */
    find_asset("big_atlas_c.raw", g_rawPath, sizeof(g_rawPath));
    find_asset("table_c.bin",   g_tblPath, sizeof(g_tblPath));
    if (!g_rawPath[0]) lstrcpynA(g_rawPath, "__RUNTIME__", sizeof(g_rawPath));
    if (!g_tblPath[0]) lstrcpynA(g_tblPath, "__RUNTIME__", sizeof(g_tblPath));
    /* 日志目录：优先 hzp_tools\out（与原行为一致），否则游戏目录 */
    lstrcpynA(g_logDir, g_base, sizeof(g_logDir));
    lstrcatA(g_logDir, "hzp_tools\\out\\");
    if (!dir_exists_a(g_logDir)) lstrcpynA(g_logDir, g_base, sizeof(g_logDir));
    lstrcpynA(g_tdDir, g_logDir, sizeof(g_tdDir));
    lstrcatA(g_tdDir, "tex_dump\\");
    /* 落盘一条，方便确认推导结果（纯 Win32，不依赖 stdio） */
    char msg[1024];
    lstrcpynA(msg, g_logDir, sizeof(msg));
    lstrcatA(msg, "c87_paths.txt");
    HANDLE hf = CreateFileA(msg, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hf != INVALID_HANDLE_VALUE) {
        char* q = msg;                       /* q 游标推进, 避免覆写 */
        lstrcpynA(q, "base = ", 32); q += lstrlenA(q);
        lstrcpynA(q, g_base, 300); q += lstrlenA(q);
        lstrcpynA(q, "\r\n", 4); q += lstrlenA(q);
        lstrcpynA(q, "raw  = ", 32); q += lstrlenA(q);
        lstrcpynA(q, g_rawPath, 340); q += lstrlenA(q);
        lstrcpynA(q, file_exists_a(g_rawPath) ? "   [OK]" : "   [MISSING]", 24); q += lstrlenA(q);
        lstrcpynA(q, "\r\n", 4); q += lstrlenA(q);
        lstrcpynA(q, "tbl  = ", 32); q += lstrlenA(q);
        lstrcpynA(q, g_tblPath, 340); q += lstrlenA(q);
        lstrcpynA(q, file_exists_a(g_tblPath) ? "   [OK]" : "   [MISSING]", 24); q += lstrlenA(q);
        lstrcpynA(q, "\r\n", 4); q += lstrlenA(q);
        lstrcpynA(q, "log  = ", 32); q += lstrlenA(q);
        lstrcpynA(q, g_logDir, 340); q += lstrlenA(q);
        lstrcpynA(q, "\r\n", 4); q += lstrlenA(q);
        DWORD wr = 0; WriteFile(hf, msg, (DWORD)(q - msg), &wr, NULL); CloseHandle(hf);
    }
}

static char* mk_logpath(const char* name) {
    static char buf[400];
    lstrcpynA(buf, g_logDir, sizeof(buf));
    lstrcatA(buf, name);
    return buf;
}

static void LOGF_(const char* file, const char* s);   /* 前向声明 */

/* ============================================================
 *  ★ c88 文件访问记录器（诊断 #6/#存档）：记录游戏对 *.sav / save 目录的访问
 *    编译开关 -DEXP_FILELOG=1 启用；默认关闭，零开销。
 *    原理：hook exe IAT 里的 kernel32!CreateFileA，只对路径含
 *    "save"/".sav" 的调用记一行（原始路径 + 欲望访问 + 返回句柄）。
 * ============================================================ */
#if EXP_FILELOG
typedef HANDLE (WINAPI *PFN_CreateFileA)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES,
                                         DWORD, DWORD, HANDLE);
static PFN_CreateFileA g_real_CreateFileA = NULL;
static DWORD g_flTls = 0;            /* 防重入 */

static HANDLE WINAPI Hook_CreateFileA(LPCSTR name, DWORD access, DWORD share,
                                      LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                      DWORD flags, HANDLE tmpl) {
    HANDLE r = g_real_CreateFileA(name, access, share, sa, disp, flags, tmpl);
    if (name && !g_flTls) {
        /* 只关心 save 相关路径 */
        char low[300]; int i = 0;
        for (; name[i] && i < 299; i++) {
            char c = name[i];
            low[i] = (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
        }
        low[i] = 0;
        if (strstr(low, ".sav") || strstr(low, "save") || strstr(low, ".ipt")) {
            g_flTls = 1;
            char b[700], t[64];
            lstrcpynA(b, "[FILE] ", sizeof(b));
            lstrcatA(b, name);
            lstrcatA(b, "  acc=0x");
            wsprintfA(t, "%X", access); lstrcatA(b, t);
            lstrcatA(b, " -> ");
            if (r == INVALID_HANDLE_VALUE) {
                lstrcatA(b, "FAIL err=");
                wsprintfA(t, "%lu", GetLastError()); lstrcatA(b, t);
            } else {
                lstrcatA(b, "OK");
            }
            lstrcatA(b, "\r\n");
            LOGF_(mk_logpath("fileaccess.log"), b);
            g_flTls = 0;
        }
    }
    return r;
}

/* 在 exe IAT 里找 kernel32!CreateFileA 并替换 */
static void install_filelog(void) {
    HMODULE exe = GetModuleHandleW(NULL);
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)exe;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)((BYTE*)exe + dos->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)((BYTE*)exe + rva);
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    if (!k32) return;
    FARPROC real = GetProcAddress(k32, "CreateFileA");
    if (!real) return;
    for (; imp->Name; imp++) {
        const char* dll = (const char*)((BYTE*)exe + imp->Name);
        IMAGE_THUNK_DATA* names = (IMAGE_THUNK_DATA*)((BYTE*)exe +
            (imp->OriginalFirstThunk ? imp->OriginalFirstThunk : imp->FirstThunk));
        IMAGE_THUNK_DATA* iat = (IMAGE_THUNK_DATA*)((BYTE*)exe + imp->FirstThunk);
        for (; names->u1.Function; names++, iat++) {
            if (names->u1.Function & 0x80000000) continue;
            const char* nm = (const char*)((BYTE*)exe + names->u1.Function + 2);
            if (lstrcmpiA(nm, "CreateFileA") == 0 && (FARPROC)iat->u1.Function != real) {
                g_real_CreateFileA = (PFN_CreateFileA)iat->u1.Function;
                DWORD old;
                if (VirtualProtect(&iat->u1.Function, sizeof(void*), PAGE_READWRITE, &old)) {
                    iat->u1.Function = (DWORD)Hook_CreateFileA;
                    VirtualProtect(&iat->u1.Function, sizeof(void*), old, &old);
                    char b[160];
                    lstrcpynA(b, "[FILELOG] IAT CreateFileA hooked: ", sizeof(b));
                    char t[32];
                    wsprintfA(t, "%p -> %p\r\n", (void*)g_real_CreateFileA, (void*)Hook_CreateFileA);
                    lstrcatA(b, t);
                    LOGF_(mk_logpath("fileaccess.log"), b);
                }
                return;
            }
        }
    }
}
#else
static void install_filelog(void) { }
#endif  /* EXP_FILELOG */



static void LOGF_(const char* file, const char* s) {
    HANDLE h = CreateFileA(file, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD n = 0; WriteFile(h, s, lstrlenA(s), &n, NULL); CloseHandle(h);
}
static void VPLOG(const char* s) { LOGF_(mk_logpath("versionproxy.log"), s); }
static void HKLOG(const char* s) { LOGF_(mk_logpath("x_hook.log"), s); }

/* ============================================================
 *  第一步：透明转发（17 个导出，32 字节对齐桩）
 * ============================================================ */
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

__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoA(void)        { __asm__("jmp *pGetFileVersionInfoA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoByHandle(void) { __asm__("jmp *pGetFileVersionInfoByHandle"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoExA(void)      { __asm__("jmp *pGetFileVersionInfoExA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoExW(void)      { __asm__("jmp *pGetFileVersionInfoExW"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoSizeA(void)    { __asm__("jmp *pGetFileVersionInfoSizeA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoSizeExA(void)  { __asm__("jmp *pGetFileVersionInfoSizeExA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoSizeExW(void)  { __asm__("jmp *pGetFileVersionInfoSizeExW"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoSizeW(void)    { __asm__("jmp *pGetFileVersionInfoSizeW"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_GetFileVersionInfoW(void)        { __asm__("jmp *pGetFileVersionInfoW"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerFindFileA(void)               { __asm__("jmp *pVerFindFileA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerFindFileW(void)               { __asm__("jmp *pVerFindFileW"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerInstallFileA(void)            { __asm__("jmp *pVerInstallFileA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerInstallFileW(void)            { __asm__("jmp *pVerInstallFileW"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerLanguageNameA(void)           { __asm__("jmp *pVerLanguageNameA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerLanguageNameW(void)           { __asm__("jmp *pVerLanguageNameW"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerQueryValueA(void)             { __asm__("jmp *pVerQueryValueA"); }
__attribute__((aligned(32))) __attribute__((naked)) void Stub_VerQueryValueW(void)             { __asm__("jmp *pVerQueryValueW"); }

static HMODULE g_realVer = NULL;

static void resolve_all(void) {
    if (g_realVer) return;
    g_realVer = LoadLibraryA(REAL_DLL);
    if (!g_realVer) { VPLOG("[VP] FATAL: cannot load real version.dll\r\n"); return; }
    p_GetFileVersionInfoA        = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoA");
    p_GetFileVersionInfoByHandle = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoByHandle");
    p_GetFileVersionInfoExA      = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoExA");
    p_GetFileVersionInfoExW      = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoExW");
    p_GetFileVersionInfoSizeA    = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoSizeA");
    p_GetFileVersionInfoSizeExA  = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoSizeExA");
    p_GetFileVersionInfoSizeExW  = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoSizeExW");
    p_GetFileVersionInfoSizeW    = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoSizeW");
    p_GetFileVersionInfoW        = (void*)GetProcAddress(g_realVer, "GetFileVersionInfoW");
    p_VerFindFileA               = (void*)GetProcAddress(g_realVer, "VerFindFileA");
    p_VerFindFileW               = (void*)GetProcAddress(g_realVer, "VerFindFileW");
    p_VerInstallFileA            = (void*)GetProcAddress(g_realVer, "VerInstallFileA");
    p_VerInstallFileW            = (void*)GetProcAddress(g_realVer, "VerInstallFileW");
    p_VerLanguageNameA           = (void*)GetProcAddress(g_realVer, "VerLanguageNameA");
    p_VerLanguageNameW           = (void*)GetProcAddress(g_realVer, "VerLanguageNameW");
    p_VerQueryValueA             = (void*)GetProcAddress(g_realVer, "VerQueryValueA");
    p_VerQueryValueW             = (void*)GetProcAddress(g_realVer, "VerQueryValueW");
}

/* ============================================================
 *  第二步：IAT hook + CreateDevice
 * ============================================================ */
typedef void* (WINAPI *PFN_D3DC9)(unsigned int);
typedef long  (WINAPI *PFN_CreateDevice)(void*, unsigned int, unsigned int, void*,
                                         unsigned long, void*, void**);

static PFN_D3DC9        g_orig_Create9    = NULL;   /* IAT 原值（可能是 Overlay 的转发） */
static PFN_CreateDevice g_orig_CreateDevice = NULL;
static void**           g_iat_slot        = NULL;   /* 被改的 IAT 槽地址 */


static const char* mod_of(DWORD a) {
    static char buf[MAX_PATH];
    HMODULE hm = 0;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCSTR)a, &hm)) return "?";
    GetModuleFileNameA(hm, buf, MAX_PATH);
    char* p = strrchr(buf, '\\');
    return p ? p + 1 : buf;
}

/* 为一个原始 vtable 生成克隆（幂等：同一原始 vtable 只克隆一次，多个对象共享） */
static void** g_origVt  = NULL;    /* 已克隆的原始 vtable */
static void** g_cloneVt = NULL;    /* 对应的克隆 */

static void** make_clone(void** realVt, int idx, void* hook, int n) {
    if (!realVt) return NULL;
    void** fake = (void**)VirtualAlloc(NULL, n * sizeof(void*),
                                       MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!fake) return NULL;
    for (int i = 0; i < n; i++) fake[i] = realVt[i];
    fake[idx] = hook;
    return fake;
}

/* 把对象接到克隆 vtable 上（同一原始 vtable 的所有对象共享一个克隆） */
static void attach_object(void* obj, int idx, void* hook, int n) {
    if (!obj) return;
    void** vt = *(void***)obj;
    if (!vt) return;
    if (vt == g_cloneVt) return;                    /* 已经接过 */
    if (vt != g_origVt) {                           /* 新的原始 vtable -> 建新克隆 */
        g_origVt  = vt;
        g_orig_CreateDevice = (PFN_CreateDevice)vt[idx];
        g_cloneVt = make_clone(vt, idx, hook, n);
        char b[256], t[64];
        b[0] = 0;
        lstrcatA(b, "  [vt16] 新克隆: orig="); wsprintfA(t, "%p", (void*)vt); lstrcatA(b, t);
        lstrcatA(b, " clone="); wsprintfA(t, "%p", (void*)g_cloneVt); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
    if (g_cloneVt) *(void***)obj = g_cloneVt;       /* ★ 每个对象都要换首指针 */
}

/* ============================================================
 *  第三步：device vt[17] Present 每帧计数
 * ============================================================ */
typedef long (WINAPI *PFN_Present)(void*, const void*, const void*, void*, const void*);
typedef long (WINAPI *PFN_SetTexture)(void*, unsigned long, void*);   /* vt[65]，前移供 Present 用 */
static PFN_Present g_orig_Present = NULL;
static long g_frames   = 0;      /* 帧计数 */
static long g_bindTick = 0;      /* 本 100 帧内字体纹理绑定次数 */
/* ★ draw 调用计数（判断游戏走哪个绘制路径） */
static long g_drawCntDIP = 0, g_drawCntUP = 0, g_drawCntIUP = 0;

/* ★★★ c93 D2 门禁全局状态（信号复用既有变量，零新增 getter）
 *  置于文件头部：Present(~800)/Hook_UpdateTexture/Reset 路径均在前部引用。
 *  ★ 无条件编译：引用点（Reset/FONT-CONFIRM/Present）不随开关变化，
 *    仅 d2_try 触发体受 EXP_D2_GATE 控制，保证 0=与 c92 行为一致。 */
static long g_d2ResetFrame   = -1;  /* 最近一次成功 Reset 的帧号 */
static long g_d2ConfirmFrame = -1;  /* Reset 后 FONT-CONFIRM（内容哈希确认）帧号 */
static long g_d2BigTexFrame  = -1;  /* Reset 后 bigTex 重建完成帧号 */
static int  g_d2Tried        = 0;   /* 本 rearm 窗口是否已尝试（单次性保证） */
static long g_dipWindowBase  = 0;   /* 百帧窗口起点时的 DIP 累计值 */
static int  g_dipWinZero     = 1;   /* 最近完成的百帧窗口 DIP 增量是否为 0 */
static long g_d2N            = 0;   /* D2 触发累计 */
/* 耗时计数器（仅供日志，不影响行为） */
static long g_hashLastUs     = 0;   /* td_src_hash 最近耗时（微秒） */
static long g_hashMaxUs      = 0;   /* td_src_hash 峰值耗时（微秒） */
/* ★ c93e：运行时换回模式 与 启动武装 */
static int  g_swapModeB  = 0;   /* swap_mode.txt 内容以'B'开头 -> 1（只换非 g_fontTex 命中项） */
static int  g_d2SBudget  = 1;   /* 启动武装预算（会话级 1 次；发生 Reset 即作废） */
static long g_d2SConfirm = -1;  /* 最近组1登记/跟随帧号（无条件记录，供启动武装计时） */
/* ★ 大纹理 / 替换开关（前移供 Hook_Present 用；原始定义在下方） */
static void* g_bigTex   = NULL;
static int   g_subOn    = 1;
static void** g_devOrigVt  = NULL;
static void** g_devCloneVt = NULL;
static int  g_rescan_enable = 0; /* ★ 首次识别到字体纹理后置 1 */
static int  g_rescan_n      = 0; /* ★ 已重扫次数 */
/* ★★ Reset 后「全量日志窗口」：g_frames 小于此值时，SetTexture 全部记录（不限尺寸），
 *   用于查明 Reset 后游戏到底绑定了什么纹理（2026-10-07 Alt-Tab 乱码根因排查）。 */
static long g_verboseUntil  = 0;
static long g_verboseN      = 0;

/* ★ 前向声明：patch_font_obj 定义在下面，但 Present 里要用 */
static void patch_font_obj(void);
/* 探针（阶段2 P1 诊断）：字体对象结构 dump */
static void probe_dump_fontobj(DWORD obj, const char* tag);
static void probe_scan_stack(const char* tag, void* known0);
/* c57 探针：UpdateTexture 原函数（fill_default_texture 在定义点之前要用） */
typedef long (WINAPI *PFN_UpdateTexture)(void*, void*, void*);
typedef long (WINAPI *PFN_UnlockRectT)(void*, unsigned int);
static PFN_UpdateTexture g_orig_UpdTex = 0;
/* c58：判据候选集合（定义在下方，SetTexture 在定义点之前要用） */
static int  ut_dst_hit(void* d);
static void ut_dst_add(void* d);
/* ★ c93c 前向声明：SetTex 换回路径要用（定义在 grp1 区） */
static int  grp1_hit(void* d);
static int  g_grp1N;   /* 组1 指纹集合当前元素数（定义见 g_grp1Tex 处） */

/* ★ 前向声明：这些全局在文件下方定义，但 Hook_Present 里要用 */
static void* g_fontTex;     /* 自动识别的游戏字体纹理 */
static void* g_bigTex;      /* 我们创建的大图集纹理 */
/* ★ 崩溃检查点：异常处理器记录最后到达的位置，便于定位闪退点 */
static const char* g_ckpt = "init";
/* ★ 字体纹理「已确认会被游戏绑定」标志：
 *   只有置 1 才允许打补丁（改 +12/换表），保证参数与纹理解绑同进同退，
 *   否则只改参数不换纹理 -> 白竖条。SetTexture 命中 g_fontTex 时置 1。 */
static long g_fontTexBound = 0;

/* ★★ 诊断（2026-10-08）：Reset 后 SetTexture 活动统计。
 *   目的：回答「游戏 Reset 后到底还调不调 SetTexture、绑不绑新字体纹理」。
 *   此前只能靠日志推断，现在直接计数（零额外开销，只加整数）。 */
static long  g_rstDiagUntil = 0;    /* 诊断窗口截止帧（0=关闭） */
static long  g_rstStTotal   = 0;    /* 窗口内 SetTexture 调用总数 */
static long  g_rstStFont    = 0;    /* 窗口内 tex == g_fontTex 的次数 */
static void* g_rstSample[6];
static int   g_rstSampleN   = 0;
/* ★ 2026-10-08 新增：Reset 后窗口内绑定的【不同】纹理清单（含调用者 RVA），
 *   用于回答「游戏 Reset 后到底绑了什么纹理 / 字体纹理是不是换了指针」。 */
static void* g_rstUniq[40];
static int   g_rstUniqN     = 0;
static int   g_rstLogN      = 0;
static void* g_rstOldFontTex = NULL;   /* Reset 前的字体纹理（旧指针，可能被游戏继续引用） */

/* ★★ post-Reset 强制重绑（2026-10-07 Alt-Tab 白竖条修复）
 *   实测：Reset 后游戏【不再调用 SetTexture】（其内部状态缓存失效但自己不知道），
 *   于是 D3D 用默认白纹理画字 -> 白竖条；我们的 g_fontTex 也失效 -> 无法替换。
 *   对策：Reset 后开启此标志，在 Present（每帧末尾）主动把大纹理绑到 stage0。
 *   游戏中其它绘制若自己 SetTexture 会自然覆盖 -> 不影响场景。
 *   一旦重新识别到字体纹理（g_fontTex 恢复）就关闭标志，交还给正常替换逻辑。 */
static long g_postReset = 0;
/* ★★ Reset 后重建标志（2026-10-07 晚，正确方向）
 *   真正的 Reset 会销毁 D3DPOOL_DEFAULT 资源（游戏字体图集在其中）。
 *   Reset 前我们把字体组参数撤回原值，Reset 后置此标志，
 *   交由 Present 每帧重试 + CreateTexture(vt[23]) 抓新纹理，
 *   直到重新识别到游戏新建的字体图集为止。 */
static long g_postResetNeedRebuild = 0;
static PFN_SetTexture g_ref_SetTexture = NULL;   /* 原 SetTexture（Present 里直接调） */

/* ★ 前向声明：定义在下方（find_font_obj 之前） */
static DWORD safe_read(DWORD src, void* dst, DWORD len);
typedef long (WINAPI *PFN_GetTexture)(void*, unsigned long, void**);

/* ★ 前向声明：字体对象缓存（定义在下方，探针要用） */
static DWORD g_fontObj;
static DWORD g_fontObjs[16];
static int   g_fontObjN;
static void* g_devPtr;             /* 当前 device（定义在下方） */
static void  patch_font_obj(void); /* 定义在下方 */

/* ★ 修复2 状态（2026-10-08）：Reset 后立即重打补丁的待办标志 */
static int   g_rearmPending = 0;   /* 1 = 等待「大纹仍绑定」验证后重打补丁 */
static long  g_rearmUntil   = 0;   /* 重打窗口截止帧（超时则放弃，保持安全态） */
static int   g_rearmDone    = 0;   /* 诊断：是否已成功重打 */
/* ★★★ 帧内局部干预状态（c44，2026-10-08）
 *   设计：我们的干预只存在于【本帧内】——
 *     帧中：出现字体图集绑定 -> 换大图集 + 打参数补丁
 *     帧末(Present)：把纹理绑定还原成游戏的、参数还原成原值
 *   这样游戏在任何帧的开头看到的状态都与它自己的预期完全一致：
 *     · 游戏内不再绑定字体图集（用陈旧缓存）时 -> 参数是原值 -> 原版渲染，不会竖条
 *     · 背景等"陈旧缓存绘制"采样到的是游戏自己的图集，不会出现"整屏图集"
 *   实测依据：游戏会反复重建字体纹理（指针每次都变），且游戏内 SetTexture 绑定数=0。 */
static void* g_swappedGameTex = NULL;  /* 本帧被我们换掉的游戏纹理（帧末还原） */
static int   g_patchActive    = 0;     /* 本帧参数已被我们改成 1024/新表 */
static long  g_swapN          = 0;     /* 交换次数（统计） */
static DWORD g_tblPtr         = 0;     /* 新字形表（只加载一次） */
static DWORD g_tblCnt         = 0;
static long  g_patchLogN      = 0;     /* 补丁日志限流 */
static void ensure_table(void);        /* 定义在下方 */
static void apply_params_fast(void);   /* 定义在下方 */
/* ★ vtable 补丁存活校验（attach_dev 时登记，probe 时比对） */
static void* g_hookPresent = NULL;
static void* g_hookCT      = NULL;
static void* g_hookST      = NULL;
static void* g_hookDP      = NULL;
/* ★ 绘制计数 / JIT 替换计数（定义在下方绘制钩子区） */
static long  g_drawCntDP;
static long  g_jitN;

/* ============================================================
 * ★★★ vtable 自愈（2026-10-08，本次最重要的发现与修复）
 *   实测（c42 日志铁证）：
 *     Reset 后 vt[65]（SetTexture）被【恢复成原版 d3d9 函数】，
 *     而 vt17/vt23/vt81 仍是我们的钩子。
 *     => 游戏之后所有 SetTexture 绑定全部绕过我们（直连 d3d9）：
 *        · stage0 被游戏换成它自己的 512×128 图集
 *        · 我们的大图集替换永不发生 -> 切屏后字体变小
 *        · 若参数已打补丁(1024) 而纹理是游戏的(128) -> 持续竖条（c42 现象）
 *   对策：登记我们打过的每个槽位（idx/hook/下游原函数），
 *         定期检查；发现槽位被换回【模块内函数】就重新打上我们的钩子，
 *         并把当前值作为新的"下游"保存（保持链式调用，兼容游戏自带的包装）。
 * ============================================================ */
typedef struct { int idx; void* hook; void** pOrig; } VT_SLOT;
static VT_SLOT g_vtSlots[8];
static int     g_vtSlotN = 0;
static long    g_healN   = 0;

static void vt_register(int idx, void* hook, void** pOrig) {
    for (int i = 0; i < g_vtSlotN; i++) {
        if (g_vtSlots[i].idx == idx) { g_vtSlots[i].hook = hook; g_vtSlots[i].pOrig = pOrig; return; }
    }
    if (g_vtSlotN < 8) {
        g_vtSlots[g_vtSlotN].idx = idx;
        g_vtSlots[g_vtSlotN].hook = hook;
        g_vtSlots[g_vtSlotN].pOrig = pOrig;
        g_vtSlotN++;
    }
}
static void vtable_heal(void* dev);   /* 定义在 attach_dev 之后 */

/* ★ 设备是否可用（TestCooperativeLevel = vt[3]，返回 0 才可用）。*/
/* TestCooperativeLevel = vt[3]（定义在下方绘制钩子区，这里前向声明） */
typedef long (WINAPI *PFN_TestCoop)(void*);

/* ★★ 铁律：设备丢失/Reset 失败期间【绝对不能】调用任何其它 D3D9 API，
 *      否则会返回 D3DERR_INVALIDCALL 并把设备彻底锁死（白屏）。
 *      c41 事故（Draw 热路径查 GetTexture）与 c43 白屏都源于此。 */
static int dev_alive(void* dev) {
    if (!dev) return 0;
    void** vt = *(void***)dev;
    if (!vt || !vt[3]) return 0;
    return (((PFN_TestCoop)vt[3])(dev) == 0);
}

/* ★ c63（#2 第1步）：COM 引用归还 + 计数。
 *   D3D9 规则：GetTexture 返回的对象【已 AddRef】，调用者必须 Release，
 *   否则引用计数持续泄漏（本文件每 2 帧 probe + Reset 循环 16 次都在泄漏），
 *   泄漏的引用会让后续 Release g_bigTex 无法真正销毁 DEFAULT 资源。 */
static long  g_gtN = 0, g_gtRelN = 0;
typedef unsigned long (WINAPI *PFN_COMRel)(void*);
static void com_release(void* obj) {
    if (!obj) return;
    void** v = *(void***)obj;
    if (!v || !v[2]) return;
    ((PFN_COMRel)v[2])(obj);
    g_gtRelN++;
}

/* ★ 读取当前 stage0绑定的纹理（GetTexture = vt[64]）
 *   ★ 必须先过 dev_alive() 守卫：设备丢失时调用会返回 INVALIDCALL。 */
static void* get_bound_tex0(void* dev) {
#ifdef EXP_NO_QUERY
    (void)dev; return NULL;    /* ★ c81 实验：查询全关（引用泄漏源） */
#endif
    if (!dev_alive(dev)) return NULL;
    void** vt = *(void***)dev;
    if (!vt || !vt[64]) return NULL;
    void* t0 = NULL;
    ((PFN_GetTexture)vt[64])(dev, 0, &t0);
    if (t0) g_gtN++;      /* ★ c63：调用方用完后必须 com_release() */
    return t0;
}

/* ============================================================
 * ★ 状态探针（2026-10-08）：直接读出「当前 stage0 绑定的纹理」与
 *   「字体对象实时字段」，用于判定 Reset 后的真实状态。
 *   —— 之前只能靠日志推断，现在直接看硬件侧状态。
 * ============================================================ */
static void probe_state(void* dev, const char* tag) {
#if TEX_INTERVENE == 0
    HKLOG("[STATE] (c46 二分实验：零 D3D9 调用模式)\r\n");
    return;
#endif
    char b[400], t[64];
    b[0] = 0;
    lstrcatA(b, "[STATE] "); lstrcatA(b, tag);
    lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
    /* ★★★ 关键守卫：设备不可用时【只报状态，绝不碰任何 D3D9 API】。
     *   TestCooperativeLevel 本身是唯一允许的调用（它只是查询）。
     *   之前这里无条件调GetTexture ->设备丢失后触发 D3DERR_INVALIDCALL -> 白屏。 */
    int alive = dev_alive(dev);
    lstrcatA(b, alive ? " devOK" : " devLOST");
    if (dev && alive) {
        void** vt = *(void***)dev;
        if (vt && vt[64]) {                       /* GetTexture = vt[64] */
#ifdef EXP_NO_QUERY
            /* ★ c81 实验（2026-10-09）：跳过 GetTexture 查询。
             *   假设：每次查询给 stage0 纹理 +1 引用（调用方不释放）→ 游戏切屏
             *   清理时释放不掉该纹理 → DEFAULT 资源残留 → Reset INVALIDCALL。
             *   证据：c69（零查询）通过；c80（6 次查询、零创建）失败。 */
            lstrcatA(b, " [c81实验: 跳过 GetTexture 查询]");
#else
            void* t0 = NULL;
            ((PFN_GetTexture)vt[64])(dev, 0, &t0);
            if (t0) g_gtN++;
            lstrcatA(b, " bind0="); wsprintfA(t, "%p", t0); lstrcatA(b, t);
            if (t0 == g_bigTex) lstrcatA(b, " [=bigTex]");
            else if (t0 == g_fontTex) lstrcatA(b, " [=fontTex(游戏原图集)]");
            else if (t0 == NULL) lstrcatA(b, " [=NULL]");
#endif
            /* ★ c64：不 Release —— c63 实测本环境 Release GetTexture 返回值会导致
             *   对象被提前销毁 -> DEVICELOST 白屏。改为降频控制泄漏（见 Present）。 */
        }
        /* ★ vtable 补丁存活校验（纯读内存，无需设备可用） */
        if (vt) {
            lstrcatA(b, " vt17="); lstrcatA(b, (vt[17] == g_hookPresent) ? "ok" : (vt[17] ? "?" : "null"));
            lstrcatA(b, " vt23="); lstrcatA(b, (vt[23] == g_hookCT) ? "ok" : (vt[23] ? "?" : "null"));
            lstrcatA(b, " vt65="); lstrcatA(b, (vt[65] == g_hookST) ? "ok" : (vt[65] ? "?" : "null"));
            lstrcatA(b, " vt81="); lstrcatA(b, (vt[81] == g_hookDP) ? "ok" : (vt[81] ? "?" : "null"));
            /* ★ c63：GetTexture/Release 配对监控 */
            { char gt[80]; wsprintfA(gt, " gt=%ld rel=%ld grp1=%d", g_gtN, g_gtRelN, g_grp1N); lstrcatA(b, gt); }
        }
    } else if (dev) {
        /* 设备丢失：只做纯内存读取，绝不调 D3D9 */
        void** vt = *(void***)dev;
        if (vt) {
            lstrcatA(b, " vt17="); lstrcatA(b, (vt[17] == g_hookPresent) ? "ok" : (vt[17] ? "?" : "null"));
            lstrcatA(b, " vt23="); lstrcatA(b, (vt[23] == g_hookCT) ? "ok" : (vt[23] ? "?" : "null"));
            lstrcatA(b, " vt65="); lstrcatA(b, (vt[65] == g_hookST) ? "ok" : (vt[65] ? "?" : "null"));
            lstrcatA(b, " vt81="); lstrcatA(b, (vt[81] == g_hookDP) ? "ok" : (vt[81] ? "?" : "null"));
            /* ★ c63：GetTexture/Release 配对监控 */
            { char gt[80]; wsprintfA(gt, " gt=%ld rel=%ld grp1=%d", g_gtN, g_gtRelN, g_grp1N); lstrcatA(b, gt); }
        }
    }
    {
        DWORD obj = g_fontObjN ? g_fontObjs[0] : g_fontObj;
        if (obj) {
            DWORD v0 = 0, v4 = 0, v8 = 0, v12 = 0;
            safe_read(obj,      &v0,  4);
            safe_read(obj + 4,  &v4,  4);
            safe_read(obj + 8,  &v8,  4);
            safe_read(obj + 12, &v12, 4);
            lstrcatA(b, " obj[+0]="); wsprintfA(t, "%lX", v0); lstrcatA(b, t);
            lstrcatA(b, " [+4]="); wsprintfA(t, "%lu", v4); lstrcatA(b, t);
            lstrcatA(b, " [+8]="); wsprintfA(t, "%lu", v8); lstrcatA(b, t);
            lstrcatA(b, " [+12]="); wsprintfA(t, "%lu", v12); lstrcatA(b, t);
            lstrcatA(b, (v12 == 1024) ? " [已打补丁]" : " [原值]");
        } else {
            lstrcatA(b, " (字体对象未缓存)");
        }
    }
    lstrcatA(b, "\r\n");
    HKLOG(b);
}

/* ============================================================
 * ★★★ 修复2（2026-10-08）：Reset 后【立即】重打补丁，不等 SetTexture
 *   问题：Reset 时我们撤回参数（+12 回到 128、表恢复原表）→ 显示原版小字。
 *   实测：Reset 后游戏 SetTexture 调用数 = 0（其状态缓存失效但不重绑），
 *         所以旧门禁（等 SetTexture 确认）永远不开 → 参数永远停留在 128。
 *   修复：Reset 后由 CreateTexture 抓到游戏重建的字体纹理，再由
 *         Present 的【绑定看门狗】在确认 stage0 上是该纹理时
 *         同帧完成「换大图集 + 重打参数补丁」。
 *   （watchdog_swap 定义在下方绘制钩子区，这里只做前向声明） */
static int watchdog_swap(void* dev);
static void restore_frame_end(void);   /* c44 帧末还原（定义在下方） */
static void CreateBigTexture(void* dev);      /* c72 实验：延迟创建需要前向声明 */
static volatile long g_expDeferCreate = 0;    /* c72 实验：1=已推迟，等首帧 Present 创建 */
static void d6_dump(void);                    /* c82_probe：#6 诊断汇总（定义在下方） */

static long WINAPI Hook_Present(void* self, const void* src, const void* dst,
                                void* hwndOverride, const void* dirty) {
    g_frames++;
    g_ckpt = "P:1-rescan";
    /* ★ c82_probe（#6 诊断）：每 24000 帧一条 [D6] 汇总（只读统计）
     *   c86（2026-10-10）：改为 D6_DIAG 开关。零 API 调用、仅读内存表格，
     *   默认 1（#6 正在排查中）；发布定版时编译加 -DD6_DIAG=0 关闭。 */
#ifndef D6_DIAG
#define D6_DIAG 1
#endif
#if D6_DIAG
    if (g_frames > 0 && (g_frames % 24000) == 0) d6_dump();
#endif
#ifdef EXP_DEFER_CREATE
    /* ★ c72 实验（2026-10-09）：把大纹理创建从 CreateDevice 重入窗口推迟到
     *   首帧 Present。假设：在游戏 CreateDevice 调用栈内创建纹理
     *   会导致后续 Reset INVALIDCALL。 */
#ifndef DEFER_AT_FRAME
#define DEFER_AT_FRAME 1
#endif
    if (g_expDeferCreate && g_frames >= DEFER_AT_FRAME) {
        g_expDeferCreate = 0;
        HKLOG("[EXP-c72] 首帧 Present：创建大纹理\r\n");
        CreateBigTexture(self);
    }
#endif
#ifdef EXP_EARLY_RELEASE
    /* ★ c71 实验（2026-10-09）：设备【健康期】提前释放 g_bigTex。
     *   假设：Reset 失败的条件是「设备丢失瞬间 T 仍被持有」；
     *   若本版 Reset 成功 => 修复方向 = 丢失去前释放大纹理（或用 WM 子类化提前释放）。 */
    if (g_bigTex && g_frames >= 2) {
        void** bv = *(void***)g_bigTex;
        unsigned long rc = 0xFFFFFFFFUL;
        if (bv && bv[2]) rc = ((PFN_COMRel)bv[2])(g_bigTex);
        {
            char eb[220], et[64]; eb[0] = 0;
            lstrcatA(eb, "[EXP-c71] 健康期提前释放 g_bigTex rc="); wsprintfA(et, "%lu", rc); lstrcatA(eb, et);
            lstrcatA(eb, " f="); wsprintfA(et, "%ld", g_frames); lstrcatA(eb, et);
            lstrcatA(eb, "\r\n");
            HKLOG(eb);
        }
        g_bigTex = NULL;
    }
#endif
    /* ★★ Reset 后重建：每帧重试，直到在 CreateTexture 钩子里重新抓到字体纹理。
     *   为什么要在 Present 里做：实测 Reset 后游戏【不再调用 SetTexture】，
     *   靠 SetTexture 触发重扫会永远等不到 -> 必须在每帧入口主动补。
     *   条件：处于重建状态；此时我们的 MANAGED 大纹理指针仍有效（Reset 不改它）。 */
    if (g_postResetNeedRebuild) {
        /* ★★★ 2026-10-08：这里【不再重扫】。
         *   旧实现在此每 60 帧调用 patch_font_obj()（= 全进程扫描）：
         *     (a) 主线程被反复阻塞 -> 用户感受到的卡顿；
         *     (b) 扫描撞释放页 -> AV 崩溃（23 个转储同一现场）。
         *   补丁的恢复完全由 SetTexture 门禁驱动：游戏重新绑定字体纹理时
         *   Hook_SetTexture 会立即用【缓存地址】重打补丁（零扫描、零成本）。
         *   这里只做状态汇报，外加 Reset 后 SetTexture 活动统计。 */
        if (g_fontTex && g_bigTex && g_fontTexBound) {
            g_postResetNeedRebuild = 0;      /* 新纹理已识别且已绑定 -> 重建完成 */
            HKLOG("[RESET] 重建完成：新字体纹理已识别，补丁已恢复\r\n");
        } else if ((g_frames % 200) == 0) {
            char b[220], t[64];
            b[0] = 0;
            lstrcatA(b, "[RESET] 等待重建中 f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
            lstrcatA(b, " fontTex="); wsprintfA(t, "%p", g_fontTex); lstrcatA(b, t);
            lstrcatA(b, " bigTex="); wsprintfA(t, "%p", g_bigTex); lstrcatA(b, t);
            lstrcatA(b, "\r\n");
            HKLOG(b);
        }
        /* ★ 诊断（2026-10-08）：Reset 后统计 SetTexture 活动。
         *   ★ 修正：打印移到 [Present] 的 100 帧块（不依赖 g_postResetNeedRebuild，
         *     否则 CreateTexture 抢跑清标志后永远看不到统计）。 */
    }
    g_ckpt = "P:2-present";
    /* ★★★ c45：绑定看门狗（只在【需要恢复】时运行，不是每帧）。
     *   两种需要恢复的情况：
     *     a) Reset 之后（g_postResetNeedRebuild）：游戏用旁路绑图集，SetTexture 收不到
     *        -> 看门狗是唯一能在同一帧完成「开闸+换纹理+打补丁」的路径；
     *     b) 参数已开闸但 stage0 被游戏换回原图集 -> 需要换回大图集。
     *   频率：每 2 帧一次（足够快，且 GetTexture 开销可忽略）。
     *   ★ 安全：watchdog_swap 内部先 TestCooperativeLevel，设备丢失时直接返回，
     *     绝不调用任何纹理 API（c41 事故的铁律）。 */
    /* ★ c91 修复（缺陷 A）：外层条件补上 g_rearmPending。
     *   原条件漏用它 -> CreateTexture 清掉 g_postResetNeedRebuild 后，
     *   若游戏不调 SetTexture（g_fontTexBound 保持 0），外层 (0||0)=FALSE，
     *   整个看门狗块被跳过 -> Reset 后永不恢复（实测：watchdog 进入=0）。 */
    if ((g_postResetNeedRebuild || g_fontTexBound || g_rearmPending) && (g_frames % 2) == 0) {
        g_ckpt = "P:watch";
        /* ★ c64 降频：恢复期（待重建/待重打补丁）保持每 2 帧灵敏响应；
         *   稳定态（已绑定且无待办）降为每 600 帧巡检 -> GetTexture 调用量降 300 倍 */
        if (g_postResetNeedRebuild || g_rearmPending) {
            if ((g_frames % 2) == 0) watchdog_swap(self);
        } else if ((g_frames % 600) == 0) {
            watchdog_swap(self);
        }
        g_ckpt = "P:2-present";
    }
    /* ★★★ vtable 自愈（最高优先级）：Reset 后 vt[65] 会被恢复成原版 d3d9 函数，
     *   导致游戏所有 SetTexture 绑定绕过我们（= 切屏后字体变小/竖条的根因）。
     *   每 2 帧检查一次并重新打上我们的钩子。 */
    {
        static long lastHeal = 0;
        if (g_frames - lastHeal >= 2) {
            lastHeal = g_frames;
            g_ckpt = "P:heal";
            vtable_heal(self);
            g_ckpt = "P:2-present";
        }
    }
    /* ★★★ 帧末「还原」（c44 核心）：
     *   本帧我们做过的干预（换纹理 / 改参数）在这里全部撤销，
     *   让游戏在下一帧开头看到的状态与它自己的预期完全一致。
     *   —— 这是解决"游戏内竖条 / 分辨率切换整屏图集"的关键：
     *      游戏的"陈旧缓存绘制"（不再重新绑定纹理的那些 draw）会采样本帧末
     *      游戏自己的绑定，而不是我们的大图集；参数也回到原值。 */
    restore_frame_end();
    /* ★ 状态探针：每 200 帧输出「stage0 实际绑定 + 字体对象字段」 */
    if ((g_frames % 600) == 0) probe_state(self, "periodic");   /* ★ c64 降频：减少 GetTexture 调用（泄漏控制） */
    /* 步骤一：每 100 帧报一次「本 100 帧内字体纹理绑定次数」 */
    if (g_frames <= 3 || g_frames % 100 == 0) {
        char b[320], t[64];
        b[0] = 0;
        lstrcatA(b, "[Present] dev="); wsprintfA(t, "%p", self); lstrcatA(b, t);
        lstrcatA(b, " frame="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
        lstrcatA(b, " font绑定/本100帧="); wsprintfA(t, "%ld", g_bindTick); lstrcatA(b, t);
        lstrcatA(b, " draw(DIP/UP/IUP)="); wsprintfA(t, "%ld", g_drawCntDIP); lstrcatA(b, t);
        lstrcatA(b, "/"); wsprintfA(t, "%ld", g_drawCntUP); lstrcatA(b, t);
        lstrcatA(b, "/"); wsprintfA(t, "%ld", g_drawCntIUP); lstrcatA(b, t);
        lstrcatA(b, " DP="); wsprintfA(t, "%ld", g_drawCntDP); lstrcatA(b, t);
        lstrcatA(b, " JIT="); wsprintfA(t, "%ld", g_jitN); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
        g_bindTick = 0;
        /* ★ c93：百帧窗口结算 —— D2 门禁的场景判据
         *  （本引擎 UI/菜单走 DP，DIP 只属于 3D 场景；实测 UI 窗口 DIP 增量恒 0） */
        {
            long d = g_drawCntDIP - g_dipWindowBase;
            g_dipWinZero    = (d == 0);
            g_dipWindowBase = g_drawCntDIP;
        }
    }
    /* ★ 诊断：Reset 后窗口内的 SetTexture 活动汇总（每 100 帧一次） */
    if (g_rstDiagUntil && g_frames < g_rstDiagUntil && (g_frames % 100) == 0) {
        char b2[260], t2[64];
        b2[0] = 0;
        lstrcatA(b2, "[POST-RST] f="); wsprintfA(t2, "%ld", g_frames); lstrcatA(b2, t2);
        lstrcatA(b2, " 窗口内SetTexture总="); wsprintfA(t2, "%ld", g_rstStTotal); lstrcatA(b2, t2);
        lstrcatA(b2, " 命中g_fontTex="); wsprintfA(t2, "%ld", g_rstStFont); lstrcatA(b2, t2);
        lstrcatA(b2, " 不同纹理数="); wsprintfA(t2, "%d", g_rstUniqN); lstrcatA(b2, t2);
        lstrcatA(b2, "\r\n");
        HKLOG(b2);
    }
    return g_orig_Present(self, src, dst, hwndOverride, dirty);
}

/* ============================================================
 *  第四步：device vt[65] SetTexture 只读记录（尺寸用 texVt[17] GetLevelDesc）
 * ============================================================ */
typedef long (WINAPI *PFN_GetLevelDesc)(void*, unsigned int, void*);

/* D3DSURFACE_DESC: 32 字节 */
typedef struct {
    unsigned long Format;            /* +0  */
    unsigned long Type;              /* +4  */
    unsigned long Usage;             /* +8  */
    unsigned long Pool;              /* +12 */
    unsigned long MultiSampleType;   /* +16 */
    unsigned long MultiSampleQuality;/* +20 */
    unsigned int  Width;             /* +24 */
    unsigned int  Height;            /* +28 */
} SURF_DESC;

static PFN_SetTexture g_orig_SetTexture = NULL;

/* ★ CreateTexture 原函数指针（Hook_CreateTexture 用；定义在下方）。
 *   放这里是因为 CreateBigTexture 也要用它（避免走自己的 hook）。 */
typedef long (WINAPI *PFN_CreateTex2)(void*, unsigned int, unsigned int, unsigned int,
                                      unsigned long, unsigned long, unsigned long,
                                      void**, void*);
static PFN_CreateTex2 g_orig_CreateTex = NULL;

/* ---------- 3B：大纹理替换 ---------- */
/* ★ c75 实验开关：大纹理池。默认 0=DEFAULT(现行)；1=MANAGED（干净环境重测）；
 *   历史 c49/c50 的"MANAGED 也失败"结论来自探针污染时代，需在 P1_DIAG_PROBES=0 下复测。 */
#ifndef BIGTEX_POOL
#define BIGTEX_POOL 0
#endif
/* ★ 2026-10-08 批2 扩容：TEX_H 1024 -> 2048（图集容量 888 -> 1920，
   全量 374 条译文的 1128 个非 ASCII 字符需要 >1024 高。机制零改动，
   仍是「+12 字段 + 纹理尺寸」同一常量驱动）。
   ★ 2026-10-09 扩容：TEX_H 2048 -> 4096（POT 标准高度；容量 3960，
   当前 1344 字、全量预测 ~2300 字均富余；消除 NPOT 位运算假设风险）。 */
#if EXP_MODE == 20 || EXP_MODE == 21
#ifndef TEX_W
#define TEX_W 512
#endif
#ifndef TEX_H
#define TEX_H 4096
#endif
#define RAWPATH   "__RUNTIME__"
#define TABLEPATH "__RUNTIME__"
#else
#define TEX_W 1024
#define TEX_H 1024
#define RAWPATH   "__RUNTIME__"
#define TABLEPATH "__RUNTIME__"
#endif
#define BIG_SIZE 1024
static void* g_fontTex  = NULL;   /* 自动识别的游戏字体纹理（不写死地址） */
/* ★★ 字体绘制调用点（exe RVA）。0 = 尚未探明，此时退化为「只看第一张纹理」。
 *   探明后，替换必须同时满足「调用者 RVA 命中此处」才执行，避免误替换
 *   恰好也是 512x128 的地面/场景贴图。 */
static DWORD g_fontCallRVA = 0;
static int   g_swapLogN    = 0;   /* 替换诊断计数 */

/* ★ 所有 512x128 的字体纹理（可能不止一张），全部替换成大纹理 */
static void* g_fontTexes[8] = {0};
static int   g_fontTexN = 0;
static int   is_font_tex(void* tex) {
    for (int i = 0; i < g_fontTexN; i++) if (g_fontTexes[i] == tex) return 1;
    return 0;
}

/* ══════════ c82_probe（2026-10-10，#6 主菜单启动竞态诊断）══════════
 * 【只加日志，不改任何行为】目的：区分
 *   A. 游戏真的没画文字（无任何 512x128 绑定）
 *   B. 画了但 hook 没认出来（有 512x128 被绑定但不是 g_fontTex）
 *   C. 绑定的就是 g_fontTex 但没替换（补丁/交换条件问题）
 * 统计口径：
 *   - d6_note ：Hook_CreateTexture 里每个新建的 512x128 登记（lv=创建 levels）
 *   - d6_bind ：Hook_SetTexture 全量调用计数（不受"首见一次"日志限制）
 *   - d6_tex  ：g_fontTex 每次改写（旧值/新值/帧号/lv）
 *   - d6_dump ：每 24000 帧一条 [D6] 汇总（列出全部条目 + 闸门状态）
 * ══════════════════════════════════════════════════════════════════ */
#define D6_MAX 8
typedef struct { void* ptr; long bind; long lv; } D6Rec;
static D6Rec g_d6[D6_MAX];
static int   g_d6n     = 0;
static long  g_d6NoteN = 0;

static void d6_note(void* p, unsigned long w, unsigned long h, long lv) {
    (void)w; (void)h;
    if (!p) return;
    for (int i = 0; i < g_d6n; i++) if (g_d6[i].ptr == p) return;
    if (g_d6n < D6_MAX) {
        g_d6[g_d6n].ptr = p; g_d6[g_d6n].bind = 0; g_d6[g_d6n].lv = lv;
        g_d6n++;
        if (g_d6NoteN < 60) {
            g_d6NoteN++;
            char b[220], t[64]; b[0] = 0;
            lstrcatA(b, "[D6-NEW] 512x128 tex="); wsprintfA(t, "%p", p); lstrcatA(b, t);
            lstrcatA(b, " lv="); wsprintfA(t, "%ld", lv); lstrcatA(b, t);
            lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
            lstrcatA(b, "\r\n"); HKLOG(b);
        }
    }
}

static void d6_bind(void* p, unsigned long stage) {
    for (int i = 0; i < g_d6n; i++) if (g_d6[i].ptr == p) {
        if (g_d6[i].bind == 0) {
            char b[240], t[64]; b[0] = 0;
            lstrcatA(b, "[D6-FIRST] tex="); wsprintfA(t, "%p", p); lstrcatA(b, t);
            lstrcatA(b, " st="); wsprintfA(t, "%lu", stage); lstrcatA(b, t);
            lstrcatA(b, " lv="); wsprintfA(t, "%ld", g_d6[i].lv); lstrcatA(b, t);
            lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
            lstrcatA(b, (p == g_fontTex) ? " [=g_fontTex]" : " [!=g_fontTex]");
            lstrcatA(b, "\r\n"); HKLOG(b);
        }
        g_d6[i].bind++;
        return;
    }
}

static void d6_tex(const char* site, void* oldp, void* newp) {
    long lv = -1;
    for (int i = 0; i < g_d6n; i++) if (g_d6[i].ptr == newp) { lv = g_d6[i].lv; break; }
    char b[260], t[80]; b[0] = 0;
    lstrcatA(b, "[D6-TEX] 改写["); lstrcatA(b, site);
    lstrcatA(b, "] 旧="); wsprintfA(t, "%p", oldp); lstrcatA(b, t);
    lstrcatA(b, " 新="); wsprintfA(t, "%p", newp); lstrcatA(b, t);
    lstrcatA(b, " lv="); wsprintfA(t, "%ld", lv); lstrcatA(b, t);
    lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
    lstrcatA(b, "\r\n"); HKLOG(b);
}

static void d6_dump(void) {
    char b[460], t[80]; b[0] = 0;
    lstrcatA(b, "[D6] f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
    lstrcatA(b, " ft="); wsprintfA(t, "%p", g_fontTex); lstrcatA(b, t);
    lstrcatA(b, " bound="); wsprintfA(t, "%ld", g_fontTexBound); lstrcatA(b, t);
    lstrcatA(b, " big="); wsprintfA(t, "%p", g_bigTex); lstrcatA(b, t);
    lstrcatA(b, " subOn="); wsprintfA(t, "%d", g_subOn); lstrcatA(b, t);
    lstrcatA(b, " | ");
    for (int i = 0; i < g_d6n; i++) {
        wsprintfA(t, "%p x%ld lv%ld | ", g_d6[i].ptr, g_d6[i].bind, g_d6[i].lv);
        lstrcatA(b, t);
    }
    lstrcatA(b, "\r\n"); HKLOG(b);
}

static DWORD g_fontObj  = 0;      /* 字体对象基址（+8宽 +12高） */
static DWORD g_fontObjs[16] = {0};/* grp1（字形高24）到处副本，要换表+改宽高 */
static int   g_fontObjN = 0;
/* ★ 所有「图集 512x128」的字体组（字形高 24/35/39）都要改宽高，
 *   否则它们的 UV 归一化仍按 512/128，采样位置错乱（表现为白条/错字）。
 *   只有 grp1（24）额外换新字形表。 */
static DWORD g_obj512[32] = {0};
static DWORD g_obj512H[32] = {0};
static int   g_obj512N = 0;

/* ★ 全量字体组（用名字锚点扫出来的所有组） */
static DWORD g_allObj[32] = {0};   /* 结构地址 */
static DWORD g_allW[32]   = {0};   /* 图集宽 */
static DWORD g_allH[32]   = {0};   /* 图集高 */
static DWORD g_allGh[32]  = {0};   /* 字形高 */
static DWORD g_allCnt[32] = {0};   /* 记录数 */
static int   g_allN       = 0;

/* ★ 已处理过的对象地址（增量：只处理新出现的组） */
static DWORD g_doneH[32] = {0};    /* 已改高的对象 */
static int   g_doneHN    = 0;
static DWORD g_doneT[32] = {0};    /* 已换表的对象 */
static int   g_doneTN    = 0;
static int is_done(DWORD* arr, int n, DWORD a) {
    for (int i = 0; i < n; i++) if (arr[i] == a) return 1;
    return 0;
}

/* ★★ Reset 前后「同进同退」用的原值快照（2026-10-07 晚，正确修复方向）。
 *   原则：改字体对象的字段之前，先把原值存下来；Reset 之前把字段恢复成原值，
 *   让游戏自己 Reset 时看到的是一致的、原始的字体状态；Reset 后再重新打补丁。
 *   记录的是我们【改之前】读到的原始值（即游戏启动时的真值）。 */
typedef struct {
    DWORD obj;      /* 字体组结构地址 */
    DWORD o0;       /* +0  原表指针 */
    DWORD o4;       /* +4  原记录数 */
    DWORD o8;       /* +8  原图集宽 */
    DWORD o12;      /* +12 原图集高 */
} FontOrig;
static FontOrig g_fontOrig[16];
static int      g_fontOrigN = 0;
static void record_orig(DWORD obj) {
    for (int i = 0; i < g_fontOrigN; i++) if (g_fontOrig[i].obj == obj) return;
    if (g_fontOrigN >= 16) return;
    FontOrig* fo = &g_fontOrig[g_fontOrigN++];
    fo->obj = obj;
    fo->o0  = *(DWORD*)(obj + 0);
    fo->o4  = *(DWORD*)(obj + 4);
    fo->o8  = *(DWORD*)(obj + 8);
    fo->o12 = *(DWORD*)(obj + 12);
    char b[200], t[64];
    b[0] = 0;
    lstrcatA(b, "[ORIG] 记录原值 obj=0x"); wsprintfA(t, "%lX", obj); lstrcatA(b, t);
    lstrcatA(b, " 表=0x"); wsprintfA(t, "%lX", fo->o0); lstrcatA(b, t);
    lstrcatA(b, " 记录="); wsprintfA(t, "%lu", fo->o4); lstrcatA(b, t);
    lstrcatA(b, " 图集="); wsprintfA(t, "%lux%lu", fo->o8, fo->o12); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
}
/* Reset 前把所有改过的字体组字段恢复成原值 */
static void restore_all_orig(void) {
#if TEX_INTERVENE == 0
    return;      /* c46 二分实验：不改任何字段，也不写日志 */
#endif
    int n = 0;
    for (int i = 0; i < g_fontOrigN; i++) {
        DWORD obj = g_fontOrig[i].obj;
        DWORD old = 0;
        if (!VirtualProtect((void*)obj, 16, PAGE_READWRITE, &old)) continue;
        *(DWORD*)(obj + 0)  = g_fontOrig[i].o0;
        *(DWORD*)(obj + 4)  = g_fontOrig[i].o4;
        *(DWORD*)(obj + 8)  = g_fontOrig[i].o8;
        *(DWORD*)(obj + 12) = g_fontOrig[i].o12;
        VirtualProtect((void*)obj, 16, old, &old);
        n++;
    }
    char b[160], t[64];
    b[0] = 0;
    lstrcatA(b, "[RESET] Reset 前已恢复原字段的字体组数 = "); wsprintfA(t, "%d", n); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
}

/* ★ 实验开关见文件顶部 #define EXP_MODE */

typedef long (WINAPI *PFN_CreateTex)(void*, unsigned int, unsigned int, unsigned int,
                                     unsigned long, unsigned long, unsigned long, void**, void*);
typedef long (WINAPI *PFN_LockRect)(void*, unsigned int, void*, const void*, unsigned long);
typedef long (WINAPI *PFN_UnlockRect)(void*, unsigned int);

typedef struct { long Pitch; void* pBits; } LOCKED_RECT;

/* ============================================================
 * ★★★ 安全内存读取（2026-10-08 崩溃修复的核心）
 *   旧实现对候选地址直接解引用 *(DWORD*)(p+44)。游戏其它线程可能在
 *   VirtualQuery 报告「已提交可读」之后把该页释放掉，此时解引用 ->
 *   STATUS_ACCESS_VIOLATION，崩溃点固定为 version.dll+0x40EC
 *   （23 个转储的共同现场）。改为 ReadProcessMemory 分块读：
 *   该 API 对不可读页面只返回失败/部分字节，物理上不可能再崩。
 * ============================================================ */
#define SCAN_CHUNK 262144
static BYTE  g_scanBuf[SCAN_CHUNK];      /* 静态缓冲，不放栈上 */
static HANDLE g_selfProc = NULL;

static DWORD safe_read(DWORD src, void* dst, DWORD len) {
    if (!g_selfProc) {
        g_selfProc = GetCurrentProcess();
        if (!g_selfProc) return 0;
    }
    SIZE_T got = 0;
    ReadProcessMemory(g_selfProc, (LPCVOID)(DWORD_PTR)src, dst, (SIZE_T)len, &got);
    return (DWORD)got;                   /* 失败时 got 可能为 0 或部分字节 */
}

/* ★ 一次性发现闸门：
 *   字体组对象地址在【整个进程生命周期内稳定不变】（跨 5 次 Reset 实测同址
 *   0x95F688C）。因此全进程扫描只允许在首次发现时执行；
 *   之后所有「重扫」需求（定时 / Reset 后 / 换纹理后）一律复用缓存地址。
 *   —— 周期性全进程扫描（每 600 帧 + Reset 后每 60 帧）正是用户感受到的
 *      「操作卡顿、按几次才行」的直接来源，必须根除。 */
static int g_discoveryDone  = 0;   /* 1 = 已成功发现（grp1），永不再扫 */
static int g_discoveryTries = 0;   /* 扫描尝试次数（上限 8，防病态循环） */
static int g_discoveryRedo  = 0;   /* 重新发现次数（上限 1：仅当缓存地址被证伪时） */

/* 扫描 exe 镜像找字体对象：+8=512 +12=128 +16=24(字形高) 且 +4=记录数 +0=表指针 */
static DWORD find_font_obj(void) {
#if TEX_INTERVENE == 0
    return 0;      /* c46 二分实验：不做任何内存扫描 */
#endif
    HMODULE base = GetModuleHandleA(NULL);
    if (!base) return 0;
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)base;
    IMAGE_NT_HEADERS32* nt = (IMAGE_NT_HEADERS32*)((char*)base + dos->e_lfanew);
    DWORD start = (DWORD)base, size = nt->OptionalHeader.SizeOfImage;
    DWORD best = 0;
    int shown = 0;
    DWORD a = start;
    g_ckpt = "S:1-enter";
    /* ★ exe 镜像签名扫描已废弃：它只能看到 exe 里的副本，且常命中假阳性
     *   （+8/+12 恰好为 512/128 的无关数据）。下面直接用名字锚点扫全进程，
     *   一次就能把 holstein24/30、lucida10 等所有组收全，且可靠得多。 */
    (void)best; (void)shown; (void)a; (void)start; (void)size;

    /* ============================================================
     * ★ 锚点扫描：字体定义结构的 +40 指向 "font_xxx" 名字字符串。
     *   结构布局（56 字节，以 holstein24 @0x0959688C 实测为例）：
     *     +0  表指针  +4 记录数  +8 图集宽  +12 图集高
     *     +16 字形高  +20 行带高  +24 标志(=1)  +28 指针
     *     +40 名字指针(指向 "font_xxx")  +44 == 128  +48 名字指针(副本)
     *   实测：holstein24 24->26、holstein30 35->40、lucida10 39->45，
     *   所以行带高不能写死 gh+2。这比按尺寸猜签名可靠得多，能一次抓全所有组。
     * ============================================================ */
    HKLOG("[3B?] 名字锚点 全进程扫描所有字体组...\r\n");
    shown = 0;
    a = 0x10000;
    while (a < 0x7F000000) {
        g_ckpt = "S:2-vq";
        MEMORY_BASIC_INFORMATION mbi;
        if (!VirtualQuery((void*)a, &mbi, sizeof(mbi))) break;
        DWORD rbase = (DWORD)mbi.BaseAddress;
        DWORD rend  = rbase + (DWORD)mbi.RegionSize;
        if (rend <= rbase) break;                    /* 防 RegionSize 回绕 */
        if (mbi.State == MEM_COMMIT && mbi.Type == MEM_PRIVATE &&
            !(mbi.Protect & PAGE_NOACCESS) &&
            !(mbi.Protect & 0x100 /*PAGE_GUARD*/) &&
            (mbi.Protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_EXECUTE_READWRITE |
                            PAGE_EXECUTE_READ | PAGE_EXECUTE_WRITECOPY))) {
            g_ckpt = "S:3-scanregion";
            /* ★ 安全分块扫描：ReadProcessMemory 读入 g_scanBuf，本地扫描。
             *   块间重叠 60 字节（> 结构 56 字节），避免记录跨块漏检。 */
            DWORD p = rbase;
            while (p + 56 <= rend) {
                DWORD want = rend - p;
                if (want > SCAN_CHUNK) want = SCAN_CHUNK;
                DWORD got = safe_read(p, g_scanBuf, want);
                if (got < 60) { p += (got ? got : 4); continue; }
                for (DWORD i = 0; i + 56 <= got; i += 4) {
                    /* 快速排除：+44 必须是 128（名字长度字段实测恒为 128） */
                    if (*(DWORD*)(g_scanBuf + i + 44) != 128) continue;
                    DWORD addr = p + i;
                    DWORD np = *(DWORD*)(g_scanBuf + i + 40);   /* 名字指针 */
                    if (np < 0x10000 || np >= 0x7F000000) continue;
                    /* 安全读名字（最多 44 字节）并验证是可读 ASCII */
                    char nbuf[48];
                    DWORD nn = safe_read(np, nbuf, 44);
                    if (nn < 8) continue;
                    nbuf[44] = 0;
                    int okname = 1;
                    for (int k = 0; k < 8; k++) {
                        char c = nbuf[k];
                        if (!c) break;
                        if (c < 32 || c > 126) { okname = 0; break; }
                    }
                    if (!okname) continue;
                    DWORD w8  = *(DWORD*)(g_scanBuf + i + 8);
                    DWORD h12 = *(DWORD*)(g_scanBuf + i + 12);
                    DWORD gh  = *(DWORD*)(g_scanBuf + i + 16);
                    DWORD rh  = *(DWORD*)(g_scanBuf + i + 20);
                    DWORD cnt = *(DWORD*)(g_scanBuf + i + 4);
                    /* 有效性：行带高 rh 略大于字形高 gh（实测 holstein24=24->26(+2)、
                     *   holstein30=35->40(+5)、lucida10=39->45(+6)，不可写死 +2）。 */
                    if (rh < gh || rh > gh + 12) continue;
                    if (gh < 6 || gh > 64) continue;
                    if (w8 < 16 || w8 > 4096 || h12 < 8 || h12 > 4096) continue;
                    if (cnt == 0 || cnt > 2000) continue;
                    /* 收集 */
                    int dup = 0;
                    for (int k = 0; k < g_allN; k++) if (g_allObj[k] == addr) { dup = 1; break; }
                    if (dup) continue;
                    if (g_allN < 32) {
                        g_allObj[g_allN] = addr;
                        g_allW[g_allN] = w8;
                        g_allH[g_allN] = h12;
                        g_allGh[g_allN] = gh;
                        g_allCnt[g_allN] = cnt;
                        g_allN++;
                    }
                    char nb[64], b2[320], t2[64];
                    int nl = 0;
                    for (int k = 0; k < 40; k++) {
                        char c = nbuf[k];
                        if (!c) break;
                        nb[nl++] = c;
                    }
                    nb[nl] = 0;
                    b2[0] = 0;
                    lstrcatA(b2, "[FONT组] "); lstrcatA(b2, nb);
                    lstrcatA(b2, " @0x"); wsprintfA(t2, "%lX", addr); lstrcatA(b2, t2);
                    lstrcatA(b2, " 表=0x"); wsprintfA(t2, "%lX", *(DWORD*)(g_scanBuf + i)); lstrcatA(b2, t2);
                    lstrcatA(b2, " 记录="); wsprintfA(t2, "%lu", cnt); lstrcatA(b2, t2);
                    lstrcatA(b2, " 图集="); wsprintfA(t2, "%lux%lu", w8, h12); lstrcatA(b2, t2);
                    lstrcatA(b2, " 字形高="); wsprintfA(t2, "%lu", gh); lstrcatA(b2, t2);
                    lstrcatA(b2, "\r\n");
                    HKLOG(b2);

                    /* 记录 grp1（含汉字的组：字形高 24 且记录数 >= 100）—— 去重 */
                    if (gh == 24 && cnt >= 100 && g_fontObjN < 16) {
                        int dup2 = 0;
                        for (int k = 0; k < g_fontObjN; k++) if (g_fontObjs[k] == addr) { dup2 = 1; break; }
                        if (!dup2) {
                            g_fontObjs[g_fontObjN++] = addr;
                            if (!best) best = addr;
                        }
                    }
                    /* 图集尺寸为 512x128 的组 -> 需要改高为 1024（去重） */
                    if (w8 == 512 && h12 == 128 && g_obj512N < 32) {
                        int dup3 = 0;
                        for (int k = 0; k < g_obj512N; k++) if (g_obj512[k] == addr) { dup3 = 1; break; }
                        if (!dup3) { g_obj512[g_obj512N] = addr; g_obj512H[g_obj512N] = gh; g_obj512N++; }
                    }
                    if (g_allN >= 32) return best;
                }
                /* 前进：重叠 60 字节防跨块漏检 */
                p += (got > 60) ? (got - 60) : got;
            }
        }
        if (rend <= a) break;
        a = rend;
    }
    return best;
}

/* ============================================================
 *  ★ 诊断：dump 字体对象全字段（+0 .. +40，含布局参数）
 * ============================================================ */
static void dump_font_obj(DWORD o, const char* tag) {
    char b[512], t[64];
    DWORD old = 0;
    if (!VirtualProtect((void*)o, 48, PAGE_READWRITE, &old)) return;
    b[0] = 0;
    lstrcatA(b, tag);
    lstrcatA(b, " @0x"); wsprintfA(t, "%lX", o); lstrcatA(b, t); lstrcatA(b, ":");
    for (int k = 0; k < 12; k++) {
        wsprintfA(t, " +%d=%lu", k * 4, *(DWORD*)(o + k * 4));
        lstrcatA(b, t);
    }
    lstrcatA(b, "\r\n");
    VirtualProtect((void*)o, 48, old, &old);
    HKLOG(b);
}

/* ============================================================
 * ★ c44：字形表只加载一次 + 帧内快速打补丁 / 还原
 * ============================================================ */
static DWORD use_table_cache(void) {
    if (g_tblPtr) return g_tblPtr;
    HANDLE tf = CreateFileA(g_tblPath, GENERIC_READ, FILE_SHARE_READ, NULL,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (tf == INVALID_HANDLE_VALUE) return 0;
    DWORD fsz = GetFileSize(tf, NULL);
    if (fsz > 0 && fsz < 65536) {
        void* mem = VirtualAlloc(NULL, (fsz + 4095) & ~4095,
                                 MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        if (mem) {
            DWORD got = 0;
            ReadFile(tf, mem, fsz, &got, NULL);
            g_tblPtr = (DWORD)mem;
            g_tblCnt = fsz / 10;
        }
    }
    CloseHandle(tf);
    return g_tblPtr;
}

/* 帧内快速打补丁：+12=1024（仅字形高 24 的组）+ 换新表。幂等、轻量（无文件 IO、无扫描）。 */
static void apply_params_fast(void) {
#if TEX_INTERVENE == 0
    /* c46 二分实验：完全不改任何字体组字段 */
    return;
#endif
#if TEX_CREATE_ONLY == 1
    /* c49 二分实验：只创建纹理，不改任何字体组字段 */
    return;
#endif
    if (!g_discoveryDone) {
        patch_font_obj();          /* 尚未发现字体组 -> 走完整流程（只此一次） */
        return;
    }
    ensure_table();
    if (!g_tblPtr) return;
    for (int i = 0; i < g_obj512N; i++) {
        DWORD obj = g_obj512[i];
        DWORD gh = 0;
        if (safe_read(obj + 16, &gh, 4) < 4) continue;
        if (gh != 24) continue;                       /* 只动菜单组（holstein24） */
        record_orig(obj);
        DWORD old = 0;
        if (!VirtualProtect((void*)obj, 16, PAGE_READWRITE, &old)) continue;
        *(DWORD*)(obj + 12) = TEX_H;                  /* 高 -> 4096 */
        VirtualProtect((void*)obj, 16, old, &old);
    }
    for (int i = 0; i < (g_fontObjN ? g_fontObjN : (g_fontObj ? 1 : 0)); i++) {
        DWORD obj = g_fontObjN ? g_fontObjs[i] : g_fontObj;
        if (!obj) continue;
        record_orig(obj);
        DWORD old = 0;
        if (!VirtualProtect((void*)obj, 16, PAGE_READWRITE, &old)) continue;
        *(DWORD*)(obj + 0) = g_tblPtr;
        *(DWORD*)(obj + 4) = g_tblCnt;
        VirtualProtect((void*)obj, 16, old, &old);
    }
    if (g_patchLogN < 6) {
        g_patchLogN++;
        char pb[220], pt[64];
        pb[0] = 0;
        lstrcatA(pb, "[PATCH] 帧内打补丁: +12="); wsprintfA(pt, "%d", TEX_H); lstrcatA(pb, pt);
        lstrcatA(pb, " 表=0x"); wsprintfA(pt, "%lX", g_tblPtr); lstrcatA(pb, pt);
        lstrcatA(pb, " 组数="); wsprintfA(pt, "%d", g_obj512N); lstrcatA(pb, pt);
        lstrcatA(pb, " f="); wsprintfA(pt, "%ld", g_frames); lstrcatA(pb, pt);
        lstrcatA(pb, "\r\n");
        HKLOG(pb);
    }
}

static void ensure_table(void) { use_table_cache(); }

/* 把字体对象的图集宽/高改成 BIG_SIZE */
static void patch_font_obj(void) {
#if TEX_INTERVENE == 0
    return;      /* c46 二分实验：完全不发现、不扫描、不打补丁 */
#endif
    char b[320], t[64];
    g_ckpt = "PF:1-enter";
    /* ★★★ 一次性发现（2026-10-08）：
     *   字体组地址在进程内稳定不变（跨 5 次 Reset 实测同址），
     *   所以只允许在首次发现时做全进程扫描；此后一律复用缓存地址。
     *   旧行为（每次 patch 调用都全进程扫描）是「卡顿 + 崩溃」的根源：
     *     - Present 每 600 帧 + Reset 后每 60 帧调用 -> 周期性主线程阻塞；
     *     - 扫描撞上被其它线程释放的堆页 -> AV（version.dll+0x40EC）。 */
    if (!g_discoveryDone) {
        if (g_discoveryTries >= 8) {
            if (g_discoveryTries == 8) {
                g_discoveryTries = 9;   /* 只报一次 */
                HKLOG("[3B] 字体组发现失败（8 次尝试后放弃），保持原图集，绝不打补丁\r\n");
            }
            return;
        }
        g_discoveryTries++;
        g_fontObj = find_font_obj();
        g_ckpt = "PF:2-scanned";
        if (g_fontObjN > 0) {
            g_discoveryDone = 1;        /* grp1 已发现 -> 永久缓存，不再扫描 */
            char db[160], dt[64];
            db[0] = 0;
            lstrcatA(db, "[3B] ★ 字体组发现完成（第 "); wsprintfA(dt, "%d", g_discoveryTries); lstrcatA(db, dt);
            lstrcatA(db, " 次尝试）—— 地址已缓存，此后不再扫描\r\n");
            HKLOG(db);
            /* ★ 探针：字体对象 ±0x60 结构 dump（方案B：找纹理指针字段） */
            probe_dump_fontobj(g_fontObj, "grp1");
            if (g_fontObjN > 1) probe_dump_fontobj(g_fontObjs[1], "grp2");
            if (g_fontObjN > 2) probe_dump_fontobj(g_fontObjs[2], "grp3");
        }
    } else {
        /* 复用缓存：做一次【廉价有效性校验】（1 次安全读取，微秒级）。
         *   字体对象 +44 恒为 128（名字长度字段）。若失效（地址被证伪），
         *   允许【重新发现一次】（上限 1 次，防止病态循环重复全扫）。 */
        g_ckpt = "PF:2-cached";
        if (g_fontObj && g_discoveryRedo < 1) {
            DWORD chk = 0;
            safe_read(g_fontObj + 44, &chk, 4);
            if (chk != 128) {
                g_discoveryRedo++;
                g_discoveryDone = 0;
                g_discoveryTries = 0;
                HKLOG("[3B] 缓存字体对象失效(+44!=128) -> 允许重新发现一次\r\n");
            }
        }
    }
    b[0] = 0;
    lstrcatA(b, "[3B] 扫描结果: grp1(24)命中="); wsprintfA(t, "%d", g_fontObjN); lstrcatA(b, t);
    lstrcatA(b, "  512x128 组合计="); wsprintfA(t, "%d", g_obj512N); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
    /* ★ 全量 dump 所有已发现的字体组（含名字），用于确认还有哪些组未处理 */
    for (int i = 0; i < g_allN; i++) {
        DWORD o = g_allObj[i];
        DWORD np = *(DWORD*)(o + 40);
        char nb[48];
        int nl = 0;
        if (!IsBadReadPtr((void*)np, 4)) {
            for (int k = 0; k < 40; k++) { char c = ((char*)np)[k]; if (!c) break; nb[nl++] = c; }
        }
        nb[nl] = 0;
        b[0] = 0;
        lstrcatA(b, "  [ALL] "); lstrcatA(b, nb);
        lstrcatA(b, " @0x"); wsprintfA(t, "%lX", o); lstrcatA(b, t);
        lstrcatA(b, " 表=0x"); wsprintfA(t, "%lX", *(DWORD*)o); lstrcatA(b, t);
        lstrcatA(b, " 记录="); wsprintfA(t, "%lu", *(DWORD*)(o + 4)); lstrcatA(b, t);
        lstrcatA(b, " 图集="); wsprintfA(t, "%lux%lu", *(DWORD*)(o + 8), *(DWORD*)(o + 12)); lstrcatA(b, t);
        lstrcatA(b, " 字形高="); wsprintfA(t, "%lu", *(DWORD*)(o + 16)); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
    for (int i = 0; i < g_obj512N; i++) {
        DWORD o = g_obj512[i];
        b[0] = 0;
        lstrcatA(b, "   512x128 组 @0x"); wsprintfA(t, "%lX", o); lstrcatA(b, t);
        lstrcatA(b, " 宽="); wsprintfA(t, "%lu", *(DWORD*)(o + 8)); lstrcatA(b, t);
        lstrcatA(b, " 高="); wsprintfA(t, "%lu", *(DWORD*)(o + 12)); lstrcatA(b, t);
        lstrcatA(b, " 字形高="); wsprintfA(t, "%lu", *(DWORD*)(o + 16)); lstrcatA(b, t);
        lstrcatA(b, " 记录数="); wsprintfA(t, "%lu", *(DWORD*)(o + 4)); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
    if (!g_fontObj && g_obj512N == 0) { HKLOG("[3B] 未找到任何字体对象\r\n"); return; }

#if EXP_MODE == 10
    /* ★ 纯诊断：不改任何东西，只 dump 出来看引擎到底把布局参数放哪 */
    {
        char b[128];
        b[0] = 0;
        lstrcatA(b, "[DIAG] ===== 字体对象原始字段（未做任何修改） =====\r\n");
        HKLOG(b);
        for (int i = 0; i < g_obj512N; i++) dump_font_obj(g_obj512[i], "  [DIAG] obj");
    }
#endif

    /* 载入新的字形表（大字号汉字）到进程内存，只加载一次（c44：供帧内快速打补丁复用） */
    DWORD newTbl = use_table_cache();
    DWORD newCnt = g_tblCnt;
    g_ckpt = "PF:3-loadTable";
    b[0] = 0;
    lstrcatA(b, "[3B] 新字形表="); wsprintfA(t, "0x%lX", newTbl); lstrcatA(b, t);
    lstrcatA(b, " 记录数="); wsprintfA(t, "%lu", newCnt); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);

    /* ★★★ 原子性门禁（2026-10-07 晚，竖条根因修复）：
     *   补丁包含两半：「改字体组参数(+12=1024/+0 换表)」与「替换纹理为大图集」。
     *   两者必须同进同退。若只改了参数、纹理没换（g_fontTex 未识别 / 大纹理未就绪），
     *   引擎就会用 512x128 的纹理按高 1024 归一化采样 -> 每个字只剩一条竖条 -> 白竖条。
     *   实测证据：c36 那次运行游戏从未用 SetTexture 绑字体纹理（font绑定=0），
     *   但参数被改了 -> 主菜单全是竖条。
     *   因此：只有【已识别字体纹理 g_fontTex 且大纹理 g_bigTex 就绪】时才打补丁。 */
    if (!g_fontTex || !g_bigTex || !g_fontTexBound) {
        b[0] = 0;
        lstrcatA(b, "[3B] 门禁：条件未满足(fontTex=");
        wsprintfA(t, "%p", g_fontTex); lstrcatA(b, t);
        lstrcatA(b, " bigTex="); wsprintfA(t, "%p", g_bigTex); lstrcatA(b, t);
        lstrcatA(b, " bound="); wsprintfA(t, "%ld", g_fontTexBound); lstrcatA(b, t);
        lstrcatA(b, ")，本次不打补丁（保持原图集，避免竖条）\r\n");
        HKLOG(b);
        return;
    }

    /* ① 全部 512x128 组：按 EXP_MODE 决定是否改宽高 */
    int nd = 0;
    g_ckpt = "PF:4-patchH";
#if EXP_MODE == 1 || EXP_MODE == 3 || EXP_MODE == 4 || EXP_MODE == 6
    for (int i = 0; i < g_obj512N; i++) {
        DWORD obj = g_obj512[i];
        DWORD old = 0;
        if (!VirtualProtect((void*)obj, 16, PAGE_READWRITE, &old)) continue;
#if EXP_MODE == 3
        *(DWORD*)(obj + 8)  = BIG_SIZE;   /* 只改宽 */
#else
        *(DWORD*)(obj + 8)  = BIG_SIZE;
        *(DWORD*)(obj + 12) = BIG_SIZE;
#endif
        VirtualProtect((void*)obj, 16, old, &old);
        nd++;
    }
#endif
#if EXP_MODE == 20 || EXP_MODE == 21
    /* ★ 方案C：宽 512 不变，只把高改成 1024（增量，跳过已处理）
     * ★★ 重要修正（2026-10-07）：只改 grp1（字形高 24 = holstein24 菜单字体）。
     *    曾误把 holstein30/lucida10 的 +12 也改成 1024，但这两组绑定的仍是
     *    原始 512×128 图集纹理（我们没有给它们准备新的 512×1024 纹理），
     *    导致引擎 UV 归一化按 1024 算、实际纹理却是 128 高 -> 采样行错位 ->
     *    游戏内英文 HUD/字幕全部乱码（Press A to start... 变乱码）。
     *    故：只动 holstein24，其余组一律保持原样。 */
    for (int i = 0; i < g_obj512N; i++) {
        DWORD obj = g_obj512[i];
        if (*(DWORD*)(obj + 16) != 24) continue;   /* 只处理字形高 24 的组 */
        if (is_done(g_doneH, g_doneHN, obj)) continue;
        record_orig(obj);                          /* ★ 改之前先存原值 */
        DWORD old = 0;
        if (!VirtualProtect((void*)obj, 16, PAGE_READWRITE, &old)) continue;
        *(DWORD*)(obj + 12) = TEX_H;      /* 高 -> 4096，宽保持 512 不动 */
        VirtualProtect((void*)obj, 16, old, &old);
        if (g_doneHN < 32) g_doneH[g_doneHN++] = obj;
        nd++;
    }
#endif
    /* ② 只有 grp1(24)：换新字形表 + 记录数（增量） */
    int nt = 0;
    g_ckpt = "PF:5-swapTable";
#if EXP_MODE == 1 || EXP_MODE == 3 || EXP_MODE == 6 || EXP_MODE == 20 || EXP_MODE == 21
    for (int i = 0; i < (g_fontObjN ? g_fontObjN : (g_fontObj ? 1 : 0)); i++) {
        DWORD obj = g_fontObjN ? g_fontObjs[i] : g_fontObj;
        if (!obj || !newTbl) continue;
        if (is_done(g_doneT, g_doneTN, obj)) continue;
        DWORD old = 0;
        if (!VirtualProtect((void*)obj, 16, PAGE_READWRITE, &old)) continue;
        *(DWORD*)(obj + 0) = newTbl;
        *(DWORD*)(obj + 4) = newCnt;
        VirtualProtect((void*)obj, 16, old, &old);
        if (g_doneTN < 32) g_doneT[g_doneTN++] = obj;
        nt++;
    }
#endif
    b[0] = 0;
    lstrcatA(b, "[3B] EXP_MODE="); wsprintfA(t, "%d", EXP_MODE); lstrcatA(b, t);
    lstrcatA(b, " 已改写: 宽高="); wsprintfA(t, "%d", nd); lstrcatA(b, t);
    lstrcatA(b, " 处, 换表="); wsprintfA(t, "%d", nt); lstrcatA(b, t);
    lstrcatA(b, " 处 (表=0x"); wsprintfA(t, "%lX", newTbl); lstrcatA(b, t);
    lstrcatA(b, ", 记录数="); wsprintfA(t, "%lu", newCnt); lstrcatA(b, t);
    lstrcatA(b, ")\r\n");
    HKLOG(b);
    g_ckpt = "PF:6-done";

#if EXP_MODE == 10
    {
        char b2[128];
        b2[0] = 0;
        lstrcatA(b2, "[DIAG] ===== 改写后（模式10 应保持不变） =====\r\n");
        HKLOG(b2);
        for (int i = 0; i < g_obj512N; i++) dump_font_obj(g_obj512[i], "  [DIAG] obj");
    }
#endif
}

static void fill_default_texture(void* dev, void* dstTex);   /* 定义在本函数之后 */

/* ★★★★★ c50：创建 512×1024 大纹理（D3DPOOL_DEFAULT）
 *
 *   池类型从 MANAGED(1) 改为 DEFAULT(0) 的原因（c46/c49 二分实验铁证）：
 *   MANAGED 纹理带系统内存副本，设备丢失后 Reset 需要重建它，
 *   而我们是用 LockRect 手工灌的 -> Reset 返回 D3DERR_INVALIDCALL -> 白屏。
 *   DEFAULT 纹理由 D3D9 管理，Reset 时自动重建且【COM 指针保持不变】。
 *   代价：DEFAULT 不能 LockRect，灌数据走 SYSTEMMEM 中转 + UpdateTexture
 *        （见 fill_default_texture）。
 */
static void CreateBigTexture(void* dev) {
#if defined(EXP_NO_CREATE)
    /* ★ c80（2026-10-09 用户指定）：保留【全部】hook（vt23/vt31/vt65 安装与函数体、
     *   侦测逻辑照常），只跳过 CreateBigTexture——切分两个变量：
     *     变量1 = vtable hook 框架；变量2 = 创建 bigTex。
     *   c46（两个都关）成功 / c65~c74（两个都开）全失败 → 本版 = 只开变量1。
     *   g_bigTex 保持 NULL；替换逻辑因 g_bigTex==NULL 不触发（预期）。 */
    HKLOG("[C80] 保留 hook，跳过 CreateBigTexture\r\n");
    (void)dev;
    return;
#endif
#if TEX_INTERVENE == 0
    /* c46 二分实验：完全不创建大图集（不碰任何纹理） */
    (void)dev;
    return;
#endif
    if (!dev) return;
    void** vt = *(void***)dev;
    PFN_CreateTex ct = g_orig_CreateTex ? g_orig_CreateTex
                                        : (PFN_CreateTex)vt[23];   /* 用原函数，避开自身 hook */
    void* tex = NULL;
    /* ★★★ 池 = 0 (D3DPOOL_DEFAULT) —— 这是本次修复的核心 */
    long hr = ct(dev, TEX_W, TEX_H, 1, 0, 21 /*A8R8G8B8*/, BIGTEX_POOL /*池:0=DEFAULT/1=MANAGED*/, &tex, NULL);
    char b[256], t[64];
    b[0] = 0;
    lstrcatA(b, "[3B] CreateTexture(DEFAULT) "); wsprintfA(t, "%d", TEX_W); lstrcatA(b, t);
    lstrcatA(b, "x"); wsprintfA(t, "%d", TEX_H); lstrcatA(b, t);
    lstrcatA(b, " hr=0x"); wsprintfA(t, "%lX", (unsigned long)hr); lstrcatA(b, t);
    lstrcatA(b, " tex="); wsprintfA(t, "%p", tex); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
    if (!tex || hr < 0) return;

    /* ★ DEFAULT纹理不能 LockRect -> 走 SYSTEMMEM 中转 + UpdateTexture */
    /* ★ c68 实验：-DEXP_NO_FILL 编译时跳过「灌数据」，
     *   判定 Reset 破坏源是「创建纹理」还是「fill 上传流程」。 */
#ifndef EXP_NO_FILL
    fill_default_texture(dev, tex);
#else
    HKLOG("[3B] c68实验：跳过 fill_default_texture（只创建不灌数据）\r\n");
#endif

#ifdef EXP_LEAK_TEX
    /* ★ c79 实验（2026-10-09）：创建后【永不释放、永不登记】（故意泄漏）。
     *   与 c77 对切："创建"与"释放"谁是毒药。 */
    {
        char bb[200], bt[64]; bb[0] = 0;
        lstrcatA(bb, "[EXP-c79] 创建并故意泄漏 g_bigTex="); wsprintfA(bt, "%p", tex); lstrcatA(bb, bt);
        lstrcatA(bb, "（永不释放）\r\n");
        HKLOG(bb);
    }
    return;     /* 不设置 g_bigTex（prelude 不会释放它） */
#endif
#ifdef EXP_IMM_RELEASE
    /* ★ c77 实验（2026-10-09）：创建后【立即】释放、零持有（矩阵最后一格）。
     *   若本版 Reset 仍失败 => "由我们创建纹理"这一动作本身就是毒药（与生命周期无关）。 */
    {
        void** bv = *(void***)tex;
        unsigned long rc = 0xFFFFFFFFUL;
        if (bv && bv[2]) rc = ((PFN_COMRel)bv[2])(tex);
        {
            char bb[220], bt[64]; bb[0] = 0;
            lstrcatA(bb, "[EXP-c77] 立即释放 g_bigTex="); wsprintfA(bt, "%p", tex); lstrcatA(bb, bt);
            lstrcatA(bb, " rc="); wsprintfA(bt, "%lu", rc); lstrcatA(bb, bt);
            lstrcatA(bb, " f="); wsprintfA(bt, "%ld", g_frames); lstrcatA(bb, bt);
            lstrcatA(bb, "（零持有）\r\n");
            HKLOG(bb);
        }
    }
    return;     /* 不设置 g_bigTex */
#endif
    g_bigTex = tex;
    if (g_rearmPending) g_d2BigTexFrame = g_frames;   /* ★ c93：Reset 后大纹就绪 */
    {
        char bb[160], bt[64];
        bb[0] = 0;
        lstrcatA(bb, "[SWAP] g_bigTex 就绪 = "); wsprintfA(bt, "%p", g_bigTex); lstrcatA(bb, bt);
        lstrcatA(bb, " (frame="); wsprintfA(bt, "%ld", g_frames); lstrcatA(bb, bt);
        lstrcatA(bb, ")\r\n");
        HKLOG(bb);
    }
}

/* ============================================================
 * ★★★★★ c50：灌数据改为「SYSTEMMEM 中转 + UpdateTexture」
 *
 *   根因（c46/c49 二分实验铁证）：
 *     我们创建的大纹理用 D3DPOOL_MANAGED(1)，而 MANAGED 纹理带一份
 *     「系统内存副本」+ mipmap 链。设备丢失（Alt-Tab）后调用 Reset 时，
 *     D3D9 需要重建这份副本，但我们是用 texVt[19] LockRect 手工灌的
 *     （非D3D 托管的正常上传路径）-> Reset 判定无效 ->返回
 *     D3DERR_INVALIDCALL (0x8876086C) -> 白屏。
 *     对照：c46【完全不建纹理】时 Reset 返回 hr=0x0 成功、Alt-Tab 正常。
 *
 *   正确做法（用户最早提出的「就地改纹理」方案，机制完全吻合）：
 *     ① 大纹理用 D3DPOOL_DEFAULT(0) —— Reset 时 D3D9 重建资源但【COM 指针
 *        保持不变】，这正是我们需要的语义（g_bigTex 跨 Reset 仍有效）；
 *     ② DEFAULT 纹理【不能 LockRect】，所以数据从一张 SYSTEMMEM(2) 纹理
 *        中转：LockRect 灌进 SYSTEMMEM -> UpdateTexture(sysmem -> default)；
 *     ③ UpdateTexture = device vt[31]。
 *
 *   收益：DEFAULT 纹理的重建完全由 D3D9 管理，Reset 行为与原版一致。
 * ============================================================ */
static void fill_default_texture(void* dev, void* dstTex) {
    if (!dev || !dstTex) return;
    if (!dev_alive(dev)) { HKLOG("[3B] 设备不可用，跳过 UpdateTexture\r\n"); return; }
    void** vt = *(void***)dev;
    if (!vt || !vt[23] || !vt[31]) return;      /* CreateTexture / UpdateTexture */
    PFN_CreateTex ct = g_orig_CreateTex ? g_orig_CreateTex : (PFN_CreateTex)vt[23];
    typedef long (WINAPI *PFN_UpdTex)(void*, void*, void*);
    PFN_UpdTex upd = g_orig_UpdTex ? (PFN_UpdTex)g_orig_UpdTex : (PFN_UpdTex)vt[31];

    /* ① 中转纹理：SYSTEMMEM(2)，可 LockRect */
    void* sys = NULL;
    long hr = ct(dev, TEX_W, TEX_H, 1, 0, 21 /*A8R8G8B8*/, 2 /*SYSTEMMEM*/, &sys, NULL);
    char b[240], t[64];
    b[0] = 0;
    lstrcatA(b, "[3B] 中转纹理(SYSTEMMEM) hr=0x"); wsprintfA(t, "%lX", (unsigned long)hr); lstrcatA(b, t);
    lstrcatA(b, " tex="); wsprintfA(t, "%p", sys); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
    if (!sys || hr < 0) return;

    void** sv = *(void***)sys;
    LOCKED_RECT lr; ZeroMemory(&lr, sizeof(lr));
    long lr_hr = ((PFN_LockRect)sv[19])(sys, 0, &lr, NULL, 0);
    b[0] = 0;
    lstrcatA(b, "[3B] 中转 LockRect hr=0x"); wsprintfA(t, "%lX", (unsigned long)lr_hr); lstrcatA(b, t);
    lstrcatA(b, " Pitch="); wsprintfA(t, "%ld", lr.Pitch); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
    if (lr_hr >= 0 && lr.pBits) {
        HANDLE f = CreateFileA(g_rawPath, GENERIC_READ, FILE_SHARE_READ, NULL,
                               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (f != INVALID_HANDLE_VALUE) {
            DWORD row = TEX_W * 4;
            for (long y = 0; y < TEX_H; y++) {
                DWORD got = 0;
                if (!ReadFile(f, (char*)lr.pBits + y * lr.Pitch, row, &got, NULL) || !got) break;
            }
            CloseHandle(f);
            HKLOG("[3B] raw 已灌入中转纹理\r\n");
        } else HKLOG("[3B] raw 打开失败\r\n");
        ((PFN_UnlockRect)sv[20])(sys, 0);
    }
    /* ② SYSTEMMEM -> DEFAULT 上传（UpdateTexture = vt[31]） */
    long uh = upd(dev, sys, dstTex);
    b[0] = 0;
    lstrcatA(b, "[3B] UpdateTexture(SYSTEMMEM->DEFAULT) hr=0x");
    wsprintfA(t, "%lX", (unsigned long)uh); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
    /* ③ 释放中转纹理（SYSTEMMEM 不占显存，释放安全） */
    {
        typedef long (WINAPI *PFN_Release)(void*);
        ((PFN_Release)sv[2])(sys);
        HKLOG("[3B] 中转纹理已Release\r\n");
    }
}

/* 纹理指针缓存：同一纹理只查一次尺寸，避免每帧重复 GetLevelDesc 拖慢游戏 */
#define TEXCACHE_BITS 10
#define TEXCACHE_SIZE (1 << TEXCACHE_BITS)
typedef struct { void* tex; unsigned long w, h, fmt, pool, usage; long hits; } TexEnt;
static TexEnt g_texCache[TEXCACHE_SIZE];
static int   g_texLogN = 0;          /* 已记录的不同纹理数 */

/* ============================================================
 * ★★★ 探针（2026-10-09 阶段2 · P1 贴图误伤诊断）：**只加日志，不改任何判据/行为**
 *   目的：
 *     1) 判定 512x128 纹理的调用来源 ra1 落在哪个模块（exe? hook桩? 其他 dll?）
 *     2) 打印 ra1 的绝对地址 / 模块基址/ RVA，验证 ASLR 下 RVA 是否稳定
 *     3) 记录该纹理当前是否会被替换（tex == g_fontTex）
 *     4) 字体对象结构 ±0x60 的 hex dump（方案B：找纹理指针字段）
 * ============================================================ */
static void probe_mod_name(void* addr, char* out, int outLen) {
    HMODULE hm = NULL;
    if (!addr || !out || outLen < 2) { if (out && outLen) out[0] = 0; return; }
    out[0] = 0;
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, (LPCSTR)addr, &hm)) {
        char full[300];
        full[0] = 0;
        if (GetModuleFileNameA(hm, full, sizeof(full)) > 0) {
            char* p = full; int n;
            for (n = 0; full[n]; n++) {
                if (full[n] == '\\' || full[n] == '/') p = full + n + 1;
            }
            lstrcpynA(out, p, outLen);
        }
    }
    if (!out[0]) lstrcpynA(out, "?", outLen);
}
static void probe_mod_info(void* addr, const char* tag, char* out, int outLen) {
    HMODULE hm = NULL;
    char nm[64];
    if (!out || outLen < 2) return;
    out[0] = 0;
    probe_mod_name(addr, nm, sizeof(nm));
    if (addr && GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, (LPCSTR)addr, &hm)) {
        wsprintfA(out, "%s|base=%p|rva=%p", nm, hm, (void*)((char*)addr - (char*)hm));
    } else {
        wsprintfA(out, "%s|base=?|rva=?", nm);
    }
}
/* 字体对象结构 ±0x100 hex dump（v2：范围从±0x60 扩到 ±0x100，找纹理指针字段） */
static void probe_dump_fontobj(DWORD obj, const char* tag) {
    if (!obj) return;
    char line[220];
    char t[64];
    lstrcatA(line, "[FOBJ] ");
    lstrcatA(line, tag);
    lstrcatA(line, " obj="); wsprintfA(t, "%p", (void*)obj); lstrcatA(line, t);
    HKLOG(line);
    for (DWORD off = 0; off < 0x200; off += 0x10) {
        DWORD v[4]; int ok = 1;
        for (int k = 0; k < 4; k++) if (safe_read(obj - 0x100 + off + 4*k, &v[k], 4) != 4) ok = 0;
        line[0] = 0;
        wsprintfA(line, "[FOBJ] %s %c%03lX :", tag, (off < 0x100) ? '-' : '+', (long)((off < 0x100) ? (0x100 - off) : (off - 0x100)));
        for (int k = 0; k < 4; k++) {
            wsprintfA(t, " %08lX", (unsigned long)v[k]);
            lstrcatA(line, t);
        }
        lstrcatA(line, ok ? "" : "  (部分读取失败)");
        lstrcatA(line, "\r\n");
        HKLOG(line);
    }
}

/* ============================================================
 * ★★★ 探针 v2（2026-10-09 阶段2 · P1 贴图误伤诊断）：**只加日志，不改任何判据/行为**
 * v1 教训：__builtin_return_address(1) 在 -O2 + 尾调用(tail call)下不可用
 *   （尾调用编译成 jmp，不建栈帧、不设EBP ->顺帧链读到垃圾）。
 * v2 方案：用 RtlCaptureStackBackTrace 直接扫栈（不依赖 EBP 链）。
 *   同时对每个 512x128 纹理记录【创建点栈】（一次性事件，栈最稳定）。
 * ============================================================ */
typedef USHORT (WINAPI* PFN_RtlCaptureStackBackTrace)(ULONG, ULONG, void**, ULONG*);
static PFN_RtlCaptureStackBackTrace g_pCap = NULL;
static void probe_init_cap(void) {
    if (g_pCap) return;
    HMODULE nt = GetModuleHandleA("ntdll.dll");
    if (!nt) nt = LoadLibraryA("ntdll.dll");
    if (nt) g_pCap = (PFN_RtlCaptureStackBackTrace)GetProcAddress(nt, "RtlCaptureStackBackTrace");
}
/* 打印栈：跳过本函数与 hook 自身层数 skip，返回打印的层数 */
static int probe_log_stack(const char* tag, void* known0, void* known1, int skip) {
    if (!g_pCap) probe_init_cap();
    if (!g_pCap) return 0;
    void* frames[24];
    USHORT got = g_pCap((ULONG)(skip + 1), 24, frames, NULL);
    char line[400], t[64], mi[160];
    int printed = 0;
    for (int i = 0; i < (int)got && i < 10; i++) {
        probe_mod_info(frames[i], "s", mi, sizeof(mi));
        line[0] = 0;
        lstrcatA(line, "[STK] ");
        lstrcatA(line, tag);
        lstrcatA(line, " #"); wsprintfA(t, "%d", i); lstrcatA(line, t);
        lstrcatA(line, " abs="); wsprintfA(t, "%p", frames[i]); lstrcatA(line, t);
        if (known0 && frames[i] == known0) lstrcatA(line, " <=ra0");
        if (known1 && frames[i] == known1) lstrcatA(line, " <=ra1");
        lstrcatA(line, "  "); lstrcatA(line, mi);
        lstrcatA(line, "\r\n");
        HKLOG(line);
        printed++;
    }
    return printed;
}

/* ★★★ 探针 v4：原始栈扫描（**不依赖 EBP/帧指针**）
 *   背景：RtlCaptureStackBackTrace 在 x86 依赖 EBP 链；hook 与游戏（2004 MSVC）
 *         都可能省略帧指针 -> 只能拿到第 0 层（自身）。
 *   方案：直接读 ESP，向上扫描若干DWORD，把落在【exe 模块地址范围】内的值
 *         收集去重后打印。这些就是"游戏侧调用链上的真实返回地址"。
 *   用途：对每张 512x128 纹理得到一组「调用者地址集合」，
 *         用集合差异区分「字体图集」与「误伤贴图」。 */
static void probe_scan_stack(const char* tag, void* known0) {
    DWORD esp = 0;
#if defined(__i386__) && (defined(__GNUC__) || defined(__clang__))
    __asm__ __volatile__("movl %%esp, %0" : "=r"(esp));
#else
    esp = (DWORD)(size_t)__builtin_frame_address(0);
#endif
    if (!esp) return;
    DWORD seen[16];
    int nseen = 0;
    char line[260], t[64], mi[160];
    for (int i = 0; i < 600 && nseen < 12; i++) {
        DWORD v = 0;
        if (safe_read((DWORD)(esp + i * 4), &v, 4) != 4) break;
        if (v < 0x00400000 || v > 0x00A00000) continue;      /* 只收 exe 范围 */
        int dup = 0;
        for (int k = 0; k < nseen; k++) if (seen[k] == v) { dup = 1; break; }
        if (dup) continue;
        seen[nseen++] = v;
        probe_mod_info((void*)v, "x", mi, sizeof(mi));
        line[0] = 0;
        lstrcatA(line, "[SS] ");
        lstrcatA(line, tag);
        lstrcatA(line, " "); wsprintfA(t, "%d", nseen - 1); lstrcatA(line, t);
        lstrcatA(line, " esp+"); wsprintfA(t, "%d", i * 4); lstrcatA(line, t);
        lstrcatA(line, " abs="); wsprintfA(t, "%p", (void*)v); lstrcatA(line, t);
        if (known0 && v == (DWORD)known0) lstrcatA(line, " <=ra0");
        lstrcatA(line, "  "); lstrcatA(line, mi);
        lstrcatA(line, "\r\n");
        HKLOG(line);
    }
}

static long WINAPI Hook_SetTexture(void* self, unsigned long stage, void* tex) {
    g_ckpt = "ST:1-enter";
    /* ★ 诊断（2026-10-08）：Reset 后 SetTexture 活动统计（纯整数计数，零开销）
     *   回答核心问题：Reset 后游戏到底还调不调 SetTexture？会不会重绑新字体纹理？ */
#if P1_DIAG_PROBES
    if (g_rstDiagUntil && g_frames < g_rstDiagUntil) {
        g_rstStTotal++;
        if (tex && tex == g_fontTex) g_rstStFont++;
        if (tex && g_rstSampleN < 6) {
            int sd = 0;
            for (int i = 0; i < g_rstSampleN; i++) if (g_rstSample[i] == tex) { sd = 1; break; }
            if (!sd) g_rstSample[g_rstSampleN++] = tex;
        }
        /* ★ 记录窗口内每个【不同】纹理 + 调用者 RVA（判定字体绘制代码路径） */
        if (tex && g_rstLogN < 40) {
            int dup = 0;
            for (int i = 0; i < g_rstUniqN; i++) if (g_rstUniq[i] == tex) { dup = 1; break; }
            if (!dup) {
                g_rstUniq[g_rstUniqN++] = tex;
                g_rstLogN++;
                unsigned long dw = 0, dh = 0, dp = 9;
                void** tv2 = *(void***)tex;
                if (tv2 && tv2[17]) {
                    SURF_DESC sd2; ZeroMemory(&sd2, sizeof(sd2));
                    if (((PFN_GetLevelDesc)tv2[17])(tex, 0, &sd2) >= 0) {
                        dw = sd2.Width; dh = sd2.Height; dp = sd2.Pool;
                    }
                }
                void* r0 = __builtin_return_address(0);
                HMODULE ex2 = GetModuleHandleA(NULL);
                char b3[240], t3[64];
                b3[0] = 0;
                lstrcatA(b3, "[RST-ST] f="); wsprintfA(t3, "%ld", g_frames); lstrcatA(b3, t3);
                lstrcatA(b3, " tex="); wsprintfA(t3, "%p", tex); lstrcatA(b3, t3);
                lstrcatA(b3, " "); wsprintfA(t3, "%lux%lu", dw, dh); lstrcatA(b3, t3);
                lstrcatA(b3, " pool="); wsprintfA(t3, "%lu", dp); lstrcatA(b3, t3);
                lstrcatA(b3, " ra0=exe+0x"); wsprintfA(t3, "%lX",
                        (unsigned long)((char*)r0 - (char*)ex2)); lstrcatA(b3, t3);
                if (tex == g_fontTex) lstrcatA(b3, "  <<< 当前g_fontTex");
                if (tex == g_rstOldFontTex) lstrcatA(b3, "  <<< 旧字体纹理(Reset前)");
                lstrcatA(b3, "\r\n");
                HKLOG(b3);
            }
        }
    }
    /* ★★ 全量日志窗口（Reset 后） */
    if (g_verboseUntil && g_frames < g_verboseUntil && g_verboseN < 3000) {
        g_verboseN++;
        unsigned long vw = 0, vh = 0, vpool = 0xFFFFFFFF;
        if (tex) {
            void** tvv = *(void***)tex;
            if (tvv && tvv[17]) {
                SURF_DESC vsd; ZeroMemory(&vsd, sizeof(vsd));
                if (((PFN_GetLevelDesc)tvv[17])(tex, 0, &vsd) >= 0) {
                    vw = vsd.Width; vh = vsd.Height; vpool = vsd.Pool;
                }
            }
        }
        HMODULE vexe = GetModuleHandleA(NULL);
        DWORD vra0 = (DWORD)((char*)__builtin_return_address(0) - (char*)vexe);
        char vb[256], vt[64];
        vb[0] = 0;
        lstrcatA(vb, "[VERB] f="); wsprintfA(vt, "%ld", g_frames); lstrcatA(vb, vt);
        lstrcatA(vb, " st="); wsprintfA(vt, "%lu", stage); lstrcatA(vb, vt);
        lstrcatA(vb, " tex="); wsprintfA(vt, "%p", tex); lstrcatA(vb, vt);
        lstrcatA(vb, " "); wsprintfA(vt, "%lux%lu", vw, vh); lstrcatA(vb, vt);
        lstrcatA(vb, " pool="); wsprintfA(vt, "%lu", vpool); lstrcatA(vb, vt);
        lstrcatA(vb, " ra0=exe+0x"); wsprintfA(vt, "%lX", vra0); lstrcatA(vb, vt);
        lstrcatA(vb, "\r\n");
        HKLOG(vb);
    }
#endif  /* P1_DIAG_PROBES：[RST-ST]+[VERB] 只读诊断窗口 */
    /* 注意顺序：必须先跑下面的纹理识别，识别到 g_fontTex 之后才能替换 */
    if (tex) {
        d6_bind(tex, stage);   /* ★ c82_probe：512x128 全量绑定计数（不受首见日志限制） */
        unsigned int slot = ((unsigned int)(size_t)tex >> 2) & (TEXCACHE_SIZE - 1);
        TexEnt* e = &g_texCache[slot];
        if (e->tex != tex) {
            /* 新纹理：读一次尺寸 */
            unsigned long w = 0, h = 0, fmt = 0, pool = 0, usage = 0;
            void** tv = *(void***)tex;
            if (tv && tv[17]) {
                SURF_DESC sd; ZeroMemory(&sd, sizeof(sd));
                PFN_GetLevelDesc g = (PFN_GetLevelDesc)tv[17];
                if (g(tex, 0, &sd) >= 0) {
                    w = sd.Width; h = sd.Height; fmt = sd.Format;
                    pool = sd.Pool; usage = sd.Usage;
                }
            }
            e->tex = tex; e->w = w; e->h = h; e->fmt = fmt;
            e->pool = pool; e->usage = usage; e->hits = 0;
            /* ★★ 调用栈特征（2026-10-07 加固）：记录是谁调用的 SetTexture。
             *   字体绘制和地面/场景绘制来自 exe 里不同的代码位置，
             *   用「返回地址在 exe 中的 RVA」区分，比按尺寸盲抓可靠得多。 */
            void* ra0 = __builtin_return_address(0);
            void* ra1 = __builtin_return_address(1);
#if P1_DIAG_PROBES
            /* 仅对 512x128 记录调用栈，避免日志爆量 */
            if (w == 512 && h == 128) {
                HMODULE exe = GetModuleHandleA(NULL);
                DWORD rva0 = (DWORD)((char*)ra0 - (char*)exe);
                DWORD rva1 = (DWORD)((char*)ra1 - (char*)exe);
                char cb[220], ct[64];
                cb[0] = 0;
                lstrcatA(cb, "[CALLER] 512x128 tex="); wsprintfA(ct, "%p", tex); lstrcatA(cb, ct);
                lstrcatA(cb, " f="); wsprintfA(ct, "%ld", g_frames); lstrcatA(cb, ct);
                lstrcatA(cb, " ra0=exe+0x"); wsprintfA(ct, "%lX", rva0); lstrcatA(cb, ct);
                lstrcatA(cb, " ra1=exe+0x"); wsprintfA(ct, "%lX", rva1); lstrcatA(cb, ct);
                lstrcatA(cb, "\r\n");
                HKLOG(cb);
            }
            /* ★★★探针（阶段2 P1 诊断）：512x128 纹理的来源与模块归属。**只读，不改判据** */
            if (w == 512 && h == 128) {
                char pb[900], pt[160], q0[160], q1[160];
                probe_mod_info(ra0, "ra0", q0, sizeof(q0));
                probe_mod_info(ra1, "ra1", q1, sizeof(q1));
                pb[0] = 0;
                wsprintfA(pb, "[PROBE] tex=%p %lux%lu fmt=%lu pool=%lu f=%ld replaced=%d",
                          tex, w, h, fmt, pool, g_frames, (tex == g_fontTex) ? 1 : 0);
                lstrcatA(pb, "\r\n[PROBE]   ra0_abs=");
                wsprintfA(pt, "%p", ra0); lstrcatA(pb, pt);
                lstrcatA(pb, "  "); lstrcatA(pb, q0); lstrcatA(pb, "\r\n[PROBE]   ra1_abs=");
                wsprintfA(pt, "%p", ra1); lstrcatA(pb, pt);
                lstrcatA(pb, "  "); lstrcatA(pb, q1);
                lstrcatA(pb, "\r\n");
                HKLOG(pb);
                /* v2：栈回溯 */
                probe_log_stack("SetTex", ra0, ra1, 0);
                /* ★ c58：候选判据离线判定（只读，不改行为） */
                {
                    char vb[220], vt2[64]; vb[0] = 0;
                    lstrcatA(vb, "[VERD] tex="); wsprintfA(vt2, "%p", tex); lstrcatA(vb, vt2);
                    lstrcatA(vb, " f="); wsprintfA(vt2, "%ld", g_frames); lstrcatA(vb, vt2);
                    lstrcatA(vb, " inUtDstSet="); lstrcatA(vb, ut_dst_hit(tex) ? "1(替换)" : "0(放行)");
                    lstrcatA(vb, " \r\n"); HKLOG(vb);
                }
                /* v4：原始栈扫描（不依赖帧指针）—— 核心判据数据 */
                /* c57: 停用 c56 每帧栈扫描（开销大） */
            }
#endif  /* P1_DIAG_PROBES：[CALLER]+[PROBE]+[STK] 只读探针 */
            /* ★ 自动识别「菜单字体」纹理：只认第一张出现的 512x128 纹理。
             *   ★★ 重大修正（2026-10-07）：游戏里存在多张 512x128 字体图集：
             *      - 菜单字体 holstein24 用 tex#1（约在第 125 帧主菜单时绑定）；
             *      - 游戏内 HUD/字幕 holstein30/lucida10 用另一张 tex#2
             *        （约在进入游戏第 11671 帧才绑定）。
             *   之前把「所有」512x128 纹理都换成 512x1024 大图集，导致 HUD 字体
             *   的图集也被替换，而其组参数仍按 128 归一化 -> 采样行错位 -> 英文
             *   HUD/字幕乱码。现在只替换第一张（菜单），其余一律放行。 */
            if (w == 512 && h == 128) {
                /* ★★★★★ c60（阶段2 P1 修复）：判据从「尺寸唯一」升级为
                 *   「尺寸 + 上传来源」——只有【源为 SYSTEMMEM 的 UpdateTexture 的
                 *   目标纹理】才算字体图集（游戏字体系统统一走"SYSTEMMEM 暂存+上传"
                 *   管线，实测 3 轮 18 次 512x128 上传全部是字形表）。
                 *   其余 512x128（场景/人物贴图，走别的填充路径）一律放行，
                 *   从根上消除"贴图被换成图集碎片"的误伤。
                 *   集合来源：Hook_UpdateTexture 中 ut_dst_add()。 */
                if (!ut_dst_hit(tex)) {
                    /* 放行：非字体贴图（实施验证期记录一次，确认修复生效） */
                    char sb[220], st[64]; sb[0] = 0;
                    lstrcatA(sb, "[SKIP-NONFONT] tex="); wsprintfA(st, "%p", tex); lstrcatA(sb, st);
                    lstrcatA(sb, " f="); wsprintfA(st, "%ld", g_frames); lstrcatA(sb, st);
                    lstrcatA(sb, " (512x128 但未经 SYSTEMMEM-UT 上传 -> 放行)\r\n");
                    HKLOG(sb);
                }
                /* ★★★ c44：游戏会【反复重建】字体图集（指针每次都变，实测 10+ 次），
                 *   所以必须把 g_fontTex【跟踪到最新指针】——否则交换永不匹配 ->
                 *   参数(1024)+游戏纹理(128) = 竖条（游戏内实测现象）。 */
                /* ══ c92 修复（B1+）：路径B 收紧 —— 与路径A 的组哈希判据一致 ══
                 * 问题：路径B 只用 ut_dst_hit(tex)，组1/组2 的 512x128 都满足
                 *       -> 游戏绑定组2纹理时 g_fontTex 被污染 -> 全部乱码
                 * 依据：实测 38/38 次 Reset 后都是「组1的 CreateTexture 先」，
                 *       且 Reset 后 51 行内的 SetTex 事件发生时
                 *       postRebuild 已被清 0、g_fontTex 已为组1（非NULL）
                 *       -> 本修复的两个条件都不成立 -> 会挡住误采纳
                 * 注：Hook_CreateTexture 的同类判据本轮不动。 */
                if (tex != g_fontTex && ut_dst_hit(tex)
                    && g_postResetNeedRebuild
                    && g_fontTex == NULL) {
                    char fb[240], ft[64];
                    fb[0] = 0;
                    lstrcatA(fb, "[FONT] 字体图集更新: tex="); wsprintfA(ft, "%p", tex); lstrcatA(fb, ft);
                    lstrcatA(fb, " (旧="); wsprintfA(ft, "%p", g_fontTex); lstrcatA(fb, ft);
                    lstrcatA(fb, ") f="); wsprintfA(ft, "%ld", g_frames); lstrcatA(fb, ft);
                    lstrcatA(fb, "\r\n");
                    if (g_swapN < 20) HKLOG(fb);
                    d6_tex("SetTex", g_fontTex, tex);   /* c82_probe */
                    g_fontTex = tex;
                    if (!is_font_tex(tex) && g_fontTexN < 8) g_fontTexes[g_fontTexN++] = tex;
                    g_rescan_enable = 1;
                    g_postReset = 0;
                }
                /* 尚未发现字体组时做一次完整发现（含扫描，仅一次） */
                if (!g_discoveryDone) patch_font_obj();
            }
            if (g_texLogN < 4000) {
                g_texLogN++;
                char b[320], t[64];
                b[0] = 0;
                lstrcatA(b, "[SetTex] stage="); wsprintfA(t, "%lu", stage); lstrcatA(b, t);
                lstrcatA(b, " tex="); wsprintfA(t, "%p", tex); lstrcatA(b, t);
                wsprintfA(t, " %lux%lu", w, h); lstrcatA(b, t);
                lstrcatA(b, " fmt="); wsprintfA(t, "%lu", fmt); lstrcatA(b, t);
                lstrcatA(b, " pool="); wsprintfA(t, "%lu", pool); lstrcatA(b, t);
                lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
                if (w == 512 && h == 128) lstrcatA(b, "   <<< 候选 512x128");
                lstrcatA(b, "\r\n");
                HKLOG(b);
            }
        } else {
            e->hits++;
            /* 已知候选：每 20000 次绑定报一次活跃度（累计，非每帧） */
            if (e->w == 512 && e->h == 128 && (e->hits % 20000) == 0) {
                char b[256], t[64];
                b[0] = 0;
                lstrcatA(b, "[FONT?] tex="); wsprintfA(t, "%p", tex); lstrcatA(b, t);
                lstrcatA(b, " 累计绑定="); wsprintfA(t, "%ld", e->hits); lstrcatA(b, t);
                lstrcatA(b, "\r\n");
                HKLOG(b);
            }
        }
    }
    /* ★★★ c45：持续补丁模式（回到 c43 的正确思路，删除 c44 的「帧内局部 + 帧末还原」）
     *   c44 实测失败：每帧「改参数→换纹理→立刻还原」，而游戏在帧中间绘制，
     *   状态被还原得太早导致渲染错乱 -> 菜单从第125 帧起就白屏。
     *   正确做法：补丁【持续生效】。首次确认绑定时开闸并打补丁，
     *   之后每次绑定都换成大图集（参数已改好，无需每帧重打）。
     *   参数与纹理仍严格「同进同退」：
     *     - 纹理：每次 SetTexture(游戏字体图集) -> 换成大图集；
     *     - 参数：Reset 时撤回（restore_all_orig），非Reset 期间保持 1024。
     */    /* ★ c93e：换回判据模式化（运行时 swap_mode.txt，DllMain 读一次）
     *   mode=A（默认）＝ c92 原判据 tex==g_fontTex（g_fontTex 由 FONT-CONFIRM
     *     登记/跟随分支维护为最新确认者）。c93c 集合化判据经实测否决
     *     （用户环境每帧 A/B 交替绑定 -> 静态 bigTex 覆盖动态内容 -> 闪现），回退。
     *   mode=B ＝ 只换集合中【非】g_fontTex 的命中项（二分实验备用）。
     *   共同点：命中即换 bigTex；mode 打入日志。 */
    {
        int swapHit = 0;
        if (tex && g_bigTex && g_subOn && grp1_hit(tex))
            swapHit = g_swapModeB ? (tex != g_fontTex) : (tex == g_fontTex);
        if (swapHit) {
        g_bindTick++;
#if TEX_INTERVENE == 1
        if (!g_fontTexBound) {
            /* ★ 首次确认游戏会绑这张字体图集 -> 开闸 + 同帧打参数补丁。
             *   开闸后【立刻】打补丁，保证「参数=1024」与「绑定大图集」同帧生效，
             *   物理上不可能出现「参数已改但纹理还是 128」的竖条态。*/
            g_fontTexBound = 1;
            g_ckpt = "ST:5-patchOnBind";
            apply_params_fast();
            HKLOG("[3B] SetTexture 已确认字体纹理绑定 -> 开闸并立即打补丁\r\n");
        }
        g_swapN++;
        if (g_swapN <= 12) {
            char sb[260], st[64];
            DWORD ra = (DWORD)__builtin_return_address(0) - (DWORD)GetModuleHandleA(NULL);
            sb[0] = 0;
            lstrcatA(sb, "[SWAP#] tex="); wsprintfA(st, "%p", tex); lstrcatA(sb, st);
            lstrcatA(sb, " -> bigTex="); wsprintfA(st, "%p", g_bigTex); lstrcatA(sb, st);
            lstrcatA(sb, " f="); wsprintfA(st, "%ld", g_frames); lstrcatA(sb, st);
            lstrcatA(sb, " 累计="); wsprintfA(st, "%ld", g_swapN); lstrcatA(sb, st);
            lstrcatA(sb, " 判据=组1指纹集合");   /* ★ c93c：判据留痕 */
            lstrcatA(sb, g_swapModeB ? " mode=B" : " mode=A");   /* ★ c93e：模式留痕 */
            wsprintfA(st, " ra=exe+0x%lX", ra); lstrcatA(sb, st);   /* ★ c93d 采数 */
            lstrcatA(sb, "\r\n");
            HKLOG(sb);
        }
#if TEX_CREATE_ONLY == 0
        tex = g_bigTex;      /* c49 二分：不做替换，只观察 */
#endif
#endif  /* TEX_INTERVENE == 1 */
        }   /* ★ c93e swapHit */
    }
    g_ckpt = "ST:9-callOrig";
    return g_orig_SetTexture(self, stage, tex);
}

/* ============================================================
 * ★★★ c45：帧末还原已【彻底停用】（保留函数仅为记录教训）
 *   c44 实测证伪：每帧「改参数 -> 换纹理 -> 帧末全部还原」会让菜单从第 125 帧起
 *   就白屏。日志铁证（每帧重复 6 次以上）：
 *       [PATCH] 帧内打补丁: +12=1024 组数=3 f=125
 *       [SWAP#] tex=150D3C80 -> bigTex=06FD4120 f=125
 *       [RESET] Reset 前已恢复原字段的字体组数 = 1     ← 帧末立刻撤回
 *   游戏在帧中间绘制，我们把参数还原得太早 -> 渲染时状态错乱。
 *
 *   现在的正确策略（c43 思路 + c45 设备守卫）：
 *     - 参数补丁【持续生效】，只在 Reset 时撤回（非 Reset 期间保持 1024）；
 *     - 纹理侧靠 SetTexture 钩子每次绑定都换成大图集（参数已就绪，无需每帧重打）；
 *     - 「参数」与「纹理」仍严格同进同退，不会出现竖条态。
 *============================================================ */
static void restore_frame_end(void) {
    /* c45：不再做任何帧末还原（见上方注释）。保留空实现以维持调用点。 */
}

/* device vtable 补丁：游戏返回的 device 的 vtable 在堆上（是一份私有拷贝，非模块共享内存），
 * 所以直接原地改最稳妥——任何拿到同一 vtable 的对象都会被 hook。 */static void attach_dev(void* dev, int idx, void* hook, int n);   /* 前向声明 */
static void* g_devPtr = NULL;   /* 当前 device（Reset 后重建大纹理要用） */

/* ============================================================
 *  ★★ CreateTexture (vt[23]) Hook —— 【Reset 根因修复】
 *   根因（2026-10-07 实测证实）：
 *     游戏的字体图集纹理是 D3DPOOL_DEFAULT(pool=0)。Alt-Tab 触发
 *     IDirect3DDevice9::Reset 后，DEFAULT 资源被 D3D 销毁，而游戏
 *     不会重建它（游戏内部仍持有旧指针），于是：
 *       - 绘制文字时 D3D 只绑到默认白纹理 -> 【白竖条】
 *       - 我们的 g_fontTex 指针失效 -> 替换失败
 *     实测证据：Reset 后 1200 帧的全量日志窗口里，SetTexture 调用数 = 0。
 *   修复：把「字体图集规格」的纹理池强制改成 D3DPOOL_MANAGED(1)。
 *     MANAGED 纹理在 Reset 时由 D3D 运行时自动重建，**指针保持不变**，
 *     游戏与我们的缓存全部继续有效，无需重置任何东西。
 * ============================================================ */
static int g_ctLogN = 0;

/* ============================================================
 * ★ c57 探针（阶段 2 · P1 第 1 步）：纹理真值【独立标注】
 *   不改任何替换行为；只做三件事：
 *     ① 登记每次 CreateTexture 的规格（w/h/levels/usage/fmt/pool/帧号）
 *     ② 钩 device vt[31] UpdateTexture：读【源纹理】（SYSTEMMEM，可 LockRect）
 *        像素 -> 写 BMP（RGB 版 + alpha 灰度版），文件名带帧号/源/目标指针
 *     ③ 记录内容哈希，供跨次运行比对同一张纹理
 *   ★ 不做：不改判据、不改替换、不做每帧栈扫描（c56 那套已停用）
 * ============================================================ */
#define TDDIR "__RUNTIME__"
#define TDMAXREG   96
#define TDMAXDUMP  48


typedef struct {
    void* tex; unsigned int w, h;
    unsigned long levels, usage, fmt, pool;
    long frame;
} TDReg;
static TDReg  g_tdReg[TDMAXREG];
static int    g_tdRegN = 0;
static void*  g_tdDumped[TDMAXDUMP];
static int    g_tdDumpedN = 0;
static long   g_tdUpdCount = 0;
/* ★ c61：候选集合（诊断用）= CT 创建的 512x128，UT 确认后移除 */
static void*  g_tdCand[64];
static int    g_tdCandN = 0;
static void   td_cand_add(void* t) {
    if (!t || g_tdCandN >= 64) return;
    for (int i = 0; i < g_tdCandN; i++) if (g_tdCand[i] == t) return;
    g_tdCand[g_tdCandN++] = t;
}
/* ★ c61b：组哈希——游戏有多个 512x128 字体组（标准/大字号），大图集参数按
 *   grp1 归一化，只能替换【第一组】；第二组替换会复发 c44 HUD 乱码。
 *   用 src 前 32KB 采样哈希区分组（同组重建内容相同 -> 哈希相同 -> 跟随）。 */
static unsigned long g_grp1Hash = 0;
/* ★ c62：哈希【不缓存】——游戏会复用同一个 SYSTEMMEM 暂存纹理上传不同内容
 *   （实测同一 dst 在组1/组2间交替），按 src 缓存会返回过时哈希导致误判。 */
static void*  g_grp1Tex[8]; static int g_grp1N = 0;   /* 组1 已知载体历史 */
static unsigned long td_src_hash(void* src) {
    void** sv = *(void***)src;
    if (!sv || !sv[19] || !sv[20]) return 0;
    LOCKED_RECT lr; ZeroMemory(&lr, sizeof(lr));
    if (((PFN_LockRect)sv[19])(src, 0, &lr, NULL, 0x10) < 0 || !lr.pBits) return 0;
    static unsigned char hb[32768];
    DWORD rd = safe_read((DWORD)lr.pBits, hb, (DWORD)sizeof(hb));
    ((PFN_UnlockRectT)sv[20])(src, 0);
    if (rd == 0) return 0;
    unsigned long h = 2166136261UL;
    for (DWORD i = 0; i < rd; i++) { h ^= hb[i]; h = h * 16777619UL; }
    return h;
}
static void grp1_remember(void* d) {
    for (int i = 0; i < g_grp1N; i++) if (g_grp1Tex[i] == d) return;
    if (g_grp1N < 8) { g_grp1Tex[g_grp1N++] = d; return; }
    /* ★ c93c：FIFO 溢出策略 —— 覆盖最旧槽位（最旧指针大概率已随图集重建失效；
     *   Steam 0-Reset 长会话下静默丢弃会导致换回失效，故必须淘汰并留痕） */
    {
        char bb[200], tt[64]; bb[0] = 0;
        lstrcatA(bb, "[GRP1-FIFO] 指纹集合满(8)，淘汰最旧 "); wsprintfA(tt, "%p", g_grp1Tex[0]); lstrcatA(bb, tt);
        lstrcatA(bb, " -> 新入 "); wsprintfA(tt, "%p", d); lstrcatA(bb, tt);
        lstrcatA(bb, "\r\n");
        HKLOG(bb);
    }
    for (int i = 1; i < 8; i++) g_grp1Tex[i - 1] = g_grp1Tex[i];
    g_grp1Tex[7] = d;
}
/* ★ c93c：组1 指纹集合命中判定 —— 换回判据从「单一 g_fontTex 指针」
 *   升级为「集合任一命中」。集合成员全部经 FONT-CONFIRM 内容哈希确认
 *   （组1 专属，组2 永不入集 -> 不重蹈 c91）；REVOKE/Reset 负责修剪。 */
static int grp1_hit(void* d) {
    for (int i = 0; i < g_grp1N; i++) if (g_grp1Tex[i] == d) return 1;
    return 0;
}
static void grp1_forget(void* d) {
    for (int i = 0; i < g_grp1N; i++) if (g_grp1Tex[i] == d) {
        g_grp1Tex[i] = g_grp1Tex[--g_grp1N];
        return;
    }
}
/* ★ c58：判据候选集合 =「源为 SYSTEMMEM 的 UT 的 dst」指针集合 */
static void*  g_utDstSet[64];
static int    g_utDstSetN = 0;
static int    ut_dst_hit(void* d) {
    for (int i = 0; i < g_utDstSetN; i++) if (g_utDstSet[i] == d) return 1;
    return 0;
}
static void   ut_dst_add(void* d) {
    if (!d || g_utDstSetN >= 64) return;
    for (int i = 0; i < g_utDstSetN; i++) if (g_utDstSet[i] == d) return;
    g_utDstSet[g_utDstSetN++] = d;
}
static unsigned char g_tdBuf[3 * 1024 * 1024];

static void td_register(void* tex, unsigned int w, unsigned int h, unsigned long lv,
                        unsigned long usage, unsigned long fmt, unsigned long pool) {
    if (!tex || g_tdRegN >= TDMAXREG) return;
    if (!(w == 512 && h == 128)) return;    /* c57 修正：只登记候选规格，省表位 */
    for (int i = 0; i < g_tdRegN; i++) if (g_tdReg[i].tex == tex) return;
    TDReg* r = &g_tdReg[g_tdRegN++];
    r->tex = tex; r->w = w; r->h = h; r->levels = lv;
    r->usage = usage; r->fmt = fmt; r->pool = pool; r->frame = g_frames;
}
static TDReg* td_find(void* tex) {
    if (!tex) return 0;
    for (int i = 0; i < g_tdRegN; i++) if (g_tdReg[i].tex == tex) return &g_tdReg[i];
    return 0;
}
static int td_already(void* tex) {
    for (int i = 0; i < g_tdDumpedN; i++) if (g_tdDumped[i] == tex) return 1;
    return 0;
}

/* 写 BMP：bpp=32 直接拷（D3D A8R8G8B8 内存即 B,G,R,A）；bpp=24 用 alpha 灰度 */
static void td_write_bmp(const char* path, unsigned char* px, int w, int h, int bpp) {
    int row = ((w * (bpp / 8)) + 3) & ~3;
    int imgSize = row * h;
    unsigned char hdr[54];
    for (int i = 0; i < 54; i++) hdr[i] = 0;
    hdr[0] = 'B'; hdr[1] = 'M';
    *(int*)(hdr + 2)  = 54 + imgSize;
    *(int*)(hdr + 10) = 54;
    *(int*)(hdr + 14) = 40;
    *(int*)(hdr + 18) = w;
    *(int*)(hdr + 22) = -h;                 /* top-down */
    *(short*)(hdr + 26) = 1;
    *(short*)(hdr + 28) = (short)bpp;
    *(int*)(hdr + 34) = imgSize;
    HANDLE f = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    DWORD nw = 0;
    WriteFile(f, hdr, 54, &nw, NULL);
    if (bpp == 32 && row == w * 4) {
        WriteFile(f, px, imgSize, &nw, NULL);
    } else {
        static unsigned char rb[16384];
        for (int y = 0; y < h; y++) {
            int need = (bpp == 32) ? w * 4 : w * 3;
            if (need > (int)sizeof(rb)) break;
            if (bpp == 32) {
                for (int x = 0; x < w * 4; x++) rb[x] = px[y * w * 4 + x];
            } else {
                for (int x = 0; x < w; x++) {
                    unsigned char a = px[y * w * 4 + x * 4 + 3];
                    rb[x*3] = a; rb[x*3+1] = a; rb[x*3+2] = a;
                }
            }
            WriteFile(f, rb, row, &nw, NULL);
        }
    }
    CloseHandle(f);
}

static void td_dump_src(void* src, void* dst, TDReg* r) {
    int w = (int)r->w, h = (int)r->h;
    if (w <= 0 || h <= 0) return;
    unsigned int need = (unsigned int)w * (unsigned int)h * 4;
    if (need > (unsigned int)sizeof(g_tdBuf)) return;
    void** sv = *(void***)src;
    if (!sv || !sv[19] || !sv[20]) return;
    char b[400], t[64];
    LOCKED_RECT lr; ZeroMemory(&lr, sizeof(lr));
    long hr = ((PFN_LockRect)sv[19])(src, 0, &lr, NULL, 0x10 /*D3DLOCK_READONLY*/);
    if (hr < 0) hr = ((PFN_LockRect)sv[19])(src, 0, &lr, NULL, 0);
    b[0] = 0;
    lstrcatA(b, "[DUMP] lock src="); wsprintfA(t, "%p", src); lstrcatA(b, t);
    lstrcatA(b, " hr=0x"); wsprintfA(t, "%lX", (unsigned long)hr); lstrcatA(b, t);
    lstrcatA(b, " pitch="); wsprintfA(t, "%ld", (long)lr.Pitch); lstrcatA(b, t);
    lstrcatA(b, "\r\n"); HKLOG(b);
    if (hr < 0 || !lr.pBits) return;
    unsigned char* p = (unsigned char*)lr.pBits;
    int ok = 1;
    for (int y = 0; y < h; y++) {
        if (safe_read((DWORD)(p + y * lr.Pitch), g_tdBuf + y * w * 4, w * 4) != (DWORD)(w * 4)) { ok = 0; break; }
    }
    ((PFN_UnlockRectT)sv[20])(src, 0);
    if (!ok) { HKLOG("[DUMP] 读像素失败\r\n"); return; }

    unsigned long hsh = 2166136261UL;
    for (unsigned int i = 0; i < need; i++) { hsh ^= g_tdBuf[i]; hsh = hsh * 16777619UL; }

    char path[400], name[180];
    wsprintfA(name, "f%ld_s%p_d%p_%ux%u", g_frames, src, dst, w, h);
    lstrcpyA(path, TDDIR); lstrcatA(path, name); lstrcatA(path, "_rgb.bmp");
    td_write_bmp(path, g_tdBuf, w, h, 32);
    lstrcpyA(path, TDDIR); lstrcatA(path, name); lstrcatA(path, "_a.bmp");
    td_write_bmp(path, g_tdBuf, w, h, 24);

    b[0] = 0;
    lstrcatA(b, "[DUMP] src="); wsprintfA(t, "%p", src); lstrcatA(b, t);
    lstrcatA(b, " dst="); wsprintfA(t, "%p", dst); lstrcatA(b, t);
    lstrcatA(b, " "); wsprintfA(t, "%ux%u", w, h); lstrcatA(b, t);
    lstrcatA(b, " lv="); wsprintfA(t, "%lu", r->levels); lstrcatA(b, t);
    lstrcatA(b, " usage="); wsprintfA(t, "%lu", r->usage); lstrcatA(b, t);
    lstrcatA(b, " fmt="); wsprintfA(t, "%lu", r->fmt); lstrcatA(b, t);
    lstrcatA(b, " pool="); wsprintfA(t, "%lu", r->pool); lstrcatA(b, t);
    lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
    lstrcatA(b, " hash="); wsprintfA(t, "%08lX", hsh); lstrcatA(b, t);
    lstrcatA(b, " file="); lstrcatA(b, name);
    lstrcatA(b, "\r\n"); HKLOG(b);
}

/* ★ c93 D2 全局状态已移至文件头部（g_drawCntDIP 声明之后） */

static long WINAPI Hook_UpdateTexture(void* self, void* src, void* dst) {
    g_tdUpdCount++;
    if (g_tdUpdCount <= 20 || (g_tdUpdCount % 500 == 0)) {
        char b2[240], t2[64]; b2[0] = 0;
        lstrcatA(b2, "[UT] #"); wsprintfA(t2, "%ld", g_tdUpdCount); lstrcatA(b2, t2);
        lstrcatA(b2, " src="); wsprintfA(t2, "%p", src); lstrcatA(b2, t2);
        lstrcatA(b2, " dst="); wsprintfA(t2, "%p", dst); lstrcatA(b2, t2);
        lstrcatA(b2, " f="); wsprintfA(t2, "%ld", g_frames); lstrcatA(b2, t2);
        lstrcatA(b2, " hash耗时μs last/max="); wsprintfA(t2, "%ld", g_hashLastUs); lstrcatA(b2, t2);
        lstrcatA(b2, "/"); wsprintfA(t2, "%ld", g_hashMaxUs); lstrcatA(b2, t2);
        lstrcatA(b2, "\r\n"); HKLOG(b2);
    }
    /* ★ c58：全量规格日志（验证 2：grp4/grp5 等是否也走 UT） */
    {
        SURF_DESC d1, d2; ZeroMemory(&d1, sizeof(d1)); ZeroMemory(&d2, sizeof(d2));
        int ok1 = 0, ok2 = 0;
        void** sv1 = *(void***)src;
        void** dv1 = dst ? *(void***)dst : NULL;
        if (sv1 && sv1[17]) ok1 = (((PFN_GetLevelDesc)sv1[17])(src, 0, &d1) >= 0);
        if (dv1 && dv1[17]) ok2 = (((PFN_GetLevelDesc)dv1[17])(dst, 0, &d2) >= 0);
        char b3[300], t3[64]; b3[0] = 0;
        lstrcatA(b3, "[UTD] src="); wsprintfA(t3, "%p", src); lstrcatA(b3, t3);
        if (ok1) { wsprintfA(t3, " %lux%lu/p%lu", (unsigned long)d1.Width, (unsigned long)d1.Height, (unsigned long)d1.Pool); lstrcatA(b3, t3); }
        lstrcatA(b3, " -> dst="); wsprintfA(t3, "%p", dst); lstrcatA(b3, t3);
        if (ok2) { wsprintfA(t3, " %lux%lu/p%lu", (unsigned long)d2.Width, (unsigned long)d2.Height, (unsigned long)d2.Pool); lstrcatA(b3, t3); }
        lstrcatA(b3, " f="); wsprintfA(t3, "%ld", g_frames); lstrcatA(b3, t3);
        lstrcatA(b3, "\r\n"); HKLOG(b3);
        /* ★ c61：判据 =「src 池 == SYSTEMMEM(2) 的 UT 的 dst」= 已确认字体图集。
         *   ut_dst_add 同时把 g_fontTex 跟踪到【最新已确认指针】——
         *   游戏反复重建图集（c44：10+ 次），旧指针会被 d3d9 堆重用给
         *   场景贴图；g_fontTex 若停留在失效指针就会误替换（白条根因）。
         *   由 UT 确认更新 = 跟随最新真实字体，重用指针不再命中。 */
        if (ok1 && ok2 && d1.Pool == 2 && d2.Width == 512 && d2.Height == 128) {
            ut_dst_add(dst);
            /* ★ c61b：组感知——只有【第一组】（先被确认的字形表）跟随替换 */
            /* ★ c93：td_src_hash 耗时测量（QPC；LockRect→读→UnlockRect 全程） */
            {
                static LARGE_INTEGER qpf; static int qpfOk = 0;
                LARGE_INTEGER t0, t1;
                if (!qpfOk) { QueryPerformanceFrequency(&qpf); qpfOk = 1; }
                QueryPerformanceCounter(&t0);
                unsigned long hh = td_src_hash(src);
                QueryPerformanceCounter(&t1);
                g_hashLastUs = (long)((t1.QuadPart - t0.QuadPart) * 1000000LL /
                                      (qpf.QuadPart ? qpf.QuadPart : 1));
                if (g_hashLastUs > g_hashMaxUs) g_hashMaxUs = g_hashLastUs;
            if (hh) {
                char fb2[240], ft2[64]; fb2[0] = 0;
                if (g_grp1Hash == 0) {
                    /* 首次确认 = 组1 */
                    g_grp1Hash = hh;
                    d6_tex("UTC1", g_fontTex, dst);   /* c82_probe */
                    g_fontTex = dst;
                    grp1_remember(dst);
                    if (!is_font_tex(dst) && g_fontTexN < 8) g_fontTexes[g_fontTexN++] = dst;
                    g_rescan_enable = 1;
                    if (g_rearmPending) g_d2ConfirmFrame = g_frames;   /* ★ c93 */
                    g_d2SConfirm = g_frames;   /* ★ c93e：无条件记录（启动武装计时） */
                    lstrcatA(fb2, "[FONT-CONFIRM] 组1登记 hash="); wsprintfA(ft2, "%08lX", hh); lstrcatA(fb2, ft2);
                    lstrcatA(fb2, " dst="); wsprintfA(ft2, "%p", dst); lstrcatA(fb2, ft2);
                } else if (hh == g_grp1Hash) {
                    /* 组1（首次载体或重建跟随） */
                    d6_tex("UTC2", g_fontTex, dst);   /* c82_probe */
                    g_fontTex = dst;
                    grp1_remember(dst);
                    if (!is_font_tex(dst) && g_fontTexN < 8) g_fontTexes[g_fontTexN++] = dst;
                    g_rescan_enable = 1;
                    if (g_rearmPending) g_d2ConfirmFrame = g_frames;   /* ★ c93 */
                    g_d2SConfirm = g_frames;   /* ★ c93e：无条件记录（启动武装计时） */
                    lstrcatA(fb2, "[FONT-CONFIRM] 组1跟随 dst="); wsprintfA(ft2, "%p", dst); lstrcatA(fb2, ft2);
                } else {
                    /* ★ c62：非组1 内容。若该 dst 正是当前 g_fontTex（暂存纹理被
                     *   复用、内容被改写），必须撤销——否则把组2内容当组1替换=乱码 */
                    grp1_forget(dst);
                    if (g_fontTex == dst) {
                        { void* d6nv = (g_grp1N > 0) ? g_grp1Tex[g_grp1N - 1] : 0;
                          d6_tex("REVOKE", g_fontTex, d6nv); g_fontTex = d6nv; }
                        lstrcatA(fb2, "[FONT-REVOKE] dst 内容已变为非组1 -> 撤销 g_fontTex -> ");
                        wsprintfA(ft2, "%p", g_fontTex); lstrcatA(fb2, ft2);
                    } else {
                        lstrcatA(fb2, "[FONT-OTHER] 其他组放行 hash="); wsprintfA(ft2, "%08lX", hh); lstrcatA(fb2, ft2);
                        lstrcatA(fb2, " dst="); wsprintfA(ft2, "%p", dst); lstrcatA(fb2, ft2);
                    }
                }
                lstrcatA(fb2, " f="); wsprintfA(ft2, "%ld", g_frames); lstrcatA(fb2, ft2);
                lstrcatA(fb2, " hash耗时μs="); wsprintfA(ft2, "%ld", g_hashLastUs); lstrcatA(fb2, ft2);
                lstrcatA(fb2, "/max"); wsprintfA(ft2, "%ld", g_hashMaxUs); lstrcatA(fb2, ft2);
                lstrcatA(fb2, "\r\n");
                HKLOG(fb2);
            }
            }   /* ★ c93 timing 块闭合 */
        }
    }

    TDReg* r = td_find(src);
    TDReg tmp;
    if (!r && src) {
        /* 登记表没有 -> 现场查（GetLevelDesc=texVt[17]，GetLevelCount=texVt[13]） */
        void** sv0 = *(void***)src;
        if (sv0 && sv0[17]) {
            SURF_DESC sd; ZeroMemory(&sd, sizeof(sd));
            if (((PFN_GetLevelDesc)sv0[17])(src, 0, &sd) >= 0 && sd.Width == 512 && sd.Height == 128) {
                unsigned long lvc = 1;
                if (sv0[13]) lvc = (unsigned long)((long (WINAPI*)(void*))(sv0[13]))(src);
                tmp.tex = src; tmp.w = sd.Width; tmp.h = sd.Height;
                tmp.levels = lvc; tmp.usage = 0xFFFFFFFF; tmp.fmt = sd.Format;
                tmp.pool = sd.Pool; tmp.frame = g_frames;
                r = &tmp;
            }
        }
    }
    if (r && r->w == 512 && r->h == 128 && g_tdDumpedN < TDMAXDUMP && !td_already(src)) {
        g_tdDumped[g_tdDumpedN++] = src;
        CreateDirectoryA(TDDIR, NULL);
        td_dump_src(src, dst, r);
    }
    if (!g_orig_UpdTex) return -1;
    return g_orig_UpdTex(self, src, dst);
}

static long WINAPI Hook_CreateTexture(void* self, unsigned int w, unsigned int h,
                                      unsigned int levels, unsigned long usage,
                                      unsigned long fmt, unsigned long pool,
                                      void** outTex, void* shared) {
    g_ckpt = "CT:1-enter";
    /* 诊断：记录游戏自己创建的所有纹理（前 40 个） */
    if (g_ctLogN < 40) {
        g_ctLogN++;
        char b[220], t[64];
        b[0] = 0;
        lstrcatA(b, "[CT] "); wsprintfA(t, "%ux%u", w, h); lstrcatA(b, t);
        lstrcatA(b, " fmt="); wsprintfA(t, "%lu", fmt); lstrcatA(b, t);
        lstrcatA(b, " pool="); wsprintfA(t, "%lu", pool); lstrcatA(b, t);
        lstrcatA(b, " usage="); wsprintfA(t, "%lu", usage); lstrcatA(b, t);   /* ★ c93d 采数 */
        lstrcatA(b, " lv="); wsprintfA(t, "%lu", levels); lstrcatA(b, t);
        lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
    /* ★ 字体图集规格（512x128 A8R8G8B8 DEFAULT）—— 诊断用，暂不强制改池。
     *   实测（c30）：字体图集是 lv=8 的多级渐变纹理，且游戏配套创建了 pool=2
     *   (SYSTEMMEM) 孪生纹理走 UpdateTexture 上传。强行改成 MANAGED 会打断
     *   游戏的「系统内存暂存 + 上传」流程 -> 菜单文字直接不显示。
     *   故此处不再改池，仅记录。 */
    if (w == 512 && h == 128 && fmt == 21 && pool == 0) {
        {   /* ★ c93d 采数：字体规格纹理的 usage（不受 40 条上限影响） */
            char bb[220], tt[64]; bb[0] = 0;
            lstrcatA(bb, "[CT] (字体图集规格 512x128 fmt=21 pool=0) usage="); wsprintfA(tt, "%lu", usage); lstrcatA(bb, tt);
            wsprintfA(tt, " f=%ld", g_frames); lstrcatA(bb, tt);
            lstrcatA(bb, "\r\n");
            HKLOG(bb);
        }
        HKLOG("[CT] (字体图集规格 512x128 A8R8G8B8 DEFAULT，不改池)\r\n");
        /* ★ 探针 v2：512x128 纹理的【创建点栈】（一次性事件，栈最稳定）
         *   方案 D 的数据来源：字体图集的创建栈 = 唯一身份标识 */
        {
            char pb2[300], pt2[64];
#if P1_DIAG_PROBES
            void* cra0 = __builtin_return_address(0);
            void* cra1 = __builtin_return_address(1);
            char ci0[160], ci1[160];
            probe_mod_info(cra0, "ca0", ci0, sizeof(ci0));
            probe_mod_info(cra1, "ca1", ci1, sizeof(ci1));
            pb2[0] = 0;
            wsprintfA(pb2, "[CTP] CREATE %ux%u fmt=%lu pool=%lu f=%ld",
                      w, h, fmt, pool, g_frames);
            lstrcatA(pb2, "\r\n[CTP]   ca0_abs="); wsprintfA(pt2, "%p", cra0); lstrcatA(pb2, pt2);
            lstrcatA(pb2, "  "); lstrcatA(pb2, ci0);
            lstrcatA(pb2, "\r\n[CTP]   ca1_abs="); wsprintfA(pt2, "%p", cra1); lstrcatA(pb2, pt2);
            lstrcatA(pb2, "  "); lstrcatA(pb2, ci1);
            lstrcatA(pb2, "\r\n");
            HKLOG(pb2);
            probe_log_stack("Create", cra0, cra1, 0);
#endif  /* P1_DIAG_PROBES：[CTP] Create 栈探针 */
            /* c57: 停用 c56 栈扫描 */
        }
    }
    g_ckpt = "CT:2-origCall";
    long hr = g_orig_CreateTex(self, w, h, levels, usage, fmt, pool, outTex, shared);
    g_ckpt = "CT:3-afterCall";
    /* ★ c57 探针：登记纹理规格（供 UpdateTexture 时判定 + dump） */
    if (hr >= 0 && outTex && *outTex) td_register(*outTex, w, h, levels, usage, fmt, pool);
    /* ★ c82_probe：512x128 纹理登记到 D6 表（全量绑定计数用） */
    if (hr >= 0 && outTex && *outTex && w == 512 && h == 128) d6_note(*outTex, w, h, (long)levels);

    /* ★★★ Reset 后重建（2026-10-07 晚，正确方向；2026-10-08 去除扫描）：
     *   Reset 销毁了 DEFAULT 资源，游戏会新建一张 512x128 A8R8G8B8 DEFAULT 的
     *   字体图集。这里【直接记录它返回的指针】，比事后靠 SetTexture 间接识别
     *   可靠得多（实测 Reset 后游戏不再调用 SetTexture）。抓到之后
     *   等 SetTexture 确认绑定，再由门禁在同一帧内完成「改参数 + 换纹理」。 */
    if (hr >= 0 && outTex && *outTex && g_postResetNeedRebuild &&
        w == 512 && h == 128 && fmt == 21 && pool == 0) {
        d6_tex("ResetRebuild", g_fontTex, *outTex);   /* c82_probe */
        g_fontTex = *outTex;
        if (!is_font_tex(*outTex) && g_fontTexN < 8) g_fontTexes[g_fontTexN++] = *outTex;
        g_rescan_enable = 1;
        g_postResetNeedRebuild = 0;
        /* ★★★ 修复2（2026-10-08）：
         *   【不再在这里开闸/打补丁】—— 实测游戏 Reset 后用旁路（绕过 SetTexture）
         *   把 stage0 换成它自己这张 512×128 图集；若此刻就改 +12=1024，
         *   中间这段（纹理还是 128 高）就会竖条。
         *   改为：只记录指针，交给 Present 的【绑定看门狗】在确认
         *   stage0 == 本纹理时同帧完成「换大图集 + 重打参数补丁」。 */
        g_rearmPending = 1;
        g_rearmUntil   = g_frames + 1800;
        g_rearmDone    = 0;
        {
            char bb[240], tt[64];
            bb[0] = 0;
            lstrcatA(bb, "[RESET] ★ 抓到新字体纹理 tex="); wsprintfA(tt, "%p", *outTex); lstrcatA(bb, tt);
            lstrcatA(bb, " -> 等绑定看门狗确认后再恢复补丁\r\n");
            HKLOG(bb);
        }
    }
    /* ★ 首次识别（2026-10-07 晚加固）：若还没识别到字体纹理，就用 CreateTexture
     *   返回的指针先【记下来】。理由：某些运行里游戏整局都不调用 SetTexture 绑
     *   字体纹理（实测 c36 那次 font绑定=0），只靠 SetTexture 永远认不出来。
     *   ★ 但这里【不打补丁】：必须先由 SetTexture 确认游戏真的会绑这张纹理，
     *     才能保证「改参数」与「换纹理」同进。否则只改参数不换纹理 -> 竖条。 */
    /* ★ c61：删除「首张抢占」——CT 时刻 UT 尚未发生，无法区分字体/贴图；
     *   实测 CT 与 UT 同帧（真空期 0 帧），字体必然经 UT 确认，
     *   由 Hook_UpdateTexture 的 [FONT-CONFIRM] 统一设置 g_fontTex。
     *   这里只登记候选（诊断用：统计从未走 UT 的 512x128 = 潜在误伤源）。 */
    else if (hr >= 0 && outTex && *outTex &&
             w == 512 && h == 128 && fmt == 21 && pool == 0) {
        td_cand_add(*outTex);
        char rb[220], rt[64];
        rb[0] = 0;
        lstrcatA(rb, "[CAND] 512x128 候选登记 tex="); wsprintfA(rt, "%p", *outTex); lstrcatA(rb, rt);
        lstrcatA(rb, " f="); wsprintfA(rt, "%ld", g_frames); lstrcatA(rb, rt);
        lstrcatA(rb, "（等待 UT 确认）\r\n");
        HKLOG(rb);
    }
    return hr;
}

/* ============================================================
 *  ★★ DrawIndexedPrimitive (vt[82]) 诊断 + 强制绑定
 *   背景（2026-10-07 Alt-Tab 乱码根因）：
 *   Reset 后游戏的「D3D 状态缓存」失效，游戏认为字体纹理仍绑定着，
 *   于是【不再调用 SetTexture】-> D3D 用默认白纹理绘制文字 -> 白竖条。
 *   证据：Reset 后 [VERB] 全量日志窗口内 SetTexture 调用数=0。
 *   对策：在字体绘制时由我们主动把大纹理绑上去（见 g_forceMenu）。
 * ============================================================ */
typedef long (WINAPI *PFN_DIP)(void*, unsigned int, unsigned int, unsigned int,
                              unsigned int, unsigned int, unsigned int, void*);
static PFN_DIP g_orig_DIP = NULL;
/* DrawPrimitiveUP (vt[83])：void DrawPrimitiveUP(D3DPRIMITIVETYPE, UINT PrimitiveCount,
 *                                                   const void* pVertexStreamZeroData,
 *                                                   UINT VertexStreamZeroStride) */
typedef long (WINAPI *PFN_DPUP)(void*, unsigned int, unsigned int, const void*, unsigned int);
static PFN_DPUP g_orig_DPUP = NULL;
/* DrawIndexedPrimitiveUP (vt[84]) */
typedef long (WINAPI *PFN_DIPUP)(void*, unsigned int, unsigned int, unsigned int, unsigned int,
                                 const void*, unsigned int, const void*, unsigned int);
static PFN_DIPUP g_orig_DIPUP = NULL;
/* ★ DrawPrimitive (vt[81])：void DrawPrimitive(D3DPRIMITIVETYPE, UINT StartVertex, UINT PrimitiveCount)
 *   —— 2026-10-08 新增：实测本引擎不用 82/83/84（计数全 0），主绘制路径很可能是 81。 */
typedef long (WINAPI *PFN_DP)(void*, unsigned int, unsigned int, unsigned int);
static PFN_DP g_orig_DP = NULL;
static long g_drawCntDP = 0;
static long g_dipLogN = 0;

/* ============================================================
 * ★★★ 修复2b（2026-10-08）：绘制前 JIT 纹理替换（本版核心）
 *   实测（c40 探针，铁证）：
 *     Reset 后 stage0 从 NULL 变成【游戏自己新建的 512×128 图集】，
 *     而整个过程 SetTexture 调用数 = 0！
 *     => 游戏用了绕过 SetTexture 的绑定路径（状态块 Apply 等）。
 *   => "在 SetTexture 里换纹理" 与 "等 SetTexture 开闸" 都不成立。
 *   对策：在所有 Draw* 入口前检查 stage0 —— 只要绑着的是游戏字体纹理，
 *         立即换成我们的大图集。无论游戏走哪条路径绑定，绘制前一刻都会被纠正。
 *         参数补丁(+12=1024)与本替换同进同退 => 物理上不可能竖条。
 * ============================================================ */
static long g_jitN = 0;
/* ============================================================
 * ★★★ 修复2b（2026-10-08，低风险版）：Present 每帧「绑定看门狗」
 *   c41 的教训：在 Draw 钩子里逐次查 GetTexture（38 万次）会在设备丢失
 *   期间反复查询，疑似污染设备状态 -> 真正的 Reset 返回 DEVICELOST、
 *   游戏白屏卡死（hr=0x8876086C）。
 *   改为：只在 Present（每帧 1 次，60/秒）检查 stage0：
 *     - 先用 TestCooperativeLevel 确认设备可用（丢失时一律不动）；
 *     - 若 stage0 == 游戏字体纹理（Reset 后游戏用旁路重绑了它）
 *       -> 立刻换成我们的大图集，并（若尚未开闸）同帧重打参数补丁。
 *   实测旁路绑定是「一次性持久」的（c40：stage0=1AA93F20 持续数千帧），
 *   因此帧末纠正即可让下一帧完全正确，且不产生任何中间竖条状态。
 * ============================================================ */
typedef long (WINAPI *PFN_TestCoop)(void*);
static int watchdog_swap(void* dev) {
#if TEX_INTERVENE == 0
    return 0;      /* c46 二分实验：绝不碰纹理绑定 */
#endif
    /* ★ 只在【已开闸】(参数已打补丁) 后才做交换：此时纹理必须是我们的 1024 图集，
     *   否则参数(1024) 与游戏纹理(128) 错配 -> 竖条。
     *   未开闸时不动 —— 由 SetTexture 门禁在下一次绑定时同帧完成「换纹理+改参数」。
     *   ★★ c45 修正：Reset 之后 g_fontTexBound 被清零，但游戏可能用【旁路】绑定
     *   （绕过 SetTexture，见 c40 探针：Reset 后 SetTexture 调用数 = 0）。
     *   所以这里允许「未开闸但stage0 确实是游戏字体纹理」时【同帧开闸+换纹理+打补丁】，
     *   三者在同一帧完成 -> 不会竖条。这正是Reset 后唯一的恢复路径。 */
    if (!g_fontTex || !g_bigTex || !dev || !g_orig_SetTexture) return 0;
    void** vt = *(void***)dev;
    if (!vt || !vt[3]) return 0;
    /* ★ 只在设备完全可用时动作（丢失/待重置时一律不碰纹理） */
    if (((PFN_TestCoop)vt[3])(dev) != 0) return 0;
    void* b0 = get_bound_tex0(dev);
    if (b0 != g_fontTex) {
        /* ★ c91 诊断（限流）：确认看门狗"在运行但卡在 b0"
         *   b0 恒 NULL 是 EXP_NO_QUERY 造成的（get_bound_tex0 直接 return NULL）。 */
        static long wdB0N = 0;
        wdB0N++;
        if (wdB0N <= 6 || (wdB0N % 600) == 0) {
            char wb[220], wt[64];
            wb[0] = 0;
            lstrcatA(wb, "[WD-B0] 看门狗因 stage0 不匹配退出 #"); wsprintfA(wt, "%ld", wdB0N); lstrcatA(wb, wt);
            lstrcatA(wb, " b0="); wsprintfA(wt, "%p", b0); lstrcatA(wb, wt);
            lstrcatA(wb, " fontTex="); wsprintfA(wt, "%p", g_fontTex); lstrcatA(wb, wt);
            lstrcatA(wb, " rearm="); wsprintfA(wt, "%d", g_rearmPending); lstrcatA(wb, wt);
            lstrcatA(wb, " bound="); wsprintfA(wt, "%ld", g_fontTexBound); lstrcatA(wb, wt);
            lstrcatA(wb, " f="); wsprintfA(wt, "%ld", g_frames); lstrcatA(wb, wt);
            lstrcatA(wb, "\r\n");
            HKLOG(wb);
        }
        return 0;
    }
    /* stage0 上是游戏的 512×128 图集 -> 换成我们的 512×1024 大图集 */
    g_orig_SetTexture(dev, 0, g_bigTex);
    if (!g_fontTexBound) {
        /* ★ 同帧开闸 + 打参数补丁（与换纹理同一帧 -> 参数与纹理同进同退） */
        g_fontTexBound = 1;
        g_rearmPending = 0;
        g_rearmDone    = 1;
        apply_params_fast();
        HKLOG("[RESET] 看门狗确认 stage0=游戏字体图集 -> 开闸+换大图集+同帧打补丁\r\n");
    }
    g_jitN++;
    if (g_jitN <= 12) {
        char b[260], t[64];
        b[0] = 0;
        lstrcatA(b, "[WATCH] stage0=游戏字体纹理 -> 换大图集");
        lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
        lstrcatA(b, " 累计="); wsprintfA(t, "%ld", g_jitN); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
    return 1;
}

/* （原 try_rearm 已由 watchdog_swap 取代：看门狗在确认 stage0 上是游戏字体纹理时
 *   才同帧完成「换大图集 + 重打参数补丁」，比"抓到 CreateTexture 就开闸"更安全。） */

/* ============================================================
 * ★★★ c93 D2 门禁（EXP_D2_GATE=1 时启用）
 *  全局状态定义在 Hook_UpdateTexture 之前（供其打时间戳），见上方。
 *  触发体 d2_try 在 watchdog_swap 之后定义（依赖 PFN_TestCoop/apply_params_fast）。
 */
#if EXP_D2_GATE
/* ★ D2 触发体：在 DrawPrimitive 入口调用；不满足条件时 O(1) 退出。
 *  同帧原子动作 = SetTexture(0,bigTex) + apply_params_fast + 开闸，
 *  与 c45 看门狗成功路径（watchdog_swap 2637-2646）完全同构，
 *  差别仅在「不读 stage0，直接覆盖」。 */
static void d2_try(void* dev) {
    /* ★★★ c93e 改动B：启动武装（会话级预算 1 次）
     *  场景：Steam 启动后游戏可能从不通过 hooked SetTexture 绑字体图集
     *        （实测 font绑定=0 持续 6600 帧）-> gate 永不开 -> 无中文。
     *  条件：无 rearm（未发生 Reset）+ gate 未开 + 预算未用 + 组1已确认
     *        + 确认后 150 帧稳定期 + 纯 UI 百帧窗 + 设备可用。
     *  动作：同帧 SetTexture(0,bigTex) + apply_params_fast + 开闸。
     *  Reset 发生 -> 预算作废，后续全由 rearm/D2 路径接管。 */
    if (!g_rearmPending && !g_fontTexBound && g_d2SBudget
        && g_d2SConfirm > 0 && g_fontTex && g_bigTex && g_orig_SetTexture) {
        if (g_frames - g_d2SConfirm < 150 || !g_dipWinZero) return;
        {
            void** vt = *(void***)dev;
            char b[280], t[64];
            if (!vt || !vt[3]) return;
            if (((PFN_TestCoop)vt[3])(dev) != 0) return;
            g_orig_SetTexture(dev, 0, g_bigTex);
            g_fontTexBound = 1;
            g_d2SBudget    = 0;
            apply_params_fast();
            b[0] = 0;
            lstrcatA(b, "[D2S] ★ 启动武装: 强制绑大图集+开闸 confirm@"); wsprintfA(t, "%ld", g_d2SConfirm); lstrcatA(b, t);
            lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
            lstrcatA(b, " mode="); lstrcatA(b, g_swapModeB ? "B" : "A");
            lstrcatA(b, "\r\n");
            HKLOG(b);
        }
        return;
    }
    if (!g_rearmPending || g_d2Tried) return;          /* 1) 单次性 + 待恢复 */
    if (!g_fontTex || !g_bigTex || !g_orig_SetTexture) return;
    if (g_d2ResetFrame < 0 ||
        g_d2ConfirmFrame < g_d2ResetFrame ||           /* 2) 哈希确认不早于 Reset
                                                            （★ c93b：同帧合法——实测
                                                            Reset/大纹/确认同帧发生） */
        g_d2BigTexFrame  < g_d2ResetFrame) return;     /* 3) 大纹重建不早于 Reset */
    long stable_since = (g_d2ConfirmFrame > g_d2BigTexFrame) ? g_d2ConfirmFrame
                                                             : g_d2BigTexFrame;
    if (g_frames - stable_since < 150) return;         /* 4) 稳定期 */
    if (!g_dipWinZero) return;                         /* 5) 纯 UI 场景 */
    void** vt = *(void***)dev;
    if (!vt || !vt[3]) return;
    if (((PFN_TestCoop)vt[3])(dev) != 0) return;       /* 设备不可用一律不动 */
    g_orig_SetTexture(dev, 0, g_bigTex);               /* ← 同帧三件事 */
    g_fontTexBound = 1;
    g_rearmPending = 0;
    g_rearmDone    = 1;
    g_d2Tried      = 1;
    apply_params_fast();
    g_d2N++;
    {
        char b[280], t[64]; b[0] = 0;
        lstrcatA(b, "[D2] ★ 同帧自绑大图集+开闸: reset@"); wsprintfA(t, "%ld", g_d2ResetFrame); lstrcatA(b, t);
        lstrcatA(b, " confirm@"); wsprintfA(t, "%ld", g_d2ConfirmFrame); lstrcatA(b, t);
        lstrcatA(b, " bigtex@"); wsprintfA(t, "%ld", g_d2BigTexFrame); lstrcatA(b, t);
        lstrcatA(b, " f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
        lstrcatA(b, " 累计="); wsprintfA(t, "%ld", g_d2N); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
}
#else
static void d2_try(void* dev) { (void)dev; }
#endif

static void dip_trace(const char* tag, void* self) {
    if (g_verboseUntil && g_frames < g_verboseUntil && g_dipLogN < 300) {
        g_dipLogN++;
        DWORD vra0 = (DWORD)((char*)__builtin_return_address(0) - (char*)GetModuleHandleA(NULL));
        DWORD vra1 = (DWORD)((char*)__builtin_return_address(1) - (char*)GetModuleHandleA(NULL));
        char b[220], t[64];
        b[0] = 0;
        lstrcatA(b, "[DRAW/"); lstrcatA(b, tag); lstrcatA(b, "] f=");
        wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
        lstrcatA(b, " ra0=exe+0x"); wsprintfA(t, "%lX", vra0); lstrcatA(b, t);
        lstrcatA(b, " ra1=exe+0x"); wsprintfA(t, "%lX", vra1); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
}

/* ★ DrawPrimitive (vt[81])：主绘制路径 —— 只做计数（c41 曾在此做 JIT，
 *   实测每次绘制都查 GetTexture（38 万次）会在设备丢失时污染状态 -> Reset 失败，
 *   已改为 Present 每帧一次的【绑定看门狗】，见 watchdog_swap） */
static long WINAPI Hook_DP(void* self, unsigned int type, unsigned int start, unsigned int count) {
    g_drawCntDP++;
    d2_try(self);   /* ★ c93：D2 门禁（EXP_D2_GATE=1 时生效；内部 O(1) 早退） */
    return g_orig_DP(self, type, start, count);
}

static long WINAPI Hook_DIP(void* self, unsigned int type, unsigned int baseVertexIndex,
                            unsigned int minIndex, unsigned int numVertices,
                            unsigned int startIndex, unsigned int primCount, void* idx) {
    g_drawCntDIP++;
    dip_trace("DIP", self);
    return g_orig_DIP(self, type, baseVertexIndex, minIndex, numVertices,
                      startIndex, primCount, idx);
}

static long WINAPI Hook_DPUP(void* self, unsigned int type, unsigned int primCount,
                             const void* vtx, unsigned int stride) {
    g_drawCntUP++;
    dip_trace("UP", self);
    return g_orig_DPUP(self, type, primCount, vtx, stride);
}

static long WINAPI Hook_DIPUP(void* self, unsigned int type, unsigned int minVtx,
                              unsigned int numVtx, unsigned int primCount,
                              const void* idxData, unsigned int idxFmt,
                              const void* vtxData, unsigned int vtxStride) {
    g_drawCntIUP++;
    dip_trace("IUP", self);
    return g_orig_DIPUP(self, type, minVtx, numVtx, primCount, idxData, idxFmt,
                        vtxData, vtxStride);
}

/* ============================================================
 *  ★★ IDirect3DDevice9::Reset (vt[16]) Hook
 *   Alt-Tab 切出再切回时，引擎会 Reset 设备：
 *   D3DPOOL_DEFAULT 资源（含我们创建的大纹理）会被销毁重建，
 *   缓存的纹理指针 / 字体对象指针全部失效 -> 字体再次乱掉。
 *   处理：Reset 前清空所有缓存；Reset 后重新扫描 + 重建大纹理 + 重换表。
 * ============================================================ */
typedef long (WINAPI *PFN_Reset)(void*, void*);
static PFN_Reset g_orig_Reset = NULL;

/* 清空全部缓存（Reset 前调用） */
static void clear_all_caches(void) {
    g_bigTex      = NULL;      /* 大纹理由 D3DPOOL_DEFAULT 管理，Reset 后失效 */
    g_fontTex     = NULL;      /* 字体纹理指针失效 */
    g_fontTexN    = 0;
    for (int i = 0; i < 8; i++) g_fontTexes[i] = NULL;
    g_fontObj     = 0;
    g_fontObjN    = 0;
    g_obj512N     = 0;
    g_allN        = 0;
    g_doneHN      = 0;         /* 已改高的对象记录清空 -> 重扫后会重新改 */
    g_doneTN      = 0;
    g_rescan_n    = 0;         /* 允许重新定时重扫 */
    g_swapLogN    = 0;         /* 诊断计数复位 */
    ZeroMemory(g_fontObjs, sizeof(g_fontObjs));
    ZeroMemory(g_obj512,  sizeof(g_obj512));
    ZeroMemory(g_doneH,   sizeof(g_doneH));
    ZeroMemory(g_doneT,   sizeof(g_doneT));
    /* 纹理尺寸缓存也要清：同一指针可能已变成另一张纹理 */
    ZeroMemory(g_texCache, sizeof(g_texCache));
    HKLOG("[RESET] 缓存已清空（Reset 前）\r\n");
}

/* ★★★ 【正确方向】Reset 修复（2026-10-07 晚定稿）。
 *
 *   已废弃的「路线 A」（拦截 Reset、直接返回 D3D_OK）是错的：
 *   它违反 D3D9 规范，设备状态与后备缓冲越来越不一致 -> Alt-Tab 后直接卡死。
 *   现在改为【调用真正的 Reset】，并按核心原则处理补丁：
 *
 *       「补丁状态与纹理状态同进同退」
 *
 *   真正的 Reset 会销毁所有 D3DPOOL_DEFAULT 资源（游戏字体图集就在这里）。
 *   游戏随后会重建字体图集，但我们之前把字体组参数（+0 表指针 / +4 记录数 /
 *   +8 图集宽 / +12 图集高）改成了指向 512x1024 大表——这些参数在 Reset 后
 *   会与新重建的纹理不一致 -> 白条/乱码。
 *
 *   正确流程：
 *     ① Reset 前：restore_all_orig() 把字体组 4 个字段恢复成原值（原表 / 512x128），
 *        并清掉会随 Reset 失效的纹理缓存（g_fontTex / g_fontTexes / g_texCache）。
 *     ② 调用【真正的】g_orig_Reset —— 绝不拦截。
 *     ③ Reset 后：置 g_postResetNeedRebuild，交给 CreateTexture(vt[23]) 钩子
 *        直接记录游戏新建的字体纹理指针；Present 每帧重试补丁，
 *        识别到之后在同一帧把字段改回大图集并开始替换。
 *
 *   注意：我们的 g_bigTex 是 D3DPOOL_MANAGED(1) 纹理，Reset 时由 D3D 运行时
 *   自动重建、【指针保持不变】，所以不清空、不重建，直接继续用。 */
static long g_resetCount = 0;

static long WINAPI Hook_Reset(void* self, void* pp) {
    g_resetCount++;
    /* ★ c61：Reset 销毁全部 D3D 资源 -> 字体集合/候选全部失效，必须清空。
     *   Reset 后游戏会重新 CT+UT 重建图集（c50 时代已实测），
     *   新一轮 [FONT-CONFIRM] 会重新填充集合并设置 g_fontTex。 */
    {
        char rb2[160], rt2[64]; rb2[0] = 0;
        lstrcatA(rb2, "[RESET] 清空字体集合(曾确认 "); wsprintfA(rt2, "%d", g_utDstSetN); lstrcatA(rb2, rt2);
        lstrcatA(rb2, " 张/候选 "); wsprintfA(rt2, "%d", g_tdCandN); lstrcatA(rb2, rt2);
        lstrcatA(rb2, " 张) 等待重建管线\r\n");
        HKLOG(rb2);
        g_utDstSetN = 0;
        g_tdCandN   = 0;
        g_grp1Hash  = 0;      /* 组1 重建后由新一轮 [FONT-CONFIRM] 重新登记 */
        g_grp1N     = 0;      /* 组1 载体历史同样失效 */
    }
    char b[240], t[64];
    g_ckpt = "R:0-unbind";

    /*★★★★★★★★★★ c47 关键修复：Reset 前必须【解绑我们的大纹理】 ★★★★★★★★★★
     * 二分实验（c46 vs c45）已确证：
     *   - c46（零纹理干预）：Reset 返回 hr=0x0【成功】，Alt-Tab 完全正常。
     *   - c45（含大纹理+参数干预）：Reset 返回 0x8876086C = D3DERR_INVALIDCALL，
     *     且 TestCooperativeLevel 返回 0（设备【健康】）-> 白屏。
     *   => INVALIDCALL 的成因就是我们的纹理【仍绑定在 stage 上】：
     *      D3D9 要求Reset 时没有任何纹理处于绑定状态。
     *
     * 做法：Reset 前把所有 stage 的绑定设成 NULL（游戏自己的纹理我们不碰，
     *      让游戏在Reset 后自己重绑），然后才调真正的 Reset。*/
    /* ★★ c66（#2 实验版）：删除【查询式解绑】——单一变量实验。
     *   假设：设备已丢失（devAlive=0）时，本块的 16 次 GetTexture 查询违反铁律
     *   「设备丢失期间绝不能调用任何其它 D3D9 API」-> 锁死设备 -> Reset INVALIDCALL。
     *   证据链：c65 已把 g_bigTex Release 到 rc=0（DEFAULT 资源清零）仍返回 INVALIDCALL；
     *           c46（零干预=零丢失期调用）Reset=0x0 成功。
     *   本版彻底不查询、不解绑（c65 实测 Reset 时 0 个 stage 绑定 g_bigTex，不影响本测试）；
     *   若本版 Reset 成功 => 假设成立，c67 再补回【账本式、零查询】的解绑机制。 */
    {
        int aliveNow = dev_alive(self);       /* TestCoop = 丢失期唯一被允许的调用 */
        b[0] = 0;
        lstrcatA(b, "[RESET] c66实验：跳过查询式解绑。devAlive="); wsprintfA(t, "%d", aliveNow); lstrcatA(b, t);
        lstrcatA(b, " bigTex="); wsprintfA(t, "%p", g_bigTex); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }

    /* ══════ c85（#2 第2步诊断·修正版）：Reset 前 DEFAULT 资源全景快照 ══════
     * 【零 D3D API 调用】—— 这是 c83 的事故修正（2026-10-10）。
     *
     * ★ c83 的错误（白屏事故根因）：
     *   旧 [RSD-C] 对【每个】DEFAULT 纹理调 `tv[2](tex)`（AddRef）读引用计数。
     *   但此时 devAlive=0（设备已丢失），项目铁律明确：
     *   「设备丢失期间绝不能调用任何其它 D3D9 API，否则会把设备彻底锁死」。
     *   实测：3D 场景切屏 Reset 前有 61 个 DEFAULT 纹理 -> 61 次 AddRef/Release
     *   -> 进程停在 [RSD-C] 列表中途、打印不出 [RSD-D]、Reset 永不返回 -> 白屏。
     *
     * ★ 本版原则：只读【我们自己的 g_texCache 记账字段】，
     *   **一次虚函数都不调**（不 AddRef、不 Release、不 GetLevelDesc、不 GetTexture）。
     *   尺寸/fmt/pool/hits 全部来自 CreateTexture/SetTexture 时我们自己存的值。
     *
     * ★ c86 脱水（2026-10-10）：本快照用 RSD_DIAG 条件编译包裹，默认 0=关闭。
     *   #2 已验收通过（24/24 Reset 成功），诊断日志属残留开销，发布版关闭。
     *   需要复现时编译加 -DRSD_DIAG=1 即可恢复，逻辑一字不变。 */
#ifndef RSD_DIAG
#define RSD_DIAG 0
#endif
#if RSD_DIAG
    {
        char sb[512], st[96];
        int nSlot = 0, nDef = 0, nMan = 0, nOther = 0, nDefList = 0;
        long sumHitsDef = 0;
        sb[0] = 0;
        for (int i = 0; i < TEXCACHE_SIZE; i++) {
            if (!g_texCache[i].tex) continue;
            nSlot++;
            if (g_texCache[i].pool == 0) { nDef++; sumHitsDef += g_texCache[i].hits; }
            else if (g_texCache[i].pool == 1) nMan++;
            else nOther++;
        }
        wsprintfA(st, "f=%ld ", g_frames); lstrcatA(sb, st);
        wsprintfA(st, "bigTex=%p ", g_bigTex); lstrcatA(sb, st);
        wsprintfA(st, "texCache槽=%d ", nSlot); lstrcatA(sb, st);
        wsprintfA(st, "DEFAULT=%d ", nDef); lstrcatA(sb, st);
        wsprintfA(st, "MANAGED=%d ", nMan); lstrcatA(sb, st);
        wsprintfA(st, "其它池=%d ", nOther); lstrcatA(sb, st);
        wsprintfA(st, "DEFAULT_hits总=%ld ", sumHitsDef); lstrcatA(sb, st);
        wsprintfA(st, "ut_dst=%d ", g_utDstSetN); lstrcatA(sb, st);
        wsprintfA(st, "字体组=%d ", g_grp1N); lstrcatA(sb, st);
        lstrcatA(sb, "\r\n");
        HKLOG("[RSD-A] ==== Reset 前 DEFAULT 资源全景（零API调用版）====");
        HKLOG(sb);

        /* 逐个列出 DEFAULT 纹理 —— 【只读自家缓存字段，零 D3D 调用】 */
        for (int i = 0; i < TEXCACHE_SIZE; i++) {
            if (!g_texCache[i].tex) continue;
            if (g_texCache[i].pool != 0) continue;    /* 只列 DEFAULT */
            if (nDefList >= 40) break;                 /* 上限保护 */
            nDefList++;
            void* tex = g_texCache[i].tex;
            char sb2[300], st2[96]; sb2[0] = 0;
            wsprintfA(st2, "[RSD-C] tex=%p ", tex); lstrcatA(sb2, st2);
            wsprintfA(st2, "%lux%lu ", g_texCache[i].w, g_texCache[i].h); lstrcatA(sb2, st2);
            wsprintfA(st2, "fmt=%lu ", g_texCache[i].fmt); lstrcatA(sb2, st2);
            wsprintfA(st2, "hits=%ld", g_texCache[i].hits); lstrcatA(sb2, st2);
            if (tex == g_bigTex) lstrcatA(sb2, "   <<< 我们的大图集");
            if (g_texCache[i].w == 512 && g_texCache[i].h == 128)
                lstrcatA(sb2, "   <<< 512x128 字体图集");
            lstrcatA(sb2, "\r\n");
            HKLOG(sb2);
        }
        if (nDef > 40) {
            char sb3[120]; wsprintfA(sb3, "[RSD-C] ...(共 %d 个 DEFAULT，只列前 40)\r\n", nDef);
            HKLOG(sb3);
        }
        HKLOG("[RSD-D] ==== 快照结束（下一行开始正常 Reset 流程）====\r\n");
    }
#endif /* RSD_DIAG */

    /* ★★ c65（#2 第2步）：Reset 前【释放】我们创建的 DEFAULT 池大纹理。
     *   D3D9 铁律：Reset 前必须释放所有 D3DPOOL_DEFAULT 资源，否则 Reset
     *   返回 D3DERR_INVALIDCALL(0x8876086C) -> 白屏（#2 根因）。
     *   ★ g_bigTex 实际池 = DEFAULT(0)；旧注释"MANAGED 存活"是历史错误（已修正）。
     *   ★ Release 返回的【剩余引用计数】= 诊断关键：0=已销毁；>0=仍有泄漏引用。 */
    if (g_bigTex) {
        void** bv = *(void***)g_bigTex;
        unsigned long rc = 0xFFFFFFFFUL;
        if (bv && bv[2]) rc = ((PFN_COMRel)bv[2])(g_bigTex);
        {
            char rb3[220], rt3[64]; rb3[0] = 0;
            lstrcatA(rb3, "[RESET] Release g_bigTex="); wsprintfA(rt3, "%p", g_bigTex); lstrcatA(rb3, rt3);
            lstrcatA(rb3, " -> 剩余引用计数="); wsprintfA(rt3, "%lu", rc); lstrcatA(rb3, rt3);
            lstrcatA(rb3, " (0=已销毁, >0=仍有泄漏引用) f="); wsprintfA(rt3, "%ld", g_frames); lstrcatA(rb3, rt3);
            lstrcatA(rb3, "\r\n");
            HKLOG(rb3);
        }
        g_bigTex = NULL;      /* ★ 立即置 NULL：Reset 后由 CreateBigTexture 重建 */
    }

    g_ckpt = "R:1-restoreOrig";

    /* ①【Reset 之前】补丁撤退：字体组字段恢复原值 + 清失效缓存 */
    restore_all_orig();
    g_rstOldFontTex = g_fontTex;             /* ★ 诊断：记住旧字体纹理指针 */
    g_fontTex   = NULL;                       /* 游戏字体纹理指针随 DEFAULT 销毁而失效 */
    g_fontTexN  = 0;
    g_fontTexBound = 0;                       /* 重新绑定确认前不再打补丁 */
    for (int i = 0; i < 8; i++) g_fontTexes[i] = NULL;
    ZeroMemory(g_texCache, sizeof(g_texCache));/* 纹理尺寸缓存失效 */
    g_doneHN = 0; g_doneTN = 0;               /* 允许重新打补丁 */
    /* ★★ 2026-10-08：不再清空字体组对象缓存（g_allObj/g_obj512/g_fontObjs/g_fontObj）。
     *   字体组是【游戏数据对象】，不是 D3D 资源 —— Reset 不销毁它们，
     *   地址在整个进程生命周期内稳定不变（跨 5 次 Reset 实测同址 0x95F688C）。
     *   保留缓存 => Reset 后重打补丁是纯字段写入、零扫描。
     *   注：不清 g_fontOrig（重打补丁仍要拿原值）；g_bigTex 已在上方 Release 并置 NULL
     *       （★ c65 修正：它是 DEFAULT(0) 池，Reset 前必须释放，旧注释"MANAGED 存活"有误）；
     *       不清 g_discoveryDone（发现结果永久有效）。 */
    /* 诊断窗口：Reset 后 1200 帧内统计 SetTexture 活动 */
    g_rstDiagUntil = g_frames + 1200;
    g_rstStTotal = 0; g_rstStFont = 0; g_rstSampleN = 0;
    for (int i = 0; i < 6; i++) g_rstSample[i] = NULL;
    g_rstUniqN = 0; g_rstLogN = 0;
    for (int i = 0; i < 40; i++) g_rstUniq[i] = NULL;

    b[0] = 0;
    lstrcatA(b, "\r\n[RESET] ===== 第 "); wsprintfA(t, "%ld", g_resetCount); lstrcatA(b, t);
    lstrcatA(b, " 次 Reset：已撤回补丁字段，准备调用真正的 Reset ===== 旧字体纹理=");
    wsprintfA(t, "%p", g_rstOldFontTex); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);

    /* ②【调用真正的 Reset】—— 绝不拦截 */
    g_ckpt = "R:2-realReset-call";
    long hr = g_orig_Reset ? g_orig_Reset(self, pp) : -1;
    g_ckpt = "R:3-realReset-ret";

    b[0] = 0;
    lstrcatA(b, "[RESET] 真正的 Reset 返回 hr=0x"); wsprintfA(t, "%lX", (unsigned long)hr); lstrcatA(b, t);
    /* ★ 精确判定 HRESULT（D3DERR_INVALIDCALL=0x8876086C / DEVICELOST=0x88760868 /
     *   DEVICENOTRESET=0x88760869，facility=0x876）。失败时给出确切原因。*/
    unsigned long uhr = (unsigned long)hr;
    const char* why = "  (成功)\r\n";
    if (uhr == 0x88760868UL)      why = "  (失败: D3DERR_DEVICELOST 设备丢失)\r\n";
    else if (uhr == 0x88760869UL) why = "  (失败: D3DERR_DEVICENOTRESET 尚未复位)\r\n";
    else if (uhr == 0x8876086CUL) why = "  (失败: D3DERR_INVALIDCALL 无效调用)\r\n";
    else if (uhr != 0)            why = "  (失败: 未知 HRESULT)\r\n";
    lstrcatA(b, why);
    HKLOG(b);

    /* ★★ Reset 失败时【绝不做任何 D3D9 调用】，只保留已撤回的原字段（安全态）。
     *   失败说明设备仍未复位，此时任何纹理查询/绑定都会让状态更糟。
     *   参数停留在原值 -> 玩家看到原版小字，但画面正常；等设备真正恢复。*/
    if (hr < 0) {
        g_postResetNeedRebuild = 0;
        g_rearmPending = 0;
        HKLOG("[RESET] Reset 失败 -> 保持安全态（原字段已撤回），不再触碰任何纹理\r\n");
        g_ckpt = "R:5-failed-safe";
        return hr;
    }

    /* ★ 状态探针：Reset 刚返回时的真实状态（stage0 绑定 + 字体对象字段）*/
    probe_state(self, "after-reset");

    /* ③【Reset 之后】安排重建 */
    if (hr >= 0) {
        g_postResetNeedRebuild = 1;
        /* ★ 修复2：安排「立即重打补丁」——由 CreateTexture/ Present 用【验证】
         *   的方式兑现（确认大纹理仍在 stage0 绑定后立刻改 +12/换表）。 */
        g_rearmPending = 1;
        g_rearmUntil   = g_frames + 1500;
        g_rearmDone    = 0;
        /* ★ c93：D2 窗口开启 —— 记录 Reset 帧、重置单次性、
         *   失效旧确认/重建时间戳（必须等 Reset 后的新信号） */
        g_d2ResetFrame   = g_frames;
        g_d2Tried        = 0;
        g_d2ConfirmFrame = -1;
        g_d2BigTexFrame  = -1;
        g_d2SBudget      = 0;   /* ★ c93e：Reset 后由 rearm 路径接管，启动武装作废 */
        g_d2SConfirm     = -1;
        /* MANAGED 大纹理指针本应存活；仅在从未建成时才补建。 */
        g_ckpt = "R:4-bigTex";
        if (!g_bigTex && g_devPtr) CreateBigTexture(g_devPtr);
        {
            char cb[180], ct[64]; cb[0] = 0;
            lstrcatA(cb, "[RESET] 大纹理重建后 g_bigTex="); wsprintfA(ct, "%p", g_bigTex); lstrcatA(cb, ct);
            lstrcatA(cb, " (NULL=重建失败)\r\n");
            HKLOG(cb);
        }
        HKLOG("[RESET] 已安排重建：CreateTexture 抓到新字体纹理后【验证大纹仍绑定】即立即重打补丁\r\n");
    }
    g_ckpt = "R:5-done";
    return hr;
}

static void attach_dev(void* dev, int idx, void* hook, int n) {
    if (!dev) return;
    void** vt = *(void***)dev;
    if (!vt) return;
    if (vt[idx] == hook) return;                    /* 已打过 */
    if (idx == 16) { g_orig_Reset = (PFN_Reset)vt[idx]; vt_register(16, hook, (void**)&g_orig_Reset); }
    if (idx == 17) { g_orig_Present = (PFN_Present)vt[idx]; g_hookPresent = hook; vt_register(17, hook, (void**)&g_orig_Present); }
    if (idx == 23) { g_orig_CreateTex = (PFN_CreateTex2)vt[idx]; g_hookCT = hook; vt_register(23, hook, (void**)&g_orig_CreateTex); }
    if (idx == 31) { g_orig_UpdTex = (PFN_UpdateTexture)vt[idx]; vt_register(31, hook, (void**)&g_orig_UpdTex); }
    if (idx == 65) { g_orig_SetTexture = (PFN_SetTexture)vt[idx]; g_ref_SetTexture = (PFN_SetTexture)vt[idx]; g_hookST = hook; vt_register(65, hook, (void**)&g_orig_SetTexture); }
    if (idx == 81) { g_orig_DP = (PFN_DP)vt[idx]; g_hookDP = hook; vt_register(81, hook, (void**)&g_orig_DP); }
    if (idx == 82) { g_orig_DIP = (PFN_DIP)vt[idx]; vt_register(82, hook, (void**)&g_orig_DIP); }
    if (idx == 83) { g_orig_DPUP = (PFN_DPUP)vt[idx]; vt_register(83, hook, (void**)&g_orig_DPUP); }
    if (idx == 84) { g_orig_DIPUP = (PFN_DIPUP)vt[idx]; vt_register(84, hook, (void**)&g_orig_DIPUP); }
    DWORD old = 0;
    if (!VirtualProtect(&vt[idx], sizeof(void*), PAGE_EXECUTE_READWRITE, &old)) {
        char b[128];
        lstrcatA(b, "  [vt?] VirtualProtect 失败\r\n"); HKLOG(b);
        return;
    }
    vt[idx] = hook;
    VirtualProtect(&vt[idx], sizeof(void*), old, &old);
    char b[256], t[64];
    b[0] = 0;
    lstrcatA(b, "  [vt"); wsprintfA(t, "%d", idx); lstrcatA(b, t);
    lstrcatA(b, "] 原地补丁: vtable="); wsprintfA(t, "%p", (void*)vt); lstrcatA(b, t);
    lstrcatA(b, " -> hook\r\n");
    HKLOG(b);
}

/* ============================================================
 * ★★★ vtable 自愈实现：把被换掉的槽位重新打上我们的钩子
 *   判定条件：槽位 ≠ 我们的 hook，且新值落在【某个已加载模块】内
 *   （= 原版函数或游戏/第三方包装，可安全链式调用），
 *   把它保存为新的"下游"后重新打上我们的钩子。
 *   若新值不在任何模块内（可疑/野指针）→ 不动，只记录。
 * ============================================================ */
static void vtable_heal(void* dev) {
    if (!dev || !g_vtSlotN) return;
    void** vt = *(void***)dev;
    if (!vt) return;
    int healed = 0;
    char logs[256];
    logs[0] = 0;
    for (int i = 0; i < g_vtSlotN; i++) {
        int idx = g_vtSlots[i].idx;
        if (!vt[idx]) continue;
        if (vt[idx] == g_vtSlots[i].hook) continue;          /* 还在 -> OK */
        const char* mod = mod_of((DWORD)(size_t)vt[idx]);
        if (!mod || mod[0] == '?') {                          /* 不在模块内 -> 不动 */
            continue;
        }
        /* 下游换了（游戏自己重装了包装）-> 更新保存值，保持链式调用 */
        if (vt[idx] != *g_vtSlots[i].pOrig) {
            char lb[200], lt[64];
            lb[0] = 0;
            lstrcatA(lb, "[HEAL] vt"); wsprintfA(lt, "%d", idx); lstrcatA(lb, lt);
            lstrcatA(lb, " 下游更新: "); wsprintfA(lt, "%p", vt[idx]); lstrcatA(lb, lt);
            lstrcatA(lb, " ("); lstrcatA(lb, mod); lstrcatA(lb, ")");
            lstrcatA(lb, "\r\n");
            HKLOG(lb);
            *g_vtSlots[i].pOrig = vt[idx];
            if (idx == 65) g_ref_SetTexture = (PFN_SetTexture)vt[idx];
        }
        DWORD old = 0;
        if (!VirtualProtect(&vt[idx], sizeof(void*), PAGE_EXECUTE_READWRITE, &old)) continue;
        vt[idx] = g_vtSlots[i].hook;
        VirtualProtect(&vt[idx], sizeof(void*), old, &old);
        healed++;
    }
    if (healed) {
        char b[220], t[64];
        b[0] = 0;
        lstrcatA(b, "[HEAL] ★ vtable 被恢复 "); wsprintfA(t, "%d", healed); lstrcatA(b, t);
        lstrcatA(b, " 处 -> 已重新打上钩子 f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
        lstrcatA(b, " 累计="); wsprintfA(t, "%ld", ++g_healN); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
}

/* Hook IDirect3D9::CreateDevice (vt[16]) */
static long WINAPI Hook_CreateDevice(void* self, unsigned int adapter, unsigned int type,
                                     void* focus, unsigned long flags, void* pp, void** outDev) {
    static int devLog = 0;
    g_ckpt = "CD:1-enter";
    long hr = g_orig_CreateDevice(self, adapter, type, focus, flags, pp, outDev);
    if (devLog < 3) {
        devLog++;
        char b[512], t[64];
        b[0] = 0;
        lstrcatA(b, "\r\n=== Hook_CreateDevice 命中 #"); wsprintfA(t, "%d", devLog); lstrcatA(b, t);
        lstrcatA(b, " ===\r\n  this="); wsprintfA(t, "%p", self); lstrcatA(b, t);
        lstrcatA(b, "\r\n  adapter="); wsprintfA(t, "%u", adapter); lstrcatA(b, t);
        lstrcatA(b, " type="); wsprintfA(t, "%u", type); lstrcatA(b, t);
        lstrcatA(b, " flags=0x"); wsprintfA(t, "%lX", flags); lstrcatA(b, t);
        lstrcatA(b, "\r\n  hr=0x"); wsprintfA(t, "%lX", (unsigned long)hr); lstrcatA(b, t);
        lstrcatA(b, "  *outDev="); wsprintfA(t, "%p", outDev ? *outDev : 0); lstrcatA(b, t);
        if (*outDev) {
            void** vt = *(void***)*outDev;
            lstrcatA(b, "\r\n  dev vtable="); wsprintfA(t, "%p", (void*)vt); lstrcatA(b, t);
            lstrcatA(b, "  ("); lstrcatA(b, mod_of((DWORD)vt)); lstrcatA(b, ")");
            lstrcatA(b, "\r\n  vt[17] Present ="); wsprintfA(t, "%p", vt[17]); lstrcatA(b, t);
            lstrcatA(b, "  ("); lstrcatA(b, mod_of((DWORD)vt[17])); lstrcatA(b, ")");
            lstrcatA(b, "\r\n  vt[65] SetTexture="); wsprintfA(t, "%p", vt[65]); lstrcatA(b, t);
            lstrcatA(b, "  ("); lstrcatA(b, mod_of((DWORD)vt[65])); lstrcatA(b, ")");
            lstrcatA(b, "\r\n");
            HKLOG(b);
            /* 第三步：Present 计数；第四步：SetTexture 记录 */
            g_orig_Present   = (PFN_Present)vt[17];
            g_orig_SetTexture = (PFN_SetTexture)vt[65];
            g_devPtr = *outDev;
            attach_dev(*outDev, 16, (void*)Hook_Reset, 119);       /* ★ Reset */
            attach_dev(*outDev, 17, (void*)Hook_Present, 119);
            attach_dev(*outDev, 23, (void*)Hook_CreateTexture, 119);/* ★ CreateTexture（强制 MANAGED） */
            attach_dev(*outDev, 31, (void*)Hook_UpdateTexture, 119);/* ★ c57 探针：纹理真值 dump */
            attach_dev(*outDev, 65, (void*)Hook_SetTexture, 119);
            attach_dev(*outDev, 81, (void*)Hook_DP, 119);          /* ★ DrawPrimitive（JIT 替换入口） */
            attach_dev(*outDev, 82, (void*)Hook_DIP, 119);         /* DrawIndexedPrimitive */
            attach_dev(*outDev, 83, (void*)Hook_DPUP, 119);        /* DrawPrimitiveUP */
            attach_dev(*outDev, 84, (void*)Hook_DIPUP, 119);       /* DrawIndexedPrimitiveUP */
            /* 3B: 建大纹理（字体对象要等它初始化完再改，见 Hook_SetTexture） */
#ifdef EXP_DEFER_CREATE
            g_expDeferCreate = 1;    /* ★ c72 实验：推迟到首帧 Present 创建 */
            HKLOG("[EXP-c72] 已推迟大纹理创建到首帧 Present\r\n");
#else
            CreateBigTexture(*outDev);
#endif
        } else {
            lstrcatA(b, "\r\n");
            HKLOG(b);
        }
    }
    return hr;
}

/* Hook Direct3DCreate9（IAT 层）—— 每个新对象都接到克隆 vtable 上 */
static void* WINAPI Hook_D3DC9(unsigned int sdk) {
    void* obj = g_orig_Create9 ? g_orig_Create9(sdk) : NULL;
    static int callCnt = 0;
    static int logCnt = 0;
    callCnt++;

    if (obj) attach_object(obj, 16, (void*)Hook_CreateDevice, 32);

    if (logCnt < 5) {
        logCnt++;
        char b[512], t[64];
        b[0] = 0;
        lstrcatA(b, "\r\n=== Hook_D3DC9 命中 #"); wsprintfA(t, "%d", callCnt); lstrcatA(b, t);
        lstrcatA(b, " ===\r\n  sdk="); wsprintfA(t, "%u", sdk); lstrcatA(b, t);
        lstrcatA(b, "\r\n  IDirect3D9*="); wsprintfA(t, "%p", obj); lstrcatA(b, t);
        if (obj) {
            void** vt = *(void***)obj;
            lstrcatA(b, "\r\n  当前vtable="); wsprintfA(t, "%p", (void*)vt); lstrcatA(b, t);
            lstrcatA(b, "  ("); lstrcatA(b, mod_of((DWORD)vt)); lstrcatA(b, ")");
            lstrcatA(b, "\r\n  vt[16]="); wsprintfA(t, "%p", vt[16]); lstrcatA(b, t);
            lstrcatA(b, "  ("); lstrcatA(b, mod_of((DWORD)vt[16])); lstrcatA(b, ")");
        }
        lstrcatA(b, "\r\n");
        HKLOG(b);
    }
    return obj;
}

/* 在 exe 的导入表里找 d3d9.dll!Direct3DCreate9 的 IAT 槽 */
static int find_iat_slot(void** *out_slot, void** *out_orig) {
    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) return 0;
    unsigned char* base = (unsigned char*)exe;
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)base;
    IMAGE_NT_HEADERS32* nt = (IMAGE_NT_HEADERS32*)(base + dos->e_lfanew);
    DWORD iva = nt->OptionalHeader.DataDirectory[1].VirtualAddress;
    if (!iva) return 0;
    IMAGE_IMPORT_DESCRIPTOR* desc = (IMAGE_IMPORT_DESCRIPTOR*)(base + iva);
    for (; desc->Name; desc++) {
        const char* mod = (const char*)(base + desc->Name);
        char lower[64]; int k = 0;
        for (; mod[k] && k < 63; k++) lower[k] = (mod[k] >= 'A' && mod[k] <= 'Z') ? mod[k] + 32 : mod[k];
        lower[k] = 0;
        if (lstrcmpiA(lower, "d3d9.dll") != 0) continue;
        IMAGE_THUNK_DATA* oft = desc->OriginalFirstThunk
            ? (IMAGE_THUNK_DATA*)(base + desc->OriginalFirstThunk) : NULL;
        IMAGE_THUNK_DATA* ft  = (IMAGE_THUNK_DATA*)(base + desc->FirstThunk);
        for (int i = 0; ft[i].u1.Function; i++) {
            const char* fname = NULL;
            if (oft && !(oft[i].u1.Ordinal & IMAGE_ORDINAL_FLAG32))
                fname = (const char*)(base + oft[i].u1.AddressOfData + 2);
            if (fname && lstrcmpiA(fname, "Direct3DCreate9") == 0) {
                *out_slot = (void**)&ft[i].u1.Function;
                *out_orig = (void**)ft[i].u1.Function;
                return 1;
            }
        }
    }
    return 0;
}

static DWORD WINAPI worker(LPVOID unused) {
    (void)unused;
    /* 等 IAT 解析完成（d3d9.dll 是静态导入，加载后槽位即非零） */
    void** slot = NULL; void* orig = NULL;
    for (int i = 0; i < 2000; i++) {          /* 最多 20 秒 */
        if (find_iat_slot(&slot, &orig) && *slot) break;
        Sleep(10);
    }
    char b[512], t[64];
    b[0] = 0;
    lstrcatA(b, "\r\n[WORKER] IAT 槽查找: ");
    if (!slot) { lstrcatA(b, "失败(未找到 d3d9.dll!Direct3DCreate9)\r\n"); VPLOG(b); return 1; }
    lstrcatA(b, "slot="); wsprintfA(t, "%p", (void*)slot); lstrcatA(b, t);
    lstrcatA(b, "  当前值="); wsprintfA(t, "%p", (void*)*slot); lstrcatA(b, t);
    lstrcatA(b, "  ("); lstrcatA(b, mod_of((DWORD)*slot)); lstrcatA(b, ")\r\n");
    VPLOG(b);

    /* 写 IAT：VirtualProtect -> 写 -> 恢复 */
    DWORD old = 0;
    if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) {
        VPLOG("[WORKER] VirtualProtect 失败\r\n"); return 1;
    }
    g_orig_Create9 = (PFN_D3DC9)*slot;        /* 保存原值 */
    *slot = (void*)Hook_D3DC9;
    VirtualProtect(slot, sizeof(void*), old, &old);
    b[0] = 0;
    lstrcatA(b, "[WORKER] IAT hook 安装: orig="); wsprintfA(t, "%p", (void*)g_orig_Create9); lstrcatA(b, t);
    lstrcatA(b, " -> hook="); wsprintfA(t, "%p", (void*)Hook_D3DC9); lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    VPLOG(b);
    return 0;
}

/* ============================================================
 *  异常处理（2026-10-07 晚加固：Reset 路径出问题时留下证据）
 *   说明：x86 MinGW 对 __try/__except 支持不可靠，这里用
 *   SetUnhandledExceptionFilter 记录异常码 + 崩溃地址，再把异常
 *   交回原处理器，不改变原有崩溃行为（不影响游戏自身 mini dump）。
 * ============================================================ */
static long WINAPI VP_UnhandledFilter(EXCEPTION_POINTERS* ep) {
    if (ep && ep->ExceptionRecord) {
        char b[400], t[64];
        b[0] = 0;
        lstrcatA(b, "\r\n[EXC] 未处理异常 code=0x");
        wsprintfA(t, "%lX", (unsigned long)ep->ExceptionRecord->ExceptionCode); lstrcatA(b, t);
        lstrcatA(b, " 地址="); wsprintfA(t, "%p", ep->ExceptionRecord->ExceptionAddress); lstrcatA(b, t);
        lstrcatA(b, "  (");
        lstrcatA(b, mod_of((DWORD)ep->ExceptionRecord->ExceptionAddress));
        lstrcatA(b, ")  最后检查点=");
        lstrcatA(b, g_ckpt ? g_ckpt : "?");
        lstrcatA(b, "  f="); wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
        lstrcatA(b, "  resetCnt="); wsprintfA(t, "%ld", g_resetCount); lstrcatA(b, t);
        lstrcatA(b, "\r\n");
        HKLOG(b);
        VPLOG(b);
    }
    return EXCEPTION_CONTINUE_SEARCH;   /* 交回原处理器 */
}

/* ============================================================
 *  DllMain
 * ============================================================ */
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    init_runtime_paths();
    install_filelog();
    (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        char b[256], t[32];
        DisableThreadLibraryCalls(h);
        b[0] = 0;
        lstrcatA(b, "\r\n#### VERSION PROXY LOADED pid=");
        wsprintfA(t, "%lu", (unsigned long)GetCurrentProcessId());
        lstrcatA(b, t); lstrcatA(b, "\r\n");
        VPLOG(b);

        resolve_all();   /* ★ 必须立刻解析，不能等第一次调用 */

        /* ★ c93e：读取运行时换回模式（swap_mode.txt：内容以'B'开头 -> B 模式） */
        {
            HANDLE hf = CreateFileA("swap_mode.txt", GENERIC_READ, FILE_SHARE_READ,
                                    NULL, OPEN_EXISTING, 0, NULL);
            if (hf != INVALID_HANDLE_VALUE) {
                char mb[8]; DWORD rd = 0; int i;
                for (i = 0; i < 8; i++) mb[i] = 0;
                ReadFile(hf, mb, 4, &rd, NULL);
                CloseHandle(hf);
                if (mb[0] == 'B') g_swapModeB = 1;
            }
            HKLOG(g_swapModeB ? "[MODE] 换回模式=B（只换非 g_fontTex 的集合命中项）\r\n"
                              : "[MODE] 换回模式=A（c92 单指针：只换 g_fontTex）\r\n");
        }

        /* ★ 安装异常记录器（不改变游戏自身崩溃行为，仅留证据） */
        SetUnhandledExceptionFilter(VP_UnhandledFilter);

        void** slots[] = {
            &p_GetFileVersionInfoA, &p_GetFileVersionInfoByHandle, &p_GetFileVersionInfoExA,
            &p_GetFileVersionInfoExW, &p_GetFileVersionInfoSizeA, &p_GetFileVersionInfoSizeExA,
            &p_GetFileVersionInfoSizeExW, &p_GetFileVersionInfoSizeW, &p_GetFileVersionInfoW,
            &p_VerFindFileA, &p_VerFindFileW, &p_VerInstallFileA, &p_VerInstallFileW,
            &p_VerLanguageNameA, &p_VerLanguageNameW, &p_VerQueryValueA, &p_VerQueryValueW
        };
        int miss = 0;
        for (int i = 0; i < 17; i++) if (!*slots[i]) miss++;
        b[0] = 0;
        lstrcatA(b, "[VP] resolve done, real="); wsprintfA(t, "%p", (void*)g_realVer); lstrcatA(b, t);
        lstrcatA(b, "  未解析="); wsprintfA(t, "%d", miss); lstrcatA(b, t); lstrcatA(b, "\r\n");
        VPLOG(b);

        /* 复杂操作放工作线程，不放 DllMain */
        HANDLE th = CreateThread(NULL, 0, worker, NULL, 0, NULL);
        if (th) CloseHandle(th);
    }
    return TRUE;
}
