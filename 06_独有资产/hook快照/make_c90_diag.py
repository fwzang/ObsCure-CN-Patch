# -*- coding: utf-8 -*-
"""c90 · T2 前置诊断版生成器
======================================================================
严格约束（本轮）：
  · 只增加日志和计数器，**不改变任何运行行为**
  · 不调用 GetTexture / 不新增 COM 引用 / 不强制绑定 g_bigTex
  · 不执行额外的 apply_params_fast
  · 不改恢复逻辑

布局：
  A. Hook_Present 之前：前向声明（diag_rearm_tick / diag_reset_snapshot / Hook_SetTexture）
  B. Hook_SetTexture 之前：全局变量 + 诊断函数定义（此处所有被引用变量均已声明）
     并且不引用 g_resetCount（改用自建 g_dResetN），彻底避开声明顺序问题
"""
import io

SRC = r'E:\SteamLibrary\steamapps\common\Obscure\hzp_tools\hook\version_proxy.c'

FWD = '''/* ★ c90 诊断：前向声明（定义在下方，Hook_Present 要用） */
static void diag_rearm_tick(void* dev);
static void diag_reset_snapshot(void* dev);
static long WINAPI Hook_SetTexture(void* self, unsigned long stage, void* tex);

'''

BODY = r'''
/* ============================================================
 *  ★ c90 · T2 前置诊断（只加日志/计数器，零行为改动）
 * ============================================================ */
#ifndef DIAG_T2
#define DIAG_T2 0
#endif
#if DIAG_T2
static long  g_dST_enter       = 0;   /* Hook_SetTexture 进入次数 */
static long  g_dST_stage0      = 0;   /* stage == 0 的次数 */
static long  g_dST_texNull     = 0;   /* tex == NULL */
static long  g_dST_matchFont   = 0;   /* tex == g_fontTex */
static long  g_dST_gateOK      = 0;   /* 满足开闸三条件 */
static long  g_dST_swapped     = 0;   /* 实际替换为大图集 */
static long  g_dWD_enter       = 0;   /* watchdog_swap 进入次数 */
static long  g_dWD_exit[6]     = {0}; /* 退出原因计数 */
static void* g_dLastDev        = NULL;
static long  g_dLastResetFrame = 0;
static long  g_dResetN         = 0;   /* 自建 Reset 计数（避免依赖 g_resetCount 的声明位置） */
#endif

/* watchdog 退出原因（仅计数，绝不改行为） */
static void wd_exit(int r) {
#if DIAG_T2
    if (r >= 0 && r < 6) g_dWD_exit[r]++;
#endif
    (void)r;
}

/* ★ hook 有效性：纯内存读取函数指针，零 D3D 调用、零 COM 引用 */
static const char* hook_state_str(void* dev) {
    if (!dev) return "dev=NULL";
    void** vt = *(void***)dev;
    if (!vt) return "vt=NULL";
    if (vt[65] == (void*)Hook_SetTexture) return "OK(仍是我们的钩子)";
    return "CHANGED(已被换掉)";
}

/* ★ 每次 Reset 成功后打印整体快照，并清零本轮计数 */
static void diag_reset_snapshot(void* dev) {
    char b[900], t[96];
    b[0] = 0;
    lstrcatA(b, "\r\n[DIAG-RESET] ===== 第 ");
    wsprintfA(t, "%ld", g_dResetN); lstrcatA(b, t);
    lstrcatA(b, " 次 Reset 后快照 =====\r\n");
    wsprintfA(t, "  dev=%p  g_devPtr=%p  上次dev=%p  %s\r\n",
              dev, g_devPtr, g_dLastDev,
              (dev && g_dLastDev && dev != g_dLastDev) ? "<<< 设备指针变了!" : "");
    lstrcatA(b, t);
    wsprintfA(t, "  vt[65] SetTexture hook = %s\r\n", hook_state_str(dev));
    lstrcatA(b, t);
    wsprintfA(t, "  g_fontTex=%p  g_bigTex=%p  g_fontTexBound=%ld  rearm=%d  postRebuild=%d\r\n",
              g_fontTex, g_bigTex, g_fontTexBound, g_rearmPending, g_postResetNeedRebuild);
    lstrcatA(b, t);
    wsprintfA(t, "  g_tblPtr=0x%lX  discovery=%d  subOn=%d  frame=%ld (距上次Reset %ld 帧)\r\n",
              (unsigned long)g_tblPtr, g_discoveryDone, g_subOn, g_frames,
              g_frames - g_dLastResetFrame);
    lstrcatA(b, t);
    lstrcatA(b, "  --- 自上次 Reset 以来的统计 ---\r\n");
    wsprintfA(t, "    SetTexture 进入=%ld  stage0=%ld  texNULL=%ld  tex==fontTex=%ld\r\n",
              g_dST_enter, g_dST_stage0, g_dST_texNull, g_dST_matchFont);
    lstrcatA(b, t);
    wsprintfA(t, "    满足开闸=%ld  实际替换=%ld\r\n", g_dST_gateOK, g_dST_swapped);
    lstrcatA(b, t);
    wsprintfA(t, "    watchdog 进入=%ld  退出[0]=%ld [1]=%ld [2]=%ld [3]=%ld [4]=%ld\r\n",
              g_dWD_enter, g_dWD_exit[0], g_dWD_exit[1], g_dWD_exit[2],
              g_dWD_exit[3], g_dWD_exit[4]);
    lstrcatA(b, t);
    lstrcatA(b, "    退出原因: 0=开关关 1=缺前置 2=无vt[3] 3=设备丢失 4=stage0不匹配\r\n");
    HKLOG(b);
    g_dST_enter = g_dST_stage0 = g_dST_texNull = g_dST_matchFont = 0;
    g_dST_gateOK = g_dST_swapped = g_dWD_enter = 0;
    for (int i = 0; i < 6; i++) g_dWD_exit[i] = 0;
    g_dLastDev = dev;
    g_dLastResetFrame = g_frames;
}

/* ★ 恢复窗口内定期打印恢复态演化（每 200 帧一次） */
static void diag_rearm_tick(void* dev) {
    if (!g_rearmPending && !g_postResetNeedRebuild) return;
    if ((g_frames % 200) != 0) return;
    char b[520], t[96];
    b[0] = 0;
    lstrcatA(b, "[DIAG-REARM] f=");
    wsprintfA(t, "%ld", g_frames); lstrcatA(b, t);
    lstrcatA(b, " (距Reset ");
    wsprintfA(t, "%ld", g_frames - g_dLastResetFrame); lstrcatA(b, t);
    lstrcatA(b, "帧) hook=");
    lstrcatA(b, hook_state_str(dev));
    wsprintfA(t, " fontTex=%p bigTex=%p bound=%ld", g_fontTex, g_bigTex, g_fontTexBound);
    lstrcatA(b, t);
    wsprintfA(t, " ST进入=%ld 匹配=%ld 开闸=%ld WD进入=%ld",
              g_dST_enter, g_dST_matchFont, g_dST_gateOK, g_dWD_enter);
    lstrcatA(b, t);
    lstrcatA(b, "\r\n");
    HKLOG(b);
}
'''


def patch():
    s = io.open(SRC, encoding='utf-8', errors='surrogateescape').read()

    # A) 前向声明：Hook_Present 之前
    a1 = 'static long WINAPI Hook_Present(void* self, const void* src, const void* dst,'
    assert a1 in s, 'Hook_Present anchor not found'
    s = s.replace(a1, FWD + a1, 1)

    # B) 全局变量 + 诊断函数：Hook_SetTexture 之前
    a2 = ('static long WINAPI Hook_SetTexture(void* self, unsigned long stage, void* tex) {\n'
          '    g_ckpt = "ST:1-enter";')
    assert a2 in s, 'Hook_SetTexture def anchor not found'
    s = s.replace(a2, BODY + '\n' + a2, 1)

    # C) Hook_SetTexture 入口计数（紧跟 g_ckpt 之后）
    s = s.replace(a2, a2 + '''
#if DIAG_T2
    g_dST_enter++;
    if (stage == 0) g_dST_stage0++;
    if (!tex) g_dST_texNull++;
    else if (tex == g_fontTex) g_dST_matchFont++;
    if (tex && tex == g_fontTex && g_bigTex && g_subOn) g_dST_gateOK++;
#endif''', 1)

    # D) 实际替换计数
    a4 = '        tex = g_bigTex;      /* c49 二分：不做替换，只观察 */'
    if a4 in s:
        s = s.replace(a4, a4 + '''
#if DIAG_T2
        g_dST_swapped++;
#endif''', 1)

    # E) watchdog 进入 + 退出原因
    a5 = '    if (!g_fontTex || !g_bigTex || !dev || !g_orig_SetTexture) return 0;'
    assert a5 in s, 'watchdog anchor1 not found'
    s = s.replace(a5, '''#if DIAG_T2
    g_dWD_enter++;
#endif
    if (!g_fontTex || !g_bigTex || !dev || !g_orig_SetTexture) { wd_exit(1); return 0; }''', 1)

    a6 = ('    void** vt = *(void***)dev;\n'
          '    if (!vt || !vt[3]) return 0;\n'
          '    /* ★ 只在设备完全可用时动作（丢失/待重置时一律不碰纹理） */\n'
          '    if (((PFN_TestCoop)vt[3])(dev) != 0) return 0;\n'
          '    void* b0 = get_bound_tex0(dev);\n'
          '    if (b0 != g_fontTex) return 0;')
    assert a6 in s, 'watchdog anchor2 not found'
    s = s.replace(a6, '''    void** vt = *(void***)dev;
    if (!vt || !vt[3]) { wd_exit(2); return 0; }
    /* ★ 只在设备完全可用时动作（丢失/待重置时一律不碰纹理） */
    if (((PFN_TestCoop)vt[3])(dev) != 0) { wd_exit(3); return 0; }
    void* b0 = get_bound_tex0(dev);
    if (b0 != g_fontTex) { wd_exit(4); return 0; }''', 1)

    # F) Reset 成功后调用快照（并自增自建计数）
    a7 = ('HKLOG("[RESET] 已安排重建：CreateTexture 抓到新字体纹理后【验证大纹仍绑定】'
          '即立即重打补丁\\r\\n");')
    assert a7 in s, 'reset hook anchor not found'
    s = s.replace(a7, a7 + '''
#if DIAG_T2
        g_dResetN++;
        diag_reset_snapshot(self);
#endif''', 1)

    # G) Present 里定期打印恢复态
    a8 = ('        if (g_postResetNeedRebuild || g_rearmPending) {\n'
          '            if ((g_frames % 2) == 0) watchdog_swap(self);')
    if a8 in s:
        s = s.replace(a8, a8 + '''
#if DIAG_T2
            diag_rearm_tick(self);
#endif''', 1)

    io.open(SRC, 'w', encoding='utf-8', errors='surrogateescape').write(s)
    print('c90 诊断代码已注入')


if __name__ == '__main__':
    patch()
