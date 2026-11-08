# -*- coding: utf-8 -*-
"""c91 · 缺陷 A 修复：Hook_Present 外层条件补上 g_rearmPending
======================================================================
改动（共 2 处，均为最小改动）：

【改动 1 · 修复（核心）】Hook_Present 外层条件漏用 g_rearmPending
  现状：
      if ((g_postResetNeedRebuild || g_fontTexBound) && (g_frames % 2) == 0) {
          if (g_postResetNeedRebuild || g_rearmPending) {      // 内层才有
  问题：CreateTexture 抓到新字体纹理后会把 g_postResetNeedRebuild 清 0；
        若游戏此后不调用 SetTexture，g_fontTexBound 保持 0
        → 外层 (0 || 0) = FALSE → 看门狗块被整体跳过 → 永不恢复
  改为：
      if ((g_postResetNeedRebuild || g_fontTexBound || g_rearmPending) && ...) {

【改动 2 · 诊断（因 A 同时是最精准的诊断）】看门狗在 rearm 窗口内因 b0 退出时打一条日志
  目的：确认"看门狗确实运行了、但卡在 b0 判据"（即缺陷 B 是否存在）
  限流：前 6 次 + 之后每 600 次一条，避免刷屏

未改动：watchdog_swap 内部逻辑、b0 判据、get_bound_tex0、EXP_NO_QUERY、
        g_rearmPending 的语义、任何 Reset 流程 —— 一律保持原样。
"""
import io

SRC = r'E:\SteamLibrary\steamapps\common\Obscure\hzp_tools\hook\version_proxy.c'

# ---------- 改动 1：外层条件 ----------
OLD_COND = '''    if ((g_postResetNeedRebuild || g_fontTexBound) && (g_frames % 2) == 0) {
        g_ckpt = "P:watch";'''
NEW_COND = '''    /* ★ c91 修复（缺陷 A）：外层条件补上 g_rearmPending。
     *   原条件漏用它 -> CreateTexture 清掉 g_postResetNeedRebuild 后，
     *   若游戏不调 SetTexture（g_fontTexBound 保持 0），外层 (0||0)=FALSE，
     *   整个看门狗块被跳过 -> Reset 后永不恢复（实测：watchdog 进入=0）。 */
    if ((g_postResetNeedRebuild || g_fontTexBound || g_rearmPending) && (g_frames % 2) == 0) {
        g_ckpt = "P:watch";'''

# ---------- 改动 2：看门狗 b0 退出日志（限流） ----------
OLD_B0 = '''    void* b0 = get_bound_tex0(dev);
    if (b0 != g_fontTex) return 0;'''
NEW_B0 = '''    void* b0 = get_bound_tex0(dev);
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
            lstrcatA(wb, "\\r\\n");
            HKLOG(wb);
        }
        return 0;
    }'''


def patch():
    s = io.open(SRC, encoding='utf-8', errors='surrogateescape').read()
    n = 0

    assert OLD_COND in s, '改动1锚点未找到'
    s = s.replace(OLD_COND, NEW_COND, 1); n += 1

    assert OLD_B0 in s, '改动2锚点未找到'
    s = s.replace(OLD_B0, NEW_B0, 1); n += 1

    io.open(SRC, 'w', encoding='utf-8', errors='surrogateescape').write(s)
    print('c91 补丁已应用：%d 处改动' % n)
    print('  改动1: Hook_Present 外层条件 += g_rearmPending（修复）')
    print('  改动2: watchdog b0 退出加限流日志（诊断）')


if __name__ == '__main__':
    patch()
