# build_clearflash2.py — 「Clear 遮罩」v2（跟随字体恢复，替换固定 300ms）
#
# v1 实测反馈：闪屏已解决，但黑屏固定 300ms → 字体恢复更晚 → 出现"空白期"
# v2 需求（用户方案）：黑屏跟随"字体恢复"信号；超时兜底 2s。
#
# 【判据设计】（用户方案 "g_fontTex != 0 && g_fontTexBound == 1" 的二进制落地）
#   静态核查（reloc 全扫描 + capstone）确定真实变量语义：
#     - dword [0x10041A70]：纹理对象指针（Reset 时 Release+清 0；0x4FAB 重建后登记）
#       ≡ 用户所说 "g_fontTex != 0"。Reset 后重建（bigTex 512x4096）完成前为 0。
#     - byte [0x10048AB8]："本轮恢复流程完成"标志：
#         写点仅 2 处：Reset 清 0（FOFF 0x31B5）；[D2] 恢复动作置 1（FOFF 0x5968）
#       该恢复动作的前置条件：Reset 后字体重传已发生（base_08）+ bigTex 已重建（base_0C）
#       ≡ 用户所说 "g_fontTexBound == 1"（语义：恢复流程完成）。
#     - （注：1A80 是"SetTexture 替换已发生"标志，会在闪帧开始时就置 1，
#        故不作判据 —— 否则停黑过早会暴露闪帧。）
#
# 【Clear 条件（Present 入口）】
#   D_RST != 0                                  ; 发生过 Reset
#   && elapsed = QPC_lo - D_RST < 2s            ; 未超时（QPF 换算，与帧率无关）
#   && !( byte[0x48AB8]==1 && dword[0x10041A70]!=0 )
#   → device->Clear(0, NULL, D3DCLEAR_TARGET(1), 0xFF000000, 1.0f, 0)
#   （Reset 钩子不变：记 D_RST = QPC_lo + 1；惰性初始化 D_WIN = qpf/1000*2000 = 2s 的 tick 数）
#
# 【风险登记】
#   R1 过早：AB8 置位后文字仍需少数帧绘制 → 允许（≤数帧）
#   R2 过晚：AB8 不置位（Reset 分支 2 场景，AB4=0）→ 2s 超时兜底
#   R3 1A70 语义偏差异常 → 黑屏时长异常（可观察、可回滚）
#
# 【三重校验】capstone（有效地址集/调用目标/vt 偏移/复刻边界）
#            + 逻辑仿真（栈平衡 esp 轨迹 + 字体状态机 + QPF 数学）
#            + 加载测试（cave_probe.exe）

import hashlib
import os
import struct
import sys

SRC = r"D:\SteamLibrary\steamapps\common\Obscure\hzp_tools\decomp_work\analysis_copy.dll"
DST = r"D:\SteamLibrary\steamapps\common\Obscure\hzp_tools\decomp_work\version_c93e_d2delay_clearflash2.dll"
SRC_MD5 = "4fefa2f50ee833329a7bc34f52f0b3fd"   # d2delay 基座（不动）

TEXT = 0x10000C00
CAVE_R = 0x37862              # Reset cave（FOFF）
ENTRY_R = 0x2100
ENTRY_P = 0x26A0
ENTRY_5 = bytes.fromhex("55 89 E5 53 57")

# BSS 槽（文件外=运行时 0）
D_NOW = 0x10048B58            # 8B QPC 输出缓冲
D_RST = 0x10048B60            # 4B Reset 时刻（QPC_lo + 1）
D_WIN = 0x10048B64            # 4B 超时窗口 = qpf/1000*2000（2s）
D_QPF8 = 0x10048B68           # 8B QPF 输出缓冲

# c93e 现存状态变量（只读）
D_AB8 = 0x10048AB8            # byte "本轮恢复完成"
D_P1A70 = 0x10041A70          # dword 纹理对象指针

IAT_QPC = 0x1003F9A4
IAT_QPF = 0x1003F9A8
CLEAR_VT_OFF = 43 * 4         # 0xAC = vt[43] Clear
TIMEOUT_MS = 2000


def va(foff):
    return foff + TEXT


def rel32(from_va, to_va):
    d = (to_va - from_va) & 0xFFFFFFFF
    if d >= 0x80000000:
        d -= 0x100000000
    return struct.pack("<i", d)


def disp32(target_va, base_va):
    return struct.pack("<I", (target_va - base_va) & 0xFFFFFFFF)


def sim_font_state():
    """字体状态机仿真：Clear/放行决策（含超时与回绕）"""
    WIN = 20_000_000          # 2s @10MHz
    def decide(now, rst, ab8, t170, win=WIN):
        if rst == 0:
            return 'normal'
        if ab8 == 1 and t170 != 0:
            return 'normal'
        diff = (now - rst) & 0xFFFFFFFF
        return 'clear' if diff < win else 'normal'
    t0 = 123_456_789
    cases = [
        (t0, 0, 0, 0, 'normal', '从未 Reset → 恒不清'),
        (t0+1, t0+1, 0, 0, 'clear', 'Reset 后初始（AB8=0,1A70=0）→ 黑'),
        (t0+100_000, t0+1, 0, 0, 'clear', 'Reset 后 10ms，未恢复 → 黑'),
        (t0+10_000_000, t0+1, 0, 0, 'clear', 'Reset 后 1s，未恢复 → 黑'),
        (t0+19_999_999+1, t0+1, 0, 0, 'clear', 'diff=win-1 边界内 → 黑'),
        (t0+20_000_000+1, t0+1, 0, 0, 'normal', 'diff=win 超时 → 放行'),
        (t0+1_000_000, t0+1, 0, 0x1234, 'clear', 'AB8=0 但 1A70 非0 → 仍黑'),
        (t0+1_000_000, t0+1, 1, 0, 'clear', 'AB8=1 但 1A70=0 → 仍黑'),
        (t0+1_000_000, t0+1, 1, 0x1234, 'normal', 'AB8=1 且 1A70 非0 → 字体恢复，放行'),
        (0x00000100, 0xFFFFFF00, 0, 0, 'clear', '2^32 回绕 diff=0x200 → 黑'),
        (t0+1, t0+1, 0, 0, 'normal', 'win=0 降级（qpf 异常）', 0),
    ]
    for c in cases:
        if len(c) == 6:
            now, rst, ab8, t170, exp, desc = c; win = WIN
        else:
            now, rst, ab8, t170, exp, desc, win = c
        got = decide(now, rst, ab8, t170, win)
        assert got == exp, "字体状态仿真失败: %s (got=%s)" % (desc, got)
        print("  [ok] %-42s ab8=%d 1A70=0x%X -> %s" % (desc, ab8, t170, got))
    # QPF 数学：win = qpf/1000*2000
    for qpf, exp_win in ((10_000_000, 20_000_000), (3_579_545, 7_158_000), (0, 0), (100_000_000, 200_000_000)):
        e = (qpf // 1000) * 2000
        assert e == exp_win, "QPF 换算失败: %d -> %d" % (qpf, e)
        print("  [ok] QPF 换算 qpf=%-11d win2s=%-11d" % (qpf, e))
    print("[ok] 字体状态 + QPF 数学仿真全部通过\n")


def sim_stack(code, base_va, label, clear_off):
    """栈平衡仿真：到复刻段（末尾 10B）即停；分支点与复刻段入口 esp==0"""
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    insns = list(md.disasm(bytes(code), base_va))
    replica_off = len(code) - 10
    esp = 0
    checked = 0
    for ins in insns:
        if (ins.address - base_va) >= replica_off:
            assert esp == 0, "[%s] 复刻段入口 esp!=0 (%d)" % (label, esp)
            break
        m, o = ins.mnemonic, ins.op_str
        if m == 'push':
            esp -= 4
        elif m == 'pop':
            esp += 4
        elif m == 'call':
            if o.startswith('0x'):
                esp -= 4
            elif 'edx' in o:
                esp += 4       # QPC/QPF：-4 + 清参4 + 弹返4 = +4
            elif 'eax' in o:
                assert '0x%x' % clear_off in o, "Clear 偏移异常: %s" % o
                esp += 28      # Clear 7 槽 ret 0x1C
            else:
                raise AssertionError("未知 call: %s" % o)
        elif m in ('hlt', 'int3', 'int', 'ret', 'jmp'):
            raise AssertionError("[%s] 意外控制流指令: 0x%X %s %s" % (label, ins.address, m, o))
        elif m in ('jz', 'jae', 'jne', 'je', 'jb', 'jbe', 'ja'):
            assert esp == 0, "[%s] 分支点 esp!=0: 0x%X %s %s" % (label, ins.address, m, o)
            checked += 1
        elif m in ('mov', 'lea', 'sub', 'add', 'cmp', 'test', 'xor', 'div', 'mul', 'imul', 'and', 'or'):
            pass
        else:
            raise AssertionError("未建模指令: %s %s" % (m, o))
    assert esp == 0, "[%s] 出口 esp!=0 (%d)" % (label, esp)
    assert checked >= 1, "[%s] 分支点不足" % label
    print("  [ok] %s 栈平衡 OK（%d 个分支点校验）" % (label, checked))


def build_cave_r():
    """Reset cave：惰性初始化 D_WIN=qpf/1000*2000；记 D_RST=QPC_lo+1"""
    cave = bytearray()
    base = va(CAVE_R) + 5
    cave += b"\xE8\x00\x00\x00\x00"                       # 00 call $+5
    cave += b"\x5A"                                        # 05 pop edx
    cave += b"\x83\xBA" + disp32(D_WIN, base) + b"\x00"    # 06 cmp dword [D_WIN],0
    j_ini = len(cave); cave += b"\x75\x00"                 # 0D jne .inited
    # --- init: D_WIN = qpf/1000*2000 ---
    cave += b"\x52"                                        # 0F push edx
    cave += b"\x8D\x8A" + disp32(D_QPF8, base)             # 10 lea ecx,[edx+D_QPF8]
    cave += b"\x51"                                        # 16 push ecx
    cave += b"\xFF\x92" + disp32(IAT_QPF, base)            # 17 call [edx+IAT_QPF]
    cave += b"\x5A"                                        # 1D pop edx
    cave += b"\x8B\x82" + disp32(D_QPF8, base)             # 1E mov eax,[edx+D_QPF8]
    cave += b"\x52"                                        # 24 push edx
    cave += b"\x31\xD2"                                    # 25 xor edx,edx
    cave += b"\xB9\xE8\x03\x00\x00"                        # 27 mov ecx,1000
    cave += b"\xF7\xF1"                                    # 2C div ecx
    cave += b"\xB9\xD0\x07\x00\x00"                        # 2E mov ecx,2000
    cave += b"\xF7\xE1"                                    # 33 mul ecx
    cave += b"\x5A"                                        # 35 pop edx
    cave += b"\x89\x82" + disp32(D_WIN, base)              # 36 mov [edx+D_WIN],eax
    ini = len(cave)                                        # 3C .inited
    cave[j_ini + 1] = (ini - (j_ini + 2)) & 0xFF
    assert 0 <= (ini - (j_ini + 2)) < 128
    # --- D_RST = QPC_lo + 1 ---
    cave += b"\x52"                                        # 3C push edx
    cave += b"\x8D\x8A" + disp32(D_NOW, base)              # 3D lea ecx,[edx+D_NOW]
    cave += b"\x51"                                        # 43 push ecx
    cave += b"\xFF\x92" + disp32(IAT_QPC, base)            # 44 call [edx+IAT_QPC]
    cave += b"\x5A"                                        # 4A pop edx
    cave += b"\x8B\x82" + disp32(D_NOW, base)              # 4B mov eax,[edx+D_NOW]
    cave += b"\x83\xC0\x01"                                # 51 add eax,1
    cave += b"\x89\x82" + disp32(D_RST, base)              # 54 mov [edx+D_RST],eax
    # --- 复刻 + 跳回 ---
    cave += ENTRY_5                                        # 5A
    cave += b"\xE9" + rel32(va(CAVE_R) + len(cave) + 5, va(ENTRY_R) + 5)
    return bytes(cave)


def build_cave_p(cave_p_off):
    """Present cave：字体恢复判据 + 2s 超时 → Clear"""
    cave = bytearray()
    base = va(cave_p_off) + 5
    cave += b"\xE8\x00\x00\x00\x00"                        # 00 call $+5
    cave += b"\x5A"                                        # 05 pop edx
    # 1) D_RST == 0 → normal
    cave += b"\x8B\x82" + disp32(D_RST, base)              # 06 mov eax,[edx+D_RST]
    cave += b"\x85\xC0"                                    # 0C test eax,eax
    j_z = len(cave); cave += b"\x74\x00"                   # 0E jz .fwd
    # 2) 字体恢复: AB8==1 && 1A70!=0 → normal
    cave += b"\x80\xBA" + disp32(D_AB8, base) + b"\x01"    # 10 cmp byte [edx+D_AB8],1
    j_ne = len(cave); cave += b"\x75\x00"                  # 17 jne .do_time
    cave += b"\x83\xBA" + disp32(D_P1A70, base) + b"\x00"  # 19 cmp dword [edx+D_P1A70],0
    j_t = len(cave); cave += b"\x75\x00"                   # 20 jne .fwd   （字体已恢复）
    dt = len(cave)                                         # 22 .do_time
    cave[j_ne + 1] = (dt - (j_ne + 2)) & 0xFF
    # 3) 超时检查
    cave += b"\x52"                                        # 22 push edx
    cave += b"\x8D\x8A" + disp32(D_NOW, base)              # 23 lea ecx,[edx+D_NOW]
    cave += b"\x51"                                        # 29 push ecx
    cave += b"\xFF\x92" + disp32(IAT_QPC, base)            # 2A call [edx+IAT_QPC]
    cave += b"\x5A"                                        # 30 pop edx
    cave += b"\x8B\x82" + disp32(D_NOW, base)              # 31 mov eax,[edx+D_NOW]
    cave += b"\x2B\x82" + disp32(D_RST, base)              # 37 sub eax,[edx+D_RST]
    cave += b"\x3B\x82" + disp32(D_WIN, base)              # 3D cmp eax,[edx+D_WIN]
    j_ae = len(cave); cave += b"\x73\x00"                  # 43 jae .fwd
    # 4) Clear(0, NULL, 1, 0xFF000000, 1.0f, 0)
    cave += b"\x6A\x00"                                    # 45 push 0
    cave += b"\x68\x00\x00\x80\x3F"                        # 47 push 0x3F800000
    cave += b"\x68\x00\x00\x00\xFF"                        # 4C push 0xFF000000
    cave += b"\x68\x01\x00\x00\x00"                        # 51 push 1 (D3DCLEAR_TARGET)
    cave += b"\x6A\x00"                                    # 56 push 0
    cave += b"\x6A\x00"                                    # 58 push 0
    cave += b"\xFF\x74\x24\x1C"                            # 5A push [esp+0x1C]
    cave += b"\x8B\x04\x24"                                # 5E mov eax,[esp]
    cave += b"\x8B\x00"                                    # 61 mov eax,[eax]
    cave += b"\xFF\x90\xAC\x00\x00\x00"                    # 63 call [eax+0xAC]
    fwd = len(cave)                                        # 69 .fwd
    cave[j_z + 1] = (fwd - (j_z + 2)) & 0xFF
    cave[j_t + 1] = (fwd - (j_t + 2)) & 0xFF
    cave[j_ae + 1] = (fwd - (j_ae + 2)) & 0xFF
    for jp in (j_z, j_t, j_ae):
        assert 0 <= (fwd - (jp + 2)) < 128, "rel8 越界"
    # --- 复刻 + 跳回 ---
    cave += ENTRY_5                                        # 69
    cave += b"\xE9" + rel32(va(cave_p_off) + len(cave) + 5, va(ENTRY_P) + 5)
    return bytes(cave)


def parse_imports(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt = pe + 24
    sec_off = opt + 224
    secs = []
    for i in range(nsec):
        o = sec_off + i * 40
        vsize, vaddr, rsize, rawptr = struct.unpack_from('<IIII', data, o + 8)
        secs.append((vaddr, vsize, rawptr, rsize))

    def rva2fo(rva):
        for vaddr, vsize, rawptr, rsize in secs:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return rawptr + (rva - vaddr)
        return None

    imp_rva = struct.unpack_from('<I', data, opt + 96 + 8)[0]
    fo = rva2fo(imp_rva)
    hits = []
    while True:
        oft, ts, fc, name_rva, iat = struct.unpack_from('<IIIII', data, fo)
        if oft == 0 and ts == 0 and fc == 0 and name_rva == 0 and iat == 0:
            break
        dllname = ''
        if name_rva:
            p = rva2fo(name_rva)
            dllname = data[p:data.index(b'\x00', p)].decode()
        ofo = rva2fo(oft if oft else iat)
        idx = 0
        while True:
            e = struct.unpack_from('<I', data, ofo + idx * 4)[0]
            if e == 0:
                break
            if e & 0x80000000:
                hits.append((dllname, 'ORD%d' % (e & 0xFFFF), 0x10000000 + iat + idx * 4))
            else:
                p = rva2fo(e)
                fname = data[p + 2:data.index(b'\x00', p + 2)].decode()
                hits.append((dllname, fname, 0x10000000 + iat + idx * 4))
            idx += 1
        fo += 20
    return hits


def main():
    print("=== 构建 clearflash2（字体恢复跟随版）===\n")
    raw = open(SRC, "rb").read()
    md5 = hashlib.md5(raw).hexdigest()
    print("[pre] SRC md5 = %s" % md5)
    assert md5 == SRC_MD5, "SRC 非 d2delay 基座！"
    print("[ok] SRC == d2delay 基座（4fefa2f5 不动）\n")

    imports = parse_imports(raw)
    by_name = {f: v for _, f, v in imports}
    assert by_name.get("QueryPerformanceCounter") == IAT_QPC
    assert by_name.get("QueryPerformanceFrequency") == IAT_QPF
    assert "GetTickCount" not in by_name
    print("[ok] 导入表断言: QPC@0x%08X QPF@0x%08X\n" % (IAT_QPC, IAT_QPF))

    data = bytearray(raw)
    assert bytes(data[ENTRY_R:ENTRY_R + 5]) == ENTRY_5
    assert bytes(data[ENTRY_P:ENTRY_P + 5]) == ENTRY_5
    assert set(data[CAVE_R:CAVE_R + 256]) == {0xCC}, "cave 非全 CC"
    print("[ok] 入口/CC 区检查通过")

    # 字体状态变量核查（c93e 侧已存在；地址写死前做字节特征校验）
    # D_AB8: Reset 处应有清 0（C6 05 B8 8A 04 10 00），[D2] 处置 1
    pat = bytes([0xC6, 0x05]) + struct.pack('<I', D_AB8) + b"\x01"
    hits = []
    st = 0x400
    while True:
        i = raw.find(pat, st)
        if i < 0 or i > 0x37462: break
        hits.append(i); st = i + 1
    assert hits, "AB8 置 1 指令特征缺失"
    # D_P1A70: Reset 处 Release 后清 0（C7 05 70 1A 04 10 00 00 00 00）
    pat2 = bytes([0xC7, 0x05]) + struct.pack('<I', D_P1A70) + b"\x00\x00\x00\x00"
    assert raw.find(pat2, 0x400) > 0, "1A70 清 0 特征缺失"
    print("[ok] 字体状态变量特征核验: AB8 写点 %d 处、1A70 写点存在" % len(hits))

    for s, nm in ((D_NOW, 'D_NOW'), (D_RST, 'D_RST'), (D_WIN, 'D_WIN'), (D_QPF8, 'D_QPF8'),
                  (D_AB8, 'D_AB8'), (D_P1A70, 'D_P1A70')):
        assert 0x10041000 <= s < 0x10041000 + 0x355E90, "%s 不在 .data 虚拟范围" % nm
        fo = s - 0x10000000 - 0x2400
        if 0x3EC00 <= fo < 0x3EE00:      # .data 已初始化 raw 区：文件初值须为 0
            v = struct.unpack_from('<I', data, fo)[0]
            assert v == 0, "%s 槽 0x%08X 非零初值 0x%X" % (nm, s, v)
        # 其余为 BSS（VirtualSize 覆盖、加载器清零），运行时初值 = 0
    print("[ok] 目标槽范围/初值检查通过（BSS 由加载器清零）\n")

    print("=== 三重校验② — 逻辑仿真 ===")
    sim_font_state()

    cave_r = build_cave_r()
    cave_p_off = CAVE_R + len(cave_r)
    cave_p = build_cave_p(cave_p_off)
    total = len(cave_r) + len(cave_p)
    print("=== cave 布局 ===")
    print("  CAVE_R FOFF 0x%X len %dB" % (CAVE_R, len(cave_r)))
    print("  CAVE_P FOFF 0x%X len %dB" % (cave_p_off, len(cave_p)))
    print("  合计 %dB（上限 414B）" % total)
    assert total <= 414

    print("\n=== 三重校验① — capstone ===")
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    import re as _re
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    for code, coff, label, is_r in ((cave_r, CAVE_R, 'CAVE_R', True),
                                    (cave_p, cave_p_off, 'CAVE_P', False)):
        base = va(coff) + 5
        print("--- %s ---" % label)
        dis = []
        for ins in md.disasm(code, va(coff)):
            line = "  0x%08X  %-22s %s %s" % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str)
            print(line)
            dis.append((ins.address, ins.mnemonic, ins.op_str))
        text = '\n'.join('0x%08X %s %s' % (a, m, o) for a, m, o in dis)
        eff = set()
        for m in _re.finditer(r'\[edx \+ 0x([0-9a-f]+)\]', text):
            eff.add(base + int(m.group(1), 16))
        print("  有效地址集: %s" % ['0x%08X' % x for x in sorted(eff)])
        if is_r:
            for need in (D_WIN, D_QPF8, D_NOW, D_RST):
                assert need in eff, "缺少 0x%08X" % need
            assert ('call dword ptr [edx + 0x%x]' % (IAT_QPF - base)) in text
            assert ('call dword ptr [edx + 0x%x]' % (IAT_QPC - base)) in text
            assert ('jmp 0x%x' % (va(ENTRY_R) + 5)) in text
        else:
            for need in (D_RST, D_AB8, D_P1A70, D_NOW, D_WIN):
                assert need in eff, "缺少 0x%08X" % need
            assert ('call dword ptr [edx + 0x%x]' % (IAT_QPC - base)) in text
            assert 'call dword ptr [eax + 0xac]' in text, "vt[43] Clear 缺失"
            assert ('jmp 0x%x' % (va(ENTRY_P) + 5)) in text
            assert b"\x68\x01\x00\x00\x00" in bytes(code)
        print("  [ok] %s 断言通过" % label)
        sim_stack(code, va(coff), label, CLEAR_VT_OFF)
    print("[ok] capstone 全部断言通过\n")

    # patch
    diff_before = bytes(data)
    data[CAVE_R:CAVE_R + total] = cave_r + cave_p
    data[ENTRY_R:ENTRY_R + 5] = b"\xE9" + rel32(va(ENTRY_R) + 5, va(CAVE_R))
    data[ENTRY_P:ENTRY_P + 5] = b"\xE9" + rel32(va(ENTRY_P) + 5, va(cave_p_off))
    diff = [i for i in range(len(data)) if data[i] != diff_before[i]]
    allowed = set(range(CAVE_R, CAVE_R + total)) | set(range(ENTRY_R, ENTRY_R + 5)) | set(range(ENTRY_P, ENTRY_P + 5))
    bad = [o for o in diff if o not in allowed]
    assert not bad, "越界改动: %s" % ['0x%X' % x for x in bad[:10]]
    print("=== patch ===")
    print("[ok] 改动 %d 字节，全在 cave/入口区" % len(diff))

    if os.path.exists(DST):
        print("目标已存在，拒绝覆盖: %s" % DST)
        return 1
    open(DST, "wb").write(bytes(data))
    print("\n产物: %s" % DST)
    print("md5  = %s" % hashlib.md5(bytes(data)).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
