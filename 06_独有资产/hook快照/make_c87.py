# -*- coding: utf-8 -*-
"""c87: 把硬编码路径改成"运行时推导 + 多候选搜索"，实现跨机器可移植。
改动点（全部是路径来源，不动任何逻辑）：
  1. 新增 g_base[]  = DLL 自身所在目录（= 游戏目录，天然跨机器）
  2. 新增 resolve_asset_path()：按候选顺序找图集/字形表
       候选1  <游戏目录>\\<name>              ← 交付布局（推荐）
       候选2  <游戏目录>\\hzp_tools\\out\\<name>  ← 现有开发布局（兼容）
       候选3  <游戏目录>\\Obscure_assets\\<name>  ← 备用
  3. RAWPATH/TABLEPATH 从宏改成运行时字符串（g_rawPath/g_tblPath）
  4. 日志路径同样推导：优先 <游戏目录>\\hzp_tools\\out\\，否则 <游戏目录>\\
"""
import io, re, sys

P = r'E:\SteamLibrary\steamapps\common\Obscure\hzp_tools\hook\version_proxy.c'
s = io.open(P, encoding='utf-8', errors='surrogateescape').read()
orig = s

# ---------- 1) 路径常量改成占位（真实值运行时填） ----------
s = s.replace(
    '#define LOGF   "E://SteamLibrary//steamapps//common//Obscure//hzp_tools//out//versionproxy.log"',
    '#define LOGF   "__RUNTIME__"   /* 运行时填 */')
s = s.replace(
    '#define HOOKLOG "E://SteamLibrary//steamapps//common//Obscure//hzp_tools//out//x_hook.log"',
    '#define HOOKLOG "__RUNTIME__"  /* 运行时填 */')
s = re.sub(r'#define RAWPATH\s+"[^"]*"',
           '#define RAWPATH   "__RUNTIME__"', s)
s = re.sub(r'#define TABLEPATH\s+"[^"]*"',
           '#define TABLEPATH "__RUNTIME__"', s)
s = re.sub(r'#define TDDIR "[^"]*"',
           '#define TDDIR "__RUNTIME__"', s)

# ---------- 2) 插入运行时路径推导引擎（放在 LOGF_ 之前） ----------
RUNTIME = r'''
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
    char cand[340];
    const char* dirs[3];
    int nd = 0;
    dirs[nd++] = g_base;                                            /* 交付布局 */
    dirs[nd++] = "hzp_tools\\out\\";                                 /* 开发布局 */
    dirs[nd++] = "Obscure_assets\\";                                 /* 备用 */
    for (int i = 0; i < nd; i++) {
        lstrcpynA(cand, dirs[i], sizeof(cand));
        lstrcatA(cand, name);
        char full[340];
        lstrcpynA(full, g_base, sizeof(full));
        lstrcatA(full, cand);
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
            char* p = lstrrchrA(g_base, '\\');
            if (p) p[1] = 0;                     /* 去掉文件名，留目录+反斜杠 */
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
    /* 落盘一条，方便确认推导结果 */
    char msg[700];
    lstrcpynA(msg, g_logDir, sizeof(msg));
    lstrcatA(msg, "c87_paths.txt");
    FILE* f = NULL;
    if (fopen_s(&f, msg, "w") == 0 && f) {
        fprintf(f, "base      = %s\n", g_base);
        fprintf(f, "raw       = %s  (%s)\n", g_rawPath, file_exists_a(g_rawPath) ? "OK" : "MISSING");
        fprintf(f, "table     = %s  (%s)\n", g_tblPath, file_exists_a(g_tblPath) ? "OK" : "MISSING");
        fprintf(f, "logdir    = %s\n", g_logDir);
        fclose(f);
    }
}

'''
anchor = 'static void LOGF_(const char* file, const char* s) {'
assert anchor in s, 'anchor not found'
s = s.replace(anchor, RUNTIME + anchor, 1)

# ---------- 3) 在 DllMain 里最先调用 init_runtime_paths() ----------
# 找 DllMain 里的 PROCESS_ATTACH 分支
m = re.search(r'(BOOL\s+WINAPI\s+DllMain\s*\([^)]*\)\s*\{)', s)
assert m, 'DllMain not found'
# 在 DllMain 函数体开头插入
s = s[:m.end()] + '\n    init_runtime_paths();' + s[m.end():]

# ---------- 4) RAWPATH/TABLEPATH 改为运行时的 g_rawPath/g_tblPath ----------
s = s.replace('CreateFileA(RAWPATH,', 'CreateFileA(g_rawPath,')
s = s.replace('CreateFileA(TABLEPATH,', 'CreateFileA(g_tblPath,')

# ---------- 5) 日志路径：LOGF_/HKLOG 改用推导出的 g_logDir ----------
s = s.replace('static void LOGF_(const char* file, const char* s) {',
              'static char* mk_logpath(const char* name) {\n'
              '    static char buf[400];\n'
              '    lstrcpynA(buf, g_logDir, sizeof(buf));\n'
              '    lstrcatA(buf, name);\n'
              '    return buf;\n'
              '}\n\n'
              'static void LOGF_(const char* file, const char* s) {', 1)
# 把 HKLOG / 日志调用里的宏换成 mk_logpath
s = s.replace('static void HKLOG(const char* s) { LOGF_(HOOKLOG, s); }',
              'static void HKLOG(const char* s) { LOGF_(mk_logpath("x_hook.log"), s); }')
s = s.replace('LOGF_(LOGF,', 'LOGF_(mk_logpath("versionproxy.log"),')

io.open(P, 'w', encoding='utf-8', errors='surrogateescape').write(s)

changed = sum(1 for a, b in zip(orig.split('\n'), s.split('\n')) if a != b)
print('已改写 %s' % P)
print('变更行数 ~%d' % changed)
print('剩余硬编码 D:/E: 引用:', len(re.findall(r'[DE]://SteamLibrary', s)))