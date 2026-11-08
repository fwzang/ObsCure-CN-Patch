// setup_o1.c — ObsCure 简体中文汉化补丁 V1.02 安装器（原生 Win32）
// 与二代（Obscure II）同一套 UI 骨架：深色标题条 + 白色说明卡片 + 分块拷贝 pump。
// 目标：安装全程不假死 —— 大文件拷贝每 1 MB 刷新一次进度，外部工具
// （xdelta / obscure-hvp）用 CreateProcess + 轮询，不再同步阻塞 UI 线程。
//
// 载荷：[setup.exe][bin/*][rb/*][tool/*][ode/*][Footer2]
//   bin/    直接释放到游戏根（version.dll、dinput8.dll、scripts/…）
//   rb/     仅用于重建 datapack.hvp（zh_en.lng + maps_pc/33 件贴图）
//   tool/   obscure-hvp.exe / xdelta.exe
//   ode/    L1_*.patch + data/（加强版才有）
#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#pragma comment(lib, "comctl32.lib")

#define MAXENT 96

#pragma pack(push, 1)
typedef struct {
    char               name[64];
    unsigned long long off;
    unsigned long long size;
    unsigned int       crc;
} Ent2;
typedef struct {
    char           magic[8];      /* "O1SETUP2" */
    unsigned int   ver;           /* 2 */
    unsigned int   n;
    Ent2           ent[MAXENT];
} Footer2;
#pragma pack(pop)

#define ID_EDIT_INFO   1001
#define ID_EDIT_PATH   1002
#define ID_BTN_BROWSE  1003
#define ID_CHK_BACKUP  1004
#define ID_CHK_WIDE    1005
#define ID_BTN_INSTALL 1006
#define ID_BTN_RESTORE 1007
#define ID_BTN_EXIT    1008
#define ID_PROGRESS    1009
#define ID_STATUS      1010
#define ID_CHK_ODE     1011

#define IDD_OK         2001
#define IDD_QUIT       2002

static HINSTANCE g_hInst;
static HWND g_hMain, g_hInfo, g_hPath, g_hChkBackup, g_hChkWide, g_hChkOde, g_hProg, g_hStatus;
static HFONT g_fTitle, g_fSub, g_fBody;
static HBRUSH g_bg, g_band, g_white;
static HPEN   g_line;
static Footer2 g_ft;
static int g_haveFt = 0, g_s = 100, g_hasOde = 0;
static unsigned int g_crcTab[256];

#define S(v) MulDiv((v), g_s, 100)

#include "info_o1.h"

/* 标题随包内容变化：含 ODE 层即为「加强汉化版」 */
static const wchar_t* kTitle = L"ObsCure 简体中文汉化补丁";
static const wchar_t* kSub   = L"V1.02  ·  2026-10  ·  适用于 Steam 版  ·  正版差分包";
static const wchar_t* kWinTitle = L"ObsCure 汉化补丁 V1.02";

/* ---------- 基础工具 ---------- */
static void set_status(const wchar_t* s) { if (g_hStatus) SetWindowTextW(g_hStatus, s); }
static void set_prog(int pct) { if (g_hProg) SendMessageW(g_hProg, PBM_SETPOS, (WPARAM)pct, 0); }
static void pump(void) { MSG m; while (PeekMessageW(&m, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&m); DispatchMessageW(&m); } }

static int file_exists(const wchar_t* p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}
static int dir_exists(const wchar_t* p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}
static void join(wchar_t* out, const wchar_t* dir, const wchar_t* name) {
    lstrcpyW(out, dir);
    size_t n = lstrlenW(out);
    if (n && out[n - 1] != L'\\') lstrcatW(out, L"\\");
    lstrcatW(out, name);
}
static void mkdirs(const wchar_t* path) {
    wchar_t tmp[MAX_PATH];
    lstrcpynW(tmp, path, MAX_PATH);
    for (int i = 3; tmp[i]; i++)
        if (tmp[i] == L'\\') { tmp[i] = 0; CreateDirectoryW(tmp, NULL); tmp[i] = L'\\'; }
    CreateDirectoryW(tmp, NULL);
}
static unsigned long long fsize(const wchar_t* p) {
    WIN32_FILE_ATTRIBUTE_DATA d;
    if (!GetFileAttributesExW(p, GetFileExInfoStandard, &d)) return 0;
    return ((unsigned long long)d.nFileSizeHigh << 32) | d.nFileSizeLow;
}
static void del_tree(const wchar_t* dir) {
    wchar_t pat[MAX_PATH];
    join(pat, dir, L"*");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (!lstrcmpW(fd.cFileName, L".") || !lstrcmpW(fd.cFileName, L"..")) continue;
        wchar_t sub[MAX_PATH]; join(sub, dir, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) del_tree(sub);
        else DeleteFileW(sub);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    RemoveDirectoryW(dir);
}
static void wlog(const wchar_t* fmt, ...) {
    wchar_t path[MAX_PATH], buf[1024];
    DWORD n = GetTempPathW(MAX_PATH, path);
    if (!n) return;
    lstrcatW(path, L"O1Setup.log");
    va_list ap; va_start(ap, fmt);
    wvsprintfW(buf, fmt, ap);
    va_end(ap);
    HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    SetFilePointer(h, 0, NULL, FILE_END);
    char u8[2048] = {0};
    int len = WideCharToMultiByte(CP_UTF8, 0, buf, -1, u8, sizeof(u8) - 1, NULL, NULL);
    DWORD put = 0;
    if (len > 1) WriteFile(h, u8, (DWORD)len - 1, &put, NULL);
    WriteFile(h, "\r\n", 2, &put, NULL);
    CloseHandle(h);
}

/* ---------- CRC32 ---------- */
static void crc_init(void) {
    for (unsigned int i = 0; i < 256; i++) {
        unsigned int c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        g_crcTab[i] = c;
    }
}
static unsigned int crc_upd(unsigned int s, const BYTE* p, size_t n) {
    while (n--) s = g_crcTab[(s ^ *p++) & 0xFF] ^ (s >> 8);
    return s;
}

/* ---------- MD5（基底识别用，需与旧版安装器常量对齐） ---------- */
typedef struct { unsigned int s[4]; unsigned long long n; unsigned char b[64]; } MD5_CTX;
static const unsigned int K[64] = {
    0xd76aa478,0xe8c7b756,0x242070db,0xc1bdceee,0xf57c0faf,0x4787c62a,0xa8304613,0xfd469501,
    0x698098d8,0x8b44f7af,0xffff5bb1,0x895cd7be,0x6b901122,0xfd987193,0xa679438e,0x49b40821,
    0xf61e2562,0xc040b340,0x265e5a51,0xe9b6c7aa,0xd62f105d,0x02441453,0xd8a1e681,0xe7d3fbc8,
    0x21e1cde6,0xc33707d6,0xf4d50d87,0x455a14ed,0xa9e3e905,0xfcefa3f8,0x676f02d9,0x8d2a4c8a,
    0xfffa3942,0x8771f681,0x6d9d6122,0xfde5380c,0xa4beea44,0x4bdecfa9,0xf6bb4b60,0xbebfbc70,
    0x289b7ec6,0xeaa127fa,0xd4ef3085,0x04881d05,0xd9d4d039,0xe6db99e5,0x1fa27cf8,0xc4ac5665,
    0xf4292244,0x432aff97,0xab9423a7,0xfc93a039,0x655b59c3,0x8f0ccc92,0xffeff47d,0x85845dd1,
    0x6fa87e4f,0xfe2ce6e0,0xa3014314,0x4e0811a1,0xf7537e82,0xbd3af235,0x2ad7d2bb,0xeb86d391
};
static const unsigned char R[64] = {
    7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,
    5,9,14,20,5,9,14,20,5,9,14,20,5,9,14,20,
    4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,
    6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21
};
#define ROTL(x,c) (((x) << (c)) | ((x) >> (32 - (c))))
static void md5_block(MD5_CTX* c, const unsigned char* p) {
    unsigned int a = c->s[0], b = c->s[1], cc = c->s[2], d = c->s[3], x[16];
    for (int i = 0; i < 16; i++)
        x[i] = (unsigned int)p[i * 4] | ((unsigned int)p[i * 4 + 1] << 8) |
               ((unsigned int)p[i * 4 + 2] << 16) | ((unsigned int)p[i * 4 + 3] << 24);
    for (int i = 0; i < 64; i++) {
        unsigned int f, g;
        if (i < 16)      { f = (b & cc) | (~b & d);        g = i; }
        else if (i < 32) { f = (d & b) | (~d & cc);        g = (5 * i + 1) & 15; }
        else if (i < 48) { f = b ^ cc ^ d;                 g = (3 * i + 5) & 15; }
        else             { f = cc ^ (b | ~d);              g = (7 * i) & 15; }
        unsigned int t = d;
        d = cc; cc = b;
        b = b + ROTL(a + f + K[i] + x[g], R[i]);
        a = t;
    }
    c->s[0] += a; c->s[1] += b; c->s[2] += cc; c->s[3] += d;
}
static void md5_init(MD5_CTX* c) {
    c->s[0] = 0x67452301; c->s[1] = 0xefcdab89; c->s[2] = 0x98badcfe; c->s[3] = 0x10325476;
    c->n = 0;
}
static void md5_upd(MD5_CTX* c, const void* data, size_t len) {
    const unsigned char* p = (const unsigned char*)data;
    size_t idx = (size_t)(c->n & 63);
    c->n += len;
    if (idx) {
        size_t fill = 64 - idx;
        if (len < fill) { memcpy(c->b + idx, p, len); return; }
        memcpy(c->b + idx, p, fill);
        md5_block(c, c->b);
        p += fill; len -= fill;
    }
    while (len >= 64) { md5_block(c, p); p += 64; len -= 64; }
    if (len) memcpy(c->b, p, len);
}
static void md5_final(MD5_CTX* c, unsigned char out[16]) {
    unsigned long long bits = c->n << 3;
    size_t idx = (size_t)(c->n & 63);
    unsigned char pad[72]; size_t padlen = (idx < 56) ? (56 - idx) : (120 - idx);
    memset(pad, 0, sizeof(pad)); pad[0] = 0x80;
    md5_upd(c, pad, padlen);
    unsigned char lenb[8];
    for (int i = 0; i < 8; i++) lenb[i] = (unsigned char)(bits >> (8 * i));
    md5_upd(c, lenb, 8);
    for (int i = 0; i < 4; i++) {
        out[i * 4] = (unsigned char)(c->s[i]);
        out[i * 4 + 1] = (unsigned char)(c->s[i] >> 8);
        out[i * 4 + 2] = (unsigned char)(c->s[i] >> 16);
        out[i * 4 + 3] = (unsigned char)(c->s[i] >> 24);
    }
}
/* 取文件 md5 前 4 字节（与旧版 8 位十六进制前缀等价），顺带报进度 */
static int md5_prefix(const wchar_t* path, unsigned char out[4], int pFrom, int pTo) {
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    LARGE_INTEGER z; GetFileSizeEx(h, &z);
    static BYTE buf[1 << 20];
    MD5_CTX c; md5_init(&c);
    unsigned long long done = 0, total = (unsigned long long)z.QuadPart;
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(h, buf, sizeof(buf), &got, NULL) || !got) break;
        md5_upd(&c, buf, got);
        done += got;
        if (total) set_prog(pFrom + (int)((pTo - pFrom) * (double)done / (double)total));
        pump();
    }
    CloseHandle(h);
    unsigned char dig[16]; md5_final(&c, dig);
    memcpy(out, dig, 4);
    return 1;
}
/* v1.02：装完自校验 4 个组件的完整 md5（不再需要外部脚本核对） */
static int md5_file_full(const wchar_t* path, unsigned char out[16]) {
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    static BYTE buf[1 << 20];
    MD5_CTX c; md5_init(&c);
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(h, buf, sizeof(buf), &got, NULL) || !got) break;
        md5_upd(&c, buf, got);
    }
    CloseHandle(h);
    md5_final(&c, out);
    return 1;
}
static void md5_hex16(const unsigned char d[16], wchar_t out[33]) {
    static const wchar_t HX[] = L"0123456789abcdef";
    for (int i = 0; i < 16; i++) { out[i * 2] = HX[d[i] >> 4]; out[i * 2 + 1] = HX[d[i] & 15]; }
    out[32] = 0;
}
static int check_components(const wchar_t* dir, wchar_t* report, int cap) {
    static const wchar_t* NAMES[4] = { L"version.dll", L"dinput8.dll", L"big_atlas_c.raw", L"table_c.bin" };
    static const char* WANT[4] = {
        "0c17dfc07b666255b04652dbf0cbe541",
        "b8c51891352e3e7bfc2c30f2c903b46c",
        "99a1244494b42d9cb9572e92335720ff",
        "6a498e9d8478b48ff9b3dc0e29f5c183"
    };
    int bad = 0;
    report[0] = 0;
    for (int i = 0; i < 4; i++) {
        wchar_t p[MAX_PATH]; join(p, dir, NAMES[i]);
        unsigned char d[16]; wchar_t hex[33];
        if (!md5_file_full(p, d)) { bad++; wlog(L"[chk] %s MISSING", NAMES[i]); continue; }
        md5_hex16(d, hex);
        wchar_t wq[33];
        for (int k = 0; k < 32; k++) wq[k] = (wchar_t)WANT[i][k];
        wq[32] = 0;
        int ok = (wcscmp(hex, wq) == 0);
        if (!ok) bad++;
        wlog(L"[chk] %s = %s %s", NAMES[i], hex, ok ? L"OK" : L"MISMATCH");
        if ((int)wcslen(report) + 40 < cap) {
            wcscat(report, L"\r\n· ");
            wcscat(report, NAMES[i]);
            wcscat(report, ok ? L" 一致" : L" 不符");
        }
    }
    return bad;
}
static int eq4(const unsigned char* a, const char* hex) {
    unsigned char t[4];
    for (int i = 0; i < 4; i++) {
        unsigned int v = 0;
        for (int k = 0; k < 2; k++) {
            char ch = hex[i * 2 + k];
            v <<= 4;
            v |= (ch >= '0' && ch <= '9') ? (unsigned int)(ch - '0') : (unsigned int)((ch | 0x20) - 'a' + 10);
        }
        t[i] = (unsigned char)v;
    }
    return memcmp(a, t, 4) == 0;
}

/* ---------- 载荷 ---------- */
/* 载荷内名（UTF-8, 正斜杠）→ 相对路径（反斜杠） */
static void arc_to_win(const char* name, wchar_t* out, int cap) {
    MultiByteToWideChar(CP_UTF8, 0, name, -1, out, cap);
    for (int i = 0; out[i]; i++) if (out[i] == L'/') out[i] = L'\\';
}
static int load_footer(void) {
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(NULL, self, MAX_PATH);
    HANDLE h = CreateFileW(self, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    LARGE_INTEGER li; GetFileSizeEx(h, &li);
    LARGE_INTEGER pos; pos.QuadPart = li.QuadPart - (LONGLONG)sizeof(Footer2);
    if (pos.QuadPart > 0) {
        DWORD got = 0;
        SetFilePointerEx(h, pos, NULL, FILE_BEGIN);
        if (ReadFile(h, &g_ft, sizeof(g_ft), &got, NULL) && got == sizeof(g_ft)) {
            if (memcmp(g_ft.magic, "O1SETUP2", 8) == 0 && g_ft.ver == 2 &&
                g_ft.n > 0 && g_ft.n <= MAXENT) g_haveFt = 1;
        }
    }
    CloseHandle(h);
    return g_haveFt;
}
static Ent2* find_ent(const char* name) {
    if (!g_haveFt) return NULL;
    for (unsigned i = 0; i < g_ft.n; i++)
        if (strcmp(g_ft.ent[i].name, name) == 0) return &g_ft.ent[i];
    return NULL;
}
static int has_prefix(const char* p) {
    /* 无有效 footer 时 g_ft.n 是读进来的垃圾值，必须先判 g_haveFt */
    if (!g_haveFt) return 0;
    for (unsigned i = 0; i < g_ft.n; i++)
        if (strncmp(g_ft.ent[i].name, p, strlen(p)) == 0) return 1;
    return 0;
}

/* 打开安装包自身 */
static HANDLE open_self(void) {
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(NULL, self, MAX_PATH);
    return CreateFileW(self, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
}

/* 释放一段载荷到 dst，带进度；返回 1 成功 */
static int extract_ent(HANDLE hSrc, Ent2* e, const wchar_t* dst, int pFrom, int pTo) {
    wchar_t d[MAX_PATH];
    lstrcpynW(d, dst, MAX_PATH);
    wchar_t* slash = NULL;
    for (wchar_t* q = d; *q; q++) if (*q == L'\\') slash = q;
    if (slash) { *slash = 0; mkdirs(d); *slash = L'\\'; }

    HANDLE hDst = CreateFileW(dst, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hDst == INVALID_HANDLE_VALUE) return 0;
    LARGE_INTEGER p; p.QuadPart = (LONGLONG)e->off;
    SetFilePointerEx(hSrc, p, NULL, FILE_BEGIN);
    static BYTE buf[1 << 20];
    unsigned long long left = e->size, done = 0;
    while (left) {
        DWORD want = (DWORD)(left > sizeof(buf) ? sizeof(buf) : left), got = 0, put = 0;
        if (!ReadFile(hSrc, buf, want, &got, NULL) || !got) break;
        if (!WriteFile(hDst, buf, got, &put, NULL) || put != got) break;
        left -= got; done += got;
        if (e->size) set_prog(pFrom + (int)((double)(pTo - pFrom) * (double)done / (double)e->size));
        pump();
    }
    CloseHandle(hDst);
    return left == 0;
}

/* 拷贝文件（分块 + pump），返回 1 成功 */
static int copy_file(const wchar_t* src, const wchar_t* dst, int pFrom, int pTo) {
    HANDLE hs = CreateFileW(src, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (hs == INVALID_HANDLE_VALUE) return 0;
    wchar_t d[MAX_PATH]; lstrcpynW(d, dst, MAX_PATH);
    wchar_t* slash = NULL;
    for (wchar_t* q = d; *q; q++) if (*q == L'\\') slash = q;
    if (slash) { *slash = 0; mkdirs(d); *slash = L'\\'; }

    HANDLE hd = CreateFileW(dst, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hd == INVALID_HANDLE_VALUE) { CloseHandle(hs); return 0; }
    LARGE_INTEGER z; GetFileSizeEx(hs, &z);
    unsigned long long total = (unsigned long long)z.QuadPart, done = 0;
    static BYTE buf[1 << 20];
    int ok = 1;
    for (;;) {
        DWORD got = 0, put = 0;
        if (!ReadFile(hs, buf, sizeof(buf), &got, NULL) || !got) break;
        if (!WriteFile(hd, buf, got, &put, NULL) || put != got) { ok = 0; break; }
        done += got;
        if (total) set_prog(pFrom + (int)((double)(pTo - pFrom) * (double)done / (double)total));
        pump();
    }
    CloseHandle(hd); CloseHandle(hs);
    return ok;
}

/* ---------- 外部工具（CreateProcess + 轮询，UI 不冻结） ---------- */
static int run_tool(const wchar_t* exe, const wchar_t* params, const wchar_t* workdir,
                    int pFrom, int pTo, const wchar_t* what) {
    wchar_t cmd[2048];
    wsprintfW(cmd, L"\"%s\" %s", exe, params);
    STARTUPINFOW si; ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    PROCESS_INFORMATION pi; ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, workdir, &si, &pi)) {
        wlog(L"[tool] CreateProcess failed %s err=%lu", exe, GetLastError());
        return -1;
    }
    int pct = pFrom;
    DWORD t0 = GetTickCount();
    for (;;) {
        DWORD w = WaitForSingleObject(pi.hProcess, 120);
        pump();
        if (w == WAIT_OBJECT_0) break;
        if (pct < pTo - 1) { pct++; set_prog(pct); }
        if (what) {
            wchar_t msg[256];
            wsprintfW(msg, L"%s…（已用 %lu 秒，请勿关闭）", what, (GetTickCount() - t0) / 1000);
            set_status(msg);
        }
    }
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread);
    wlog(L"[tool] %s exit=%lu", exe, code);
    return (int)code;
}

/* ---------- 路径探测 ---------- */
static int is_game_dir(const wchar_t* d) {
    wchar_t t[MAX_PATH]; join(t, d, L"Obscure.exe");
    return file_exists(t);
}
static void vdf_paths(const wchar_t* steam, wchar_t libs[][MAX_PATH], int* n) {
    wchar_t vdf[MAX_PATH]; join(vdf, steam, L"steamapps\\libraryfolders.vdf");
    HANDLE h = CreateFileW(vdf, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    char buf[65536];
    DWORD got = 0;
    ReadFile(h, buf, sizeof(buf) - 1, &got, NULL);
    CloseHandle(h);
    buf[got] = 0;
    for (char* p = buf; (p = strstr(p, "\"path\"")) != NULL; p += 6) {
        char* q = strchr(p, '"');
        if (!q) break;
        q++;
        char* e = strchr(q, '"');
        if (!e) break;
        char tmp[512];
        size_t len = (size_t)(e - q);
        if (len >= sizeof(tmp)) len = sizeof(tmp) - 1;
        memcpy(tmp, q, len); tmp[len] = 0;
        if (len < 4 || !strchr(tmp, ':')) continue;
        wchar_t wtmp[MAX_PATH];
        MultiByteToWideChar(CP_ACP, 0, tmp, -1, wtmp, MAX_PATH);
        for (int i = 0; wtmp[i]; i++) if (wtmp[i] == L'/') wtmp[i] = L'\\';
        if (*n < 8) { lstrcpynW(libs[*n], wtmp, MAX_PATH); (*n)++; }
    }
}
static int guess_game_dir(wchar_t* out, DWORD cap) {
    wchar_t steam[MAX_PATH] = L"";
    HKEY k;
    DWORD sz = sizeof(steam), type = 0;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", 0, KEY_READ, &k) == ERROR_SUCCESS) {
        if (RegQueryValueExW(k, L"SteamPath", NULL, &type, (BYTE*)steam, &sz) == ERROR_SUCCESS) {
            for (int i = 0; steam[i]; i++) if (steam[i] == L'/') steam[i] = L'\\';
        }
        RegCloseKey(k);
    }
    wchar_t cand[MAX_PATH];
    if (lstrlenW(steam)) {
        wchar_t libs[8][MAX_PATH]; int n = 0;
        vdf_paths(steam, libs, &n);
        for (int i = 0; i < n; i++) {
            wsprintfW(cand, L"%s\\steamapps\\common\\Obscure", libs[i]);
            if (is_game_dir(cand)) { lstrcpynW(out, cand, cap); return 1; }
        }
        wsprintfW(cand, L"%s\\steamapps\\common\\Obscure", steam);
        if (is_game_dir(cand)) { lstrcpynW(out, cand, cap); return 1; }
    }
    const wchar_t* tries[] = {
        L"D:\\SteamLibrary\\steamapps\\common\\Obscure",
        L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\Obscure",
        L"C:\\SteamLibrary\\steamapps\\common\\Obscure",
        L"E:\\SteamLibrary\\steamapps\\common\\Obscure"
    };
    for (int i = 0; i < 4; i++) if (is_game_dir(tries[i])) { lstrcpynW(out, tries[i], cap); return 1; }
    lstrcpynW(out, L"D:\\SteamLibrary\\steamapps\\common\\Obscure", cap);
    return 0;
}

/* ---------- 备份 / 回滚 ---------- */
#define BACKUP_DIR L"_obscure_backup"
#define ADDED_LIST L"_added_files.txt"

/* 上面的写法过于取巧，改用简单实现：整表重写 */
static void backup_add2(const wchar_t* bdir, const wchar_t* rel) {
    wchar_t p[MAX_PATH];
    join(p, bdir, ADDED_LIST);
    static wchar_t lines[512][MAX_PATH];
    int n = 0;
    HANDLE h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        static wchar_t all[65536];
        DWORD got = 0;
        ReadFile(h, all, sizeof(all) - 2, &got, NULL);
        CloseHandle(h);
        all[got / 2] = 0;
        wchar_t* q = all;
        if (*q == 0xFEFF) q++;
        while (*q && n < 512) {
            wchar_t* e = wcschr(q, L'\n');
            if (e) *e = 0;
            size_t L = wcslen(q);
            if (L && q[L - 1] == L'\r') q[L - 1] = 0;
            if (*q) lstrcpynW(lines[n++], q, MAX_PATH);
            if (!e) break;
            q = e + 1;
        }
    }
    for (int i = 0; i < n; i++) if (!lstrcmpW(lines[i], rel)) return;
    if (n < 512) lstrcpynW(lines[n++], rel, MAX_PATH);

    h = CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD put = 0;
    WriteFile(h, L"\xFEFF", 2, &put, NULL);
    for (int i = 0; i < n; i++) {
        WriteFile(h, lines[i], (DWORD)(wcslen(lines[i]) * 2), &put, NULL);
        WriteFile(h, L"\r\n", 4, &put, NULL);
    }
    CloseHandle(h);
}

/* ---------- 基底识别 ---------- */
enum { BASE_UNKNOWN = 0, BASE_VANILLA, BASE_ODE_EN, BASE_ZH };

static int detect_base(const wchar_t* dir, int* isOdePacks, int silent) {
    wchar_t dp[MAX_PATH], cp[MAX_PATH], kp[MAX_PATH];
    join(dp, dir, L"datapack.hvp");
    join(cp, dir, L"cachpack.hvp");
    join(kp, dir, L"kinepack.hvp");
    if (!file_exists(dp) || !file_exists(cp) || !file_exists(kp)) return BASE_UNKNOWN;

    unsigned char a[4], b[4], c[4];
    set_status(L"正在校验原版数据包（约 2.9 GB，请稍候）…");
    md5_prefix(dp, a, 0, 45);
    md5_prefix(cp, b, 45, 50);
    md5_prefix(kp, c, 50, 95);
    wlog(L"[base] dp=%02x%02x%02x%02x cach=%02x%02x%02x%02x kine=%02x%02x%02x%02x",
         a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3], c[0], c[1], c[2], c[3]);

    int dpVan = eq4(a, "23ef7697"), dpOde = eq4(a, "ecaabade");
    int dpZh = eq4(a, "e539d4e8") || eq4(a, "58599296") || eq4(a, "46eca646");
    int packsVan = eq4(b, "cb5e4c66") && eq4(c, "f93b9134");
    int packsOde = eq4(b, "7f12e2ab") && eq4(c, "9411a4c3");
    *isOdePacks = packsOde;

    if (dpVan && packsVan) return BASE_VANILLA;
    if (dpOde && packsOde) return BASE_ODE_EN;
    if (dpZh) return BASE_ZH;
    if (packsOde && !dpVan) return BASE_ZH;      /* 本补丁装过的 ODE 基底 */
    (void)silent;
    return BASE_UNKNOWN;
}

/* ---------- 主安装流程 ---------- */
static int g_abort = 0;

static int install_to(const wchar_t* dir, int doBak, int doWide, int doOde, int silent) {
    wlog(L"[install] dir='%s' bak=%d wide=%d ode=%d silent=%d pid=%lu",
         dir, doBak, doWide, doOde, silent, GetCurrentProcessId());
    if (!lstrlenW(dir)) { if (!silent) MessageBoxW(g_hMain, L"请先选择游戏目录。", L"提示", MB_ICONWARNING); return 2; }
    if (!is_game_dir(dir)) {
        if (!silent) MessageBoxW(g_hMain, L"该目录下没有找到 Obscure.exe。\n请确认选择的是游戏根目录。", L"路径无效", MB_ICONERROR);
        return 3;
    }
    if (!g_haveFt) { if (!silent) MessageBoxW(g_hMain, L"安装包已损坏（缺少载荷清单）。\n请重新下载。", L"错误", MB_ICONERROR); return 4; }

    HANDLE hSrc = open_self();
    if (hSrc == INVALID_HANDLE_VALUE) { if (!silent) MessageBoxW(g_hMain, L"无法打开安装包。", L"错误", MB_ICONERROR); return 6; }

    int packsOde = 0;
    int base = detect_base(dir, &packsOde, silent);
    if (base == BASE_UNKNOWN) {
        CloseHandle(hSrc);
        if (!silent) MessageBoxW(g_hMain,
            L"无法识别的数据包版本，已中止安装。\n\n"
            L"本补丁只支持：Steam 英文原版 / 本补丁装过的版本。\n"
            L"请用 Steam「验证游戏文件完整性」恢复原版后重试。",
            L"版本不匹配", MB_ICONERROR);
        return 8;
    }
    if (base == BASE_ZH) doOde = 0;       /* 已汉化基底不再叠 ODE */

    /* 需要重建数据包？ */
    int needRebuild = (base != BASE_ZH);

    /* 待备份/待写文件清单 */
    const wchar_t* core[] = { L"version.dll", L"big_atlas_c.raw", L"table_c.bin" };
    const wchar_t* wide[] = { L"dinput8.dll", L"scripts\\main.patch", L"scripts\\obscure.asi" };

    wchar_t bdir[MAX_PATH];
    join(bdir, dir, BACKUP_DIR);

    /* ---- 备份 ---- */
    if (doBak) {
        set_status(L"正在备份原文件…");
        mkdirs(bdir);
        wchar_t src[MAX_PATH], dst[MAX_PATH];
        const wchar_t* must[] = { L"datapack.hvp", L"cachpack.hvp", L"kinepack.hvp" };
        for (int i = 0; i < 3; i++) {
            join(src, dir, must[i]);
            if (!file_exists(src)) continue;
            join(dst, bdir, must[i]);
            if (file_exists(dst)) continue;                 /* 只备份首次 */
            if (!copy_file(src, dst, 5, 12)) {
                CloseHandle(hSrc);
                if (!silent) MessageBoxW(g_hMain, L"备份失败，已中止安装。\n请确认游戏未运行、磁盘空间充足（需约 3 GB）。", L"错误", MB_ICONERROR);
                return 5;
            }
        }
        for (int i = 0; i < 3; i++) {
            join(src, dir, core[i]);
            if (file_exists(src)) { join(dst, bdir, core[i]); if (!file_exists(dst)) copy_file(src, dst, 12, 13); }
            else backup_add2(bdir, core[i]);
        }
        if (doWide) {
            for (int i = 0; i < 3; i++) {
                join(src, dir, wide[i]);
                if (file_exists(src)) { join(dst, bdir, wide[i]); if (!file_exists(dst)) copy_file(src, dst, 13, 14); }
                else backup_add2(bdir, wide[i]);
            }
        }
    }

    /* ---- ODE 增强层（仅加强版） ---- */
    if (doOde) {
        if (!has_prefix("ode/")) {
            CloseHandle(hSrc);
            if (!silent) MessageBoxW(g_hMain, L"此安装包不含 ODE 增强层，无法安装加强版。", L"提示", MB_ICONWARNING);
            return 9;
        }
        Ent2* xd = find_ent("tool/xdelta.exe");
        if (!xd) { CloseHandle(hSrc); return 9; }

        wchar_t tmp[MAX_PATH], tdir[MAX_PATH];
        GetTempPathW(MAX_PATH, tmp);
        wsprintfW(tdir, L"%so1ode_%lu", tmp, GetCurrentProcessId());
        mkdirs(tdir);
        wchar_t xdp[MAX_PATH]; join(xdp, tdir, L"xdelta.exe");
        set_status(L"正在准备 ODE 增强层…");
        if (!extract_ent(hSrc, xd, xdp, 14, 15)) { CloseHandle(hSrc); del_tree(tdir); return 10; }

        struct { const wchar_t* file; const char* arc; unsigned long long minSz; } P[3] = {
            { L"datapack.hvp", "ode/L1_datapack_ode.patch", 120ULL * 1024 * 1024 },
            { L"cachpack.hvp", "ode/L1_cachpack_ode.patch", 4ULL * 1024 * 1024 },
            { L"kinepack.hvp", "ode/L1_kinepack_ode.patch", 2000000000ULL },
        };
        for (int i = 0; i < 3; i++) {
            Ent2* pe = find_ent(P[i].arc);
            if (!pe) { CloseHandle(hSrc); del_tree(tdir); return 10; }
            wchar_t pp[MAX_PATH]; join(pp, tdir, L"p.patch");
            set_status(L"正在释放 ODE 补丁…");
            if (!extract_ent(hSrc, pe, pp, 15, 20)) { CloseHandle(hSrc); del_tree(tdir); return 10; }

            wchar_t srcf[MAX_PATH], outf[MAX_PATH], par[1024];
            join(srcf, dir, P[i].file);
            join(outf, tdir, L"out.hvp");
            wsprintfW(par, L"-d -f -s \"%s\" \"%s\" \"%s\"", srcf, pp, outf);
            wchar_t what[128]; wsprintfW(what, L"正在打 ODE 补丁：%s", P[i].file);
            set_status(what);
            int rc = run_tool(xdp, par, tdir, 20 + i * 12, 20 + (i + 1) * 12, what);
            if (rc != 0 || !file_exists(outf)) {
                CloseHandle(hSrc); del_tree(tdir);
                if (!silent) MessageBoxW(g_hMain,
                    L"打 ODE 补丁失败，原文件未改动。\n\n"
                    L"源文件可能不是原版，请用 Steam「验证游戏文件完整性」后重试。",
                    L"错误", MB_ICONERROR);
                return 11;
            }
            if (fsize(outf) < P[i].minSz) {
                CloseHandle(hSrc); del_tree(tdir);
                if (!silent) MessageBoxW(g_hMain, L"补丁结果体积异常，已中止。", L"错误", MB_ICONERROR);
                return 11;
            }
            if (!copy_file(outf, srcf, 0, 0)) {
                CloseHandle(hSrc); del_tree(tdir);
                if (!silent) MessageBoxW(g_hMain, L"写入失败，请确认游戏已退出且磁盘空间充足。", L"错误", MB_ICONERROR);
                return 12;
            }
            DeleteFileW(outf);
            DeleteFileW(pp);
        }
        del_tree(tdir);

        /* data/ 增强资源 */
        set_status(L"正在写入 ODE 增强资源（data/，约 347 MB）…");
        int nData = 0;
        for (unsigned i = 0; i < g_ft.n; i++) {
            Ent2* e = &g_ft.ent[i];
            if (strncmp(e->name, "ode/data/", 9) != 0) continue;
            wchar_t wn[256];
            arc_to_win(e->name + 4, wn, 256);   /* 去掉 "ode/" */
            wchar_t dst[MAX_PATH]; join(dst, dir, wn);
            if (doBak) {
                wchar_t bdst[MAX_PATH]; join(bdst, bdir, wn);
                if (file_exists(dst)) { if (!file_exists(bdst)) copy_file(dst, bdst, 0, 0); }
                else backup_add2(bdir, wn);
            }
            if (!extract_ent(hSrc, e, dst, 56, 60)) { CloseHandle(hSrc); return 12; }
            nData++;
        }
        wlog(L"[ode] data files=%d", nData);
    }

    /* ---- 组件文件 ---- */
    set_status(L"正在释放组件文件…");
    int nBin = 0;
    for (unsigned i = 0; i < g_ft.n; i++) {
        Ent2* e = &g_ft.ent[i];
        if (strncmp(e->name, "bin/", 4) != 0) continue;
        wchar_t wn[256];
        arc_to_win(e->name + 4, wn, 256);
        /* 宽屏件按勾选跳过 */
        if (!doWide && (!lstrcmpW(wn, L"dinput8.dll") || !lstrcmpW(wn, L"scripts\\main.patch") || !lstrcmpW(wn, L"scripts\\obscure.asi")))
            continue;
        wchar_t dst[MAX_PATH]; join(dst, dir, wn);
        if (doBak) {
            wchar_t bdst[MAX_PATH]; join(bdst, bdir, wn);
            if (file_exists(dst)) { if (!file_exists(bdst)) copy_file(dst, bdst, 0, 0); }
            else backup_add2(bdir, wn);
        }
        wchar_t msg[256]; wsprintfW(msg, L"正在安装 %s …", wn);
        set_status(msg);
        if (!extract_ent(hSrc, e, dst, 60, 66)) {
            CloseHandle(hSrc);
            if (!silent) MessageBoxW(g_hMain, L"写入失败，请确认游戏已退出且磁盘空间充足。", L"错误", MB_ICONERROR);
            return 12;
        }
        /* 校验 */
        unsigned int c = 0xFFFFFFFFu;
        {
            HANDLE hf = CreateFileW(dst, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
            if (hf != INVALID_HANDLE_VALUE) {
                static BYTE b2[1 << 20];
                DWORD got = 0;
                while (ReadFile(hf, b2, sizeof(b2), &got, NULL) && got) c = crc_upd(c, b2, got);
                CloseHandle(hf);
            }
        }
        if (e->crc && (c ^ 0xFFFFFFFFu) != e->crc) {
            wlog(L"[warn] crc mismatch %s", wn);
        }
        nBin++;
    }
    wlog(L"[bin] files=%d", nBin);

    /* ---- 重建 datapack.hvp ---- */
    if (needRebuild) {
        Ent2* hv = find_ent("tool/obscure-hvp.exe");
        if (!hv) { CloseHandle(hSrc); if (!silent) MessageBoxW(g_hMain, L"安装包缺少数据包工具。", L"错误", MB_ICONERROR); return 13; }

        wchar_t tmp[MAX_PATH], tdir[MAX_PATH];
        GetTempPathW(MAX_PATH, tmp);
        wsprintfW(tdir, L"%so1rb_%lu", tmp, GetCurrentProcessId());
        mkdirs(tdir);
        wchar_t hvp[MAX_PATH], base_hvp[MAX_PATH], exdir[MAX_PATH], new_hvp[MAX_PATH];
        join(hvp, tdir, L"obscure-hvp.exe");
        join(base_hvp, tdir, L"base.hvp");
        join(exdir, tdir, L"ex");
        join(new_hvp, tdir, L"new.hvp");

        set_status(L"正在准备数据包工具…");
        if (!extract_ent(hSrc, hv, hvp, 66, 68)) { CloseHandle(hSrc); del_tree(tdir); return 14; }

        wchar_t dp[MAX_PATH]; join(dp, dir, L"datapack.hvp");
        set_status(L"正在复制原版数据包到临时目录…");
        if (!copy_file(dp, base_hvp, 68, 72)) { CloseHandle(hSrc); del_tree(tdir); return 14; }

        wchar_t par[1024];
        wsprintfW(par, L"-g obscure1 extract \"%s\" \"%s\"", base_hvp, exdir);
        set_status(L"正在解包原版数据包（约 1~3 分钟，请勿关闭）…");
        if (run_tool(hvp, par, tdir, 72, 84, L"正在解包原版数据包") != 0 || !dir_exists(exdir)) {
            CloseHandle(hSrc); del_tree(tdir);
            if (!silent) MessageBoxW(g_hMain, L"解包失败，原文件未改动。\n请用 Steam 验证文件完整性后重试。", L"错误", MB_ICONERROR);
            return 15;
        }

        /* 替换译文 */
        Ent2* lng = find_ent("rb/zh_en.lng");
        if (lng) {
            wchar_t t[MAX_PATH]; join(t, exdir, L"_texts\\en.lng");
            if (extract_ent(hSrc, lng, t, 84, 86)) wlog(L"[rb] lng replaced");
        }
        /* 替换地图贴图：解包树里的目标目录是 _common\maps_pc */
        int nTex = 0;
        wchar_t texdir[MAX_PATH];
        join(texdir, exdir, L"_common\\maps_pc");
        if (!dir_exists(texdir)) {
            CloseHandle(hSrc); del_tree(tdir);
            if (!silent) MessageBoxW(g_hMain,
                L"解包结果中未找到 _common\\maps_pc，数据包结构不匹配。\n"
                L"请用 Steam 验证文件完整性后重试。", L"错误", MB_ICONERROR);
            return 15;
        }
        for (unsigned i = 0; i < g_ft.n; i++) {
            Ent2* e = &g_ft.ent[i];
            if (strncmp(e->name, "rb/maps_pc/", 11) != 0) continue;
            wchar_t wn[256];
            arc_to_win(e->name + 3, wn, 256);   /* "maps_pc\\x.bmp" */
            wchar_t* fn = wn;
            for (wchar_t* q = wn; *q; q++) if (*q == L'\\') fn = q + 1;
            wchar_t t[MAX_PATH]; join(t, texdir, fn);
            if (extract_ent(hSrc, e, t, 86, 88)) nTex++;
        }
        wlog(L"[rb] map textures=%d", nTex);

        wsprintfW(par, L"-g obscure1 create \"%s\" \"%s\" \"%s\"", base_hvp, exdir, new_hvp);
        set_status(L"正在重建中文数据包（约 1~3 分钟，请勿关闭）…");
        if (run_tool(hvp, par, tdir, 88, 96, L"正在重建中文数据包") != 0 || !file_exists(new_hvp)) {
            CloseHandle(hSrc); del_tree(tdir);
            if (!silent) MessageBoxW(g_hMain, L"重建数据包失败，原文件未改动。\n请用 Steam 验证文件完整性后重试。", L"错误", MB_ICONERROR);
            return 16;
        }
        if (fsize(new_hvp) < 100ULL * 1024 * 1024) {
            CloseHandle(hSrc); del_tree(tdir);
            if (!silent) MessageBoxW(g_hMain, L"重建结果体积异常，已拒绝写入。", L"错误", MB_ICONERROR);
            return 16;
        }
        set_status(L"正在写入游戏目录…");
        if (!copy_file(new_hvp, dp, 96, 99)) {
            CloseHandle(hSrc); del_tree(tdir);
            if (!silent) MessageBoxW(g_hMain, L"写入失败，请确认游戏已退出。", L"错误", MB_ICONERROR);
            return 17;
        }
        del_tree(tdir);
    }

    CloseHandle(hSrc);
    set_prog(100);
    set_status(L"安装完成");
    /* 两种模式都做组件自校验，结果写进日志；GUI 模式再显示到完成提示里 */
    wchar_t chkrep[512] = { 0 };
    int badnum = check_components(dir, chkrep, 512);
    wlog(L"[chk] bad=%d", badnum);
    if (!silent) {
        wchar_t body[1600];
        wsprintfW(body,
            L"汉化补丁安装完成。\r\n\r\n"
            L"· 数据包：%s\r\n"
            L"· 组件：version.dll / big_atlas_c.raw / table_c.bin\r\n"
            L"· 宽屏补丁：%s\r\n"
            L"· ODE 增强层：%s\r\n"
            L"\r\n【组件校验】%s%s\r\n"
            L"\r\n直接启动游戏即可。若需还原，请点“还原原版”。",
            needRebuild ? L"已由原版差分重建为中文版" : L"已是汉化版，未重复重建",
            doWide ? L"已安装（dinput8.dll + scripts）" : L"未安装",
            doOde ? L"已安装" : L"未安装",
            badnum ? L"有文件与预期不一致，请还原后重装" : L"4/4 全部一致",
            chkrep);
        {
            /* v1.02：完成提示 3 秒后自动关闭，装完直接退出，无需再点 */
            typedef int (WINAPI* PFN_MBT)(HWND, LPCWSTR, LPCWSTR, UINT, WORD, DWORD);
            HMODULE u32 = GetModuleHandleW(L"user32.dll");
            PFN_MBT pMBT = (PFN_MBT)(u32 ? (void*)GetProcAddress(u32, "MessageBoxTimeoutW") : NULL);
            if (pMBT) pMBT(g_hMain, body, L"完成", MB_ICONINFORMATION, 0, 3000);
            else     MessageBoxW(g_hMain, body, L"完成", MB_ICONINFORMATION);
        }
        /* v1.02 fix: 点掉完成提示后自动退出，不再停在主界面 */
        PostMessageW(g_hMain, WM_CLOSE, 0, 0);
    }
    return 0;
}

/* ---------- 还原 ---------- */
static int restore_from(const wchar_t* dir, int silent) {
    if (!is_game_dir(dir)) {
        if (!silent) MessageBoxW(g_hMain, L"目标目录不是游戏根目录。", L"提示", MB_ICONWARNING);
        return 2;
    }
    wchar_t bdir[MAX_PATH];
    join(bdir, dir, BACKUP_DIR);
    if (!dir_exists(bdir)) {
        if (!silent) MessageBoxW(g_hMain, L"未找到备份目录 _obscure_backup，无法自动还原。\n请用 Steam「验证游戏文件完整性」恢复。", L"还原失败", MB_ICONWARNING);
        return 1;
    }
    set_status(L"正在还原…");

    /* 1) 删除“安装前不存在”的文件 */
    wchar_t listp[MAX_PATH];
    join(listp, bdir, ADDED_LIST);
    HANDLE h = CreateFileW(listp, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        static wchar_t all[65536];
        DWORD got = 0;
        ReadFile(h, all, sizeof(all) - 2, &got, NULL);
        CloseHandle(h);
        all[got / 2] = 0;
        wchar_t* q = all;
        if (*q == 0xFEFF) q++;
        while (*q) {
            wchar_t* e = wcschr(q, L'\n');
            if (e) *e = 0;
            size_t L = wcslen(q);
            if (L && q[L - 1] == L'\r') q[L - 1] = 0;
            if (*q) {
                wchar_t t[MAX_PATH]; join(t, dir, q);
                DeleteFileW(t);
            }
            if (!e) break;
            q = e + 1;
        }
    }
    /* 2) 备份文件覆盖回去 */
    wchar_t pat[MAX_PATH];
    join(pat, bdir, L"*");
    WIN32_FIND_DATAW fd;
    h = FindFirstFileW(pat, &fd);
    int n = 0;
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!lstrcmpW(fd.cFileName, L".") || !lstrcmpW(fd.cFileName, L"..")) continue;
            if (!lstrcmpW(fd.cFileName, ADDED_LIST)) continue;
            wchar_t src[MAX_PATH], dst[MAX_PATH];
            join(src, bdir, fd.cFileName);
            join(dst, dir, fd.cFileName);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;  /* 仅根级文件 */
            if (copy_file(src, dst, 0, 0)) n++;
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    /* 3) 备份目录内的子目录（scripts 等） */
    {
        wchar_t sub[MAX_PATH];
        const wchar_t* dirs[] = { L"scripts" };
        for (int i = 0; i < 1; i++) {
            join(sub, bdir, dirs[i]);
            if (!dir_exists(sub)) continue;
            wchar_t sp[MAX_PATH]; join(sp, sub, L"*");
            HANDLE h2 = FindFirstFileW(sp, &fd);
            if (h2 == INVALID_HANDLE_VALUE) continue;
            do {
                if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
                wchar_t src[MAX_PATH], dst[MAX_PATH];
                join(src, sub, fd.cFileName);
                wchar_t sub2[MAX_PATH]; join(sub2, dir, dirs[i]);
                join(dst, sub2, fd.cFileName);
                if (copy_file(src, dst, 0, 0)) n++;
            } while (FindNextFileW(h2, &fd));
            FindClose(h2);
        }
    }
    /* 4) 清理空的 scripts 目录 */
    {
        wchar_t sd[MAX_PATH];
        join(sd, dir, L"scripts");
        RemoveDirectoryW(sd);
    }
    del_tree(bdir);
    set_prog(0);
    set_status(L"就绪");
    wlog(L"[restore] restored=%d", n);
    if (!silent) {
        wchar_t body[512];
        wsprintfW(body, L"已还原 %d 个文件，并删除本次新增的补丁文件。\n备份目录 _obscure_backup 已清理。", n);
        MessageBoxW(g_hMain, body, L"还原完成", MB_ICONINFORMATION);
    }
    return 0;
}

/* ---------- 界面 ---------- */
static void do_install(void) {
    wchar_t dir[MAX_PATH];
    GetWindowTextW(g_hPath, dir, MAX_PATH);
    int doBak = (SendMessageW(g_hChkBackup, BM_GETCHECK, 0, 0) == BST_CHECKED);
    int doWide = (SendMessageW(g_hChkWide, BM_GETCHECK, 0, 0) == BST_CHECKED);
    int doOde = g_hasOde && (SendMessageW(g_hChkOde, BM_GETCHECK, 0, 0) == BST_CHECKED);
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_INSTALL), FALSE);
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_RESTORE), FALSE);
    int rc = install_to(dir, doBak, doWide, doOde, 0);
    if (rc != 0) { set_prog(0); set_status(rc == 2 || rc == 3 ? L"就绪" : L"安装失败"); }
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_INSTALL), TRUE);
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_RESTORE), TRUE);
}
static void do_restore(void) {
    wchar_t dir[MAX_PATH];
    GetWindowTextW(g_hPath, dir, MAX_PATH);
    if (!lstrlenW(dir)) return;
    if (MessageBoxW(g_hMain, L"将删除汉化补丁，并用 _obscure_backup 还原原文件。\n继续吗？", L"还原原版", MB_YESNO | MB_ICONQUESTION) != IDYES) return;
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_INSTALL), FALSE);
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_RESTORE), FALSE);
    restore_from(dir, 0);
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_INSTALL), TRUE);
    EnableWindow(GetDlgItem(g_hMain, ID_BTN_RESTORE), TRUE);
}
static void browse(void) {
    BROWSEINFOW bi; ZeroMemory(&bi, sizeof(bi));
    bi.hwndOwner = g_hMain;
    bi.lpszTitle = L"选择 ObsCure 游戏根目录（含 Obscure.exe）";
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST id = SHBrowseForFolderW(&bi);
    if (id) {
        wchar_t p[MAX_PATH];
        if (SHGetPathFromIDListW(id, p)) SetWindowTextW(g_hPath, p);
        CoTaskMemFree(id);
    }
}

static void paint_main(HWND h, HDC dc) {
    RECT rc; GetClientRect(h, &rc);
    FillRect(dc, &rc, g_bg);
    RECT band = { 0, 0, rc.right, S(64) };
    FillRect(dc, &band, g_band);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, RGB(255, 255, 255));
    SelectObject(dc, g_fTitle);
    RECT t = { S(22), S(10), rc.right - S(20), S(42) };
    DrawTextW(dc, kTitle, -1, &t, DT_LEFT | DT_SINGLELINE | DT_VCENTER);
    SelectObject(dc, g_fSub);
    SetTextColor(dc, RGB(160, 180, 214));
    RECT s = { S(24), S(40), rc.right - S(20), S(60) };
    DrawTextW(dc, kSub, -1, &s, DT_LEFT | DT_SINGLELINE | DT_VCENTER);

    HGDIOBJ ob2 = SelectObject(dc, g_line);
    HGDIOBJ obr = SelectObject(dc, GetStockObject(NULL_BRUSH));
    Rectangle(dc, S(19), S(79), rc.right - S(19), S(383));
    SelectObject(dc, obr);
    SelectObject(dc, ob2);

    HGDIOBJ op = SelectObject(dc, g_line);
    MoveToEx(dc, S(20), S(398), NULL);
    LineTo(dc, rc.right - S(20), S(398));
    SelectObject(dc, op);
}

static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_CREATE: {
        RECT rc; GetClientRect(h, &rc);
        int W = rc.right;
        g_hInfo = CreateWindowExW(0, L"EDIT", kInfo,
            WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_TABSTOP | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
            S(20), S(80), W - S(40), S(302), h, (HMENU)ID_EDIT_INFO, g_hInst, NULL);
        SendMessageW(g_hInfo, WM_SETFONT, (WPARAM)g_fBody, TRUE);
        SendMessageW(g_hInfo, EM_SETSEL, 0, 0);
        SendMessageW(g_hInfo, WM_VSCROLL, SB_TOP, 0);

        CreateWindowExW(0, L"STATIC", L"游戏目录：", WS_CHILD | WS_VISIBLE,
            S(20), S(412), S(76), S(22), h, NULL, g_hInst, NULL);
        g_hPath = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL, S(98), S(409), W - S(216), S(27), h, (HMENU)ID_EDIT_PATH, g_hInst, NULL);
        SendMessageW(g_hPath, WM_SETFONT, (WPARAM)g_fBody, TRUE);
        CreateWindowExW(0, L"BUTTON", L"浏览…", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
            W - S(108), S(408), S(88), S(29), h, (HMENU)ID_BTN_BROWSE, g_hInst, NULL);

        g_hChkBackup = CreateWindowExW(0, L"BUTTON", L"安装前备份原文件（推荐）",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX, S(98), S(442), S(320), S(22), h, (HMENU)ID_CHK_BACKUP, g_hInst, NULL);
        SendMessageW(g_hChkBackup, BM_SETCHECK, BST_CHECKED, 0);
        g_hChkWide = CreateWindowExW(0, L"BUTTON", L"同时安装宽屏补丁（ThirteenAG Widescreen Fix）",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX, S(98), S(466), S(460), S(22), h, (HMENU)ID_CHK_WIDE, g_hInst, NULL);
        SendMessageW(g_hChkWide, BM_SETCHECK, BST_CHECKED, 0);
        SendMessageW(g_hChkBackup, WM_SETFONT, (WPARAM)g_fBody, TRUE);
        SendMessageW(g_hChkWide, WM_SETFONT, (WPARAM)g_fBody, TRUE);

        if (g_hasOde) {
            g_hChkOde = CreateWindowExW(0, L"BUTTON", L"安装 ODE 增强层（约 1.6 GB，仅支持 Steam 英文原版基底）",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX, S(98), S(490), S(520), S(22), h, (HMENU)ID_CHK_ODE, g_hInst, NULL);
            SendMessageW(g_hChkOde, BM_SETCHECK, BST_CHECKED, 0);
            SendMessageW(g_hChkOde, WM_SETFONT, (WPARAM)g_fBody, TRUE);
        }

        g_hProg = CreateWindowExW(0, PROGRESS_CLASSW, NULL, WS_CHILD | WS_VISIBLE,
            S(20), S(520), W - S(40), S(16), h, (HMENU)ID_PROGRESS, g_hInst, NULL);
        SendMessageW(g_hProg, PBM_SETRANGE, 0, MAKELPARAM(0, 100));

        g_hStatus = CreateWindowExW(0, L"STATIC", L"就绪", WS_CHILD | WS_VISIBLE,
            S(20), S(542), W - S(40), S(20), h, (HMENU)ID_STATUS, g_hInst, NULL);
        SendMessageW(g_hStatus, WM_SETFONT, (WPARAM)g_fBody, TRUE);

        CreateWindowExW(0, L"BUTTON", L"开始安装", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
            W - S(364), S(570), S(108), S(34), h, (HMENU)ID_BTN_INSTALL, g_hInst, NULL);
        CreateWindowExW(0, L"BUTTON", L"还原原版", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
            W - S(248), S(570), S(108), S(34), h, (HMENU)ID_BTN_RESTORE, g_hInst, NULL);
        CreateWindowExW(0, L"BUTTON", L"退出", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
            W - S(132), S(570), S(112), S(34), h, (HMENU)ID_BTN_EXIT, g_hInst, NULL);

        wchar_t gd[MAX_PATH]; guess_game_dir(gd, MAX_PATH); SetWindowTextW(g_hPath, gd);
        return 0;
    }
    case WM_CTLCOLORSTATIC: {
        HDC dc = (HDC)w;
        SetTextColor(dc, RGB(27, 32, 48));
        if ((HWND)l == g_hInfo) {
            SetBkMode(dc, OPAQUE);
            SetBkColor(dc, RGB(255, 255, 255));
            return (LRESULT)g_white;
        }
        SetBkMode(dc, TRANSPARENT);
        return (LRESULT)g_bg;
    }
    case WM_ERASEBKGND:
        return 1;
    case WM_PAINT: {
        PAINTSTRUCT ps; HDC dc = BeginPaint(h, &ps);
        RECT rc; GetClientRect(h, &rc);
        HDC mdc = CreateCompatibleDC(dc);
        HBITMAP bmp = CreateCompatibleBitmap(dc, rc.right, rc.bottom);
        HGDIOBJ ob = SelectObject(mdc, bmp);
        paint_main(h, mdc);
        BitBlt(dc, 0, 0, rc.right, rc.bottom, mdc, 0, 0, SRCCOPY);
        SelectObject(mdc, ob);
        DeleteObject(bmp); DeleteDC(mdc);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_MOUSEWHEEL: {
        POINT pt; pt.x = GET_X_LPARAM(l); pt.y = GET_Y_LPARAM(l);
        if (g_hInfo && WindowFromPoint(pt) == g_hInfo) {
            SendMessageW(g_hInfo, WM_MOUSEWHEEL, w, l);
            return 0;
        }
        break;
    }
    case WM_COMMAND:
        switch (LOWORD(w)) {
        case ID_BTN_BROWSE:  browse(); return 0;
        case ID_BTN_INSTALL: do_install(); return 0;
        case ID_BTN_RESTORE: do_restore(); return 0;
        case ID_BTN_EXIT:    DestroyWindow(h); return 0;
        }
        return 0;
    case WM_CLOSE: DestroyWindow(h); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

/* ---------- 免责声明弹窗（点「确认」后才进主界面） ---------- */
static HWND g_hDlg;
static int  g_dlgOk = 0;

static LRESULT CALLBACK DlgProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_CREATE: {
        RECT rc; GetClientRect(h, &rc);
        HWND e = CreateWindowExW(0, L"EDIT", kInfo,
            WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
            S(18), S(18), rc.right - S(36), rc.bottom - S(78), h, (HMENU)ID_EDIT_INFO, g_hInst, NULL);
        SendMessageW(e, WM_SETFONT, (WPARAM)g_fBody, TRUE);
        SendMessageW(e, EM_SETSEL, 0, 0);
        SendMessageW(e, WM_VSCROLL, SB_TOP, 0);
        CreateWindowExW(0, L"BUTTON", L"确认", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
            rc.right - S(230), rc.bottom - S(52), S(100), S(32), h, (HMENU)IDD_OK, g_hInst, NULL);
        CreateWindowExW(0, L"BUTTON", L"退出", WS_CHILD | WS_VISIBLE,
            rc.right - S(122), rc.bottom - S(52), S(100), S(32), h, (HMENU)IDD_QUIT, g_hInst, NULL);
        return 0;
    }
    case WM_CTLCOLORSTATIC: {
        HDC dc = (HDC)w;
        SetTextColor(dc, RGB(27, 32, 48));
        if ((HWND)l == GetDlgItem(h, ID_EDIT_INFO)) {
            SetBkMode(dc, OPAQUE);
            SetBkColor(dc, RGB(255, 255, 255));
            return (LRESULT)g_white;
        }
        SetBkMode(dc, TRANSPARENT);
        return (LRESULT)g_bg;
    }
    case WM_ERASEBKGND: return 1;
    case WM_PAINT: {
        PAINTSTRUCT ps; HDC dc = BeginPaint(h, &ps);
        RECT rc; GetClientRect(h, &rc);
        FillRect(dc, &rc, g_bg);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_COMMAND:
        if (LOWORD(w) == IDD_OK)     { g_dlgOk = 1; DestroyWindow(h); return 0; }
        if (LOWORD(w) == IDD_QUIT)   { g_dlgOk = 0; DestroyWindow(h); return 0; }
        return 0;
    case WM_CLOSE: g_dlgOk = 0; DestroyWindow(h); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static int show_disclaimer(void) {
    WNDCLASSEXW wc; ZeroMemory(&wc, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = DlgProc;
    wc.hInstance = g_hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = g_bg;
    wc.lpszClassName = L"O1DisclaimerWnd";
    RegisterClassExW(&wc);

    RECT rc = { 0, 0, S(720), S(560) };
    DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_CLIPCHILDREN;
    AdjustWindowRect(&rc, style, FALSE);
    int W = rc.right - rc.left, H = rc.bottom - rc.top;
    int sx = (GetSystemMetrics(SM_CXSCREEN) - W) / 2;
    int sy = (GetSystemMetrics(SM_CYSCREEN) - H) / 2;
    if (sy < 0) sy = 0;
    g_hDlg = CreateWindowExW(WS_EX_TOPMOST, wc.lpszClassName, L"免责与使用声明 · 请阅读后确认",
        style, sx, sy, W, H, NULL, NULL, g_hInst, NULL);
    if (!g_hDlg) return 1;
    ShowWindow(g_hDlg, SW_SHOW);
    UpdateWindow(g_hDlg);

    MSG msg;
    while (IsWindow(g_hDlg) && GetMessageW(&msg, NULL, 0, 0)) {
        if (IsDialogMessageW(g_hDlg, &msg)) continue;
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return g_dlgOk;
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, LPWSTR cmd, int show) {
    g_hInst = hi;
    (void)hp;

    if (cmd && cmd[0] == L'/' && wcsncmp(cmd, L"/test:", 6) == 0) {
        wchar_t dir[MAX_PATH];
        lstrcpynW(dir, cmd + 6, MAX_PATH);
        for (int i = (int)lstrlenW(dir) - 1; i >= 0; i--) {
            if (dir[i] == L' ' || dir[i] == L'\t' || dir[i] == L'\r' || dir[i] == L'\n') dir[i] = 0;
            else break;
        }
        crc_init();
        if (!load_footer()) return 9;
        g_hasOde = has_prefix("ode/");
        return install_to(dir, 1, 1, g_hasOde, 1);
    }
    if (cmd && wcsncmp(cmd, L"/restore:", 9) == 0) {
        wchar_t dir[MAX_PATH];
        lstrcpynW(dir, cmd + 9, MAX_PATH);
        for (int i = (int)lstrlenW(dir) - 1; i >= 0; i--) {
            if (dir[i] == L' ' || dir[i] == L'\t' || dir[i] == L'\r' || dir[i] == L'\n') dir[i] = 0;
            else break;
        }
        crc_init();
        load_footer();
        return restore_from(dir, 1);
    }

    {
        HDC sdc = GetDC(NULL);
        int dpi = GetDeviceCaps(sdc, LOGPIXELSX);
        ReleaseDC(NULL, sdc);
        if (dpi >= 96) g_s = MulDiv(dpi, 100, 96);
        SetProcessDPIAware();
    }
    CoInitialize(NULL);
    INITCOMMONCONTROLSEX ic; ic.dwSize = sizeof(ic); ic.dwICC = ICC_PROGRESS_CLASS;
    InitCommonControlsEx(&ic);
    crc_init();

    g_fTitle = CreateFontW(-MulDiv(24, g_s, 100), 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Microsoft YaHei UI");
    g_fSub   = CreateFontW(-MulDiv(12, g_s, 100), 0, 0, 0, FW_NORMAL,   0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Microsoft YaHei UI");
    g_fBody  = CreateFontW(-MulDiv(15, g_s, 100), 0, 0, 0, FW_NORMAL,   0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Microsoft YaHei UI");
    g_bg    = CreateSolidBrush(RGB(245, 247, 250));
    g_band  = CreateSolidBrush(RGB(31, 42, 68));
    g_white = CreateSolidBrush(RGB(255, 255, 255));
    g_line  = CreatePen(PS_SOLID, 1, RGB(215, 220, 232));

    load_footer();
    g_hasOde = has_prefix("ode/");
    if (g_hasOde) {
        kTitle    = L"ObsCure 加强汉化版";
        kSub      = L"V1.02  ·  2026-10  ·  适用于 Steam 版  ·  ODE 增强层 + 简体中文汉化";
        kWinTitle = L"ObsCure 加强汉化版 V1.02";
    }

    if (!show_disclaimer()) return 0;

    WNDCLASSEXW wc; ZeroMemory(&wc, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hi;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = g_bg;
    wc.lpszClassName = L"O1SetupWnd";
    RegisterClassExW(&wc);

    RECT rc = { 0, 0, S(780), S(622) };
    DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN;
    AdjustWindowRect(&rc, style, FALSE);
    int W = rc.right - rc.left, H = rc.bottom - rc.top;
    int sx = (GetSystemMetrics(SM_CXSCREEN) - W) / 2;
    int sy = (GetSystemMetrics(SM_CYSCREEN) - H) / 2;
    if (sy < 0) sy = 0;
    g_hMain = CreateWindowExW(0, wc.lpszClassName, kWinTitle,
        style, sx, sy, W, H, NULL, NULL, hi, NULL);
    if (!g_hMain) return 0;
    ShowWindow(g_hMain, show ? show : SW_SHOWNORMAL);
    /* v1.02 fix: 免责声明是 TOPMOST 窗口，销毁后主窗会落到其它窗口后面 */
    BringWindowToTop(g_hMain);
    SetForegroundWindow(g_hMain);
    SetWindowPos(g_hMain, HWND_TOPMOST, 0, 0, 0, 0,
                 SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
    UpdateWindow(g_hMain);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    return 0;
}
