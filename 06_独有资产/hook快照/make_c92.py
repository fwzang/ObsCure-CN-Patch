# -*- coding: utf-8 -*-
"""c92 · B1+ 修复：路径B 收紧（新增 2 个 && 条件，{} 内零改动）"""
import io
SRC = r'E:/SteamLibrary/steamapps/common/Obscure/hzp_tools/hook/version_proxy.c'
OLD = '''                if (tex != g_fontTex && ut_dst_hit(tex)) {'''
NEW = '''                /* ══ c92 修复（B1+）：路径B 收紧 —— 与路径A 的组哈希判据一致 ══
                 * 问题：路径B 只用 ut_dst_hit(tex)，组1/组2 的 512x128 都满足
                 *       -> 游戏绑定组2纹理时 g_fontTex 被污染 -> 全部乱码
                 * 依据：实测 38/38 次 Reset 后都是「组1的 CreateTexture 先」，
                 *       且 Reset 后 51 行内的 SetTex 事件发生时
                 *       postRebuild 已被清 0、g_fontTex 已为组1（非NULL）
                 *       -> 本修复的两个条件都不成立 -> 会挡住误采纳
                 * 注：Hook_CreateTexture 的同类判据本轮不动。 */
                if (tex != g_fontTex && ut_dst_hit(tex)
                    && g_postResetNeedRebuild
                    && g_fontTex == NULL) {'''
s = io.open(SRC, encoding='utf-8', errors='surrogateescape').read()
assert OLD in s, '锚点未找到'
assert s.count(OLD) == 1, '锚点不唯一'
s = s.replace(OLD, NEW, 1)
io.open(SRC,'w',encoding='utf-8',errors='surrogateescape').write(s)
print('c92 B1+ 补丁已应用（新增 2 个条件）')
