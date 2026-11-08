# build_clearflash.py — 「Clear 遮罩」v1（真·最后一次）
#
# 目标：Reset 后 300ms 内，在 Present 入口调 device->Clear 把 backbuffer 涂黑，
#       盖住切屏恢复期的条纹闪帧。
#
# 与用户方案的 2 处显式偏离（静态核对产出，已在报告说明）：
#   1) Flags：用户方案写 "push 2 ; Flags = D3DCLEAR_TARGET"；
#      d3d9types.h:31 权威定义 D3DCLEAR_TARGET = 0x00000001（2 是 ZBUFFER，只清深度不清画面）
#      → 本实现用 1（否则功能完全无效）。
#   2) 墙钟：用户方案用 GetTickCount（走 IAT）；PE 导入表权威解析（52 项）确认本 DLL
#      未导入 GetTickCount；已导入 QueryPerformanceCounter (IAT 0x1003F9A4) +
#      QueryPerformanceFrequency (0x1003F9A8)。→ 用 QPC 低 32 位（语义等价，精度更高）。
#
# 结构：
#   CAVE_R @ 0x37862（Reset 钩子入口 0x2100 跳入）：
#     - 惰性初始化：D_WIN==0 时 QPF → D_WIN = qpf*3/10（300ms 的 QPC tick 数）
#     - 记 D_RST = QPC_lo() + 1（+1 使 0 保留给"从未 Reset"哨兵）
#   CAVE_P @ 紧跟（Present 钩子入口 0x26A0 跳入）：
#     - D_RST==0 → 正常（启动期安全，绝不误清）
#     - (QPC_lo - D_RST) < D_WIN → device->Clear(0, NULL, 1, 0xFF000000, 1.0f, 0)
#
# 预期行为：
#   切屏（Reset）后 300ms 内所有 Present 前涂黑；300ms 后恢复。
#   QPF 失败降级：D_WIN=0 → 永不 Clear（功能静默失效、游戏无害）。
#
# 三重校验：① capstone（有效地址集/调用目标/vt 偏移/复刻边界）
#           ② 逻辑仿真（栈平衡 esp 轨迹 + 时间窗状态机 + QPF 数学）
#           ③ 加载测试（cave_probe.exe，LoadLibraryEx DONT_RESOLVE）

import hashlib
import os
import struct
import sys

SRC = r"D:\SteamLibrary\steamapps\common\Obscure\hzp_tools\decomp_work\analysis_copy.dll"
DST = r"D:\SteamLibrary\steamapps\common\Obscure\hzp_tools\decomp_work\version_c93e_d2delay_clearflash.dll"
SRC_MD5 = "4fefa2f50ee833329a7bc34f52f0b3fd"   # d2delay 装机基座

TEXT = 0x10000C00
CAVE_R = 0x37862              # Reset cave 起始（FOFF）
ENTRY_R = 0x2100              # Reset 钩子入口（FOFF）
ENTRY_P = 0x26A0              # Present 钩子入口（FOFF）
ENTRY_5 = bytes.fromhex("55 89 E5 53 57")     # 两入口公共前 5 字节

# BSS 槽（0x10048B00~BFF 零引用区、文件外=运行时 0）
D_NOW = 0x10048B58            # 8B：QPC 输出缓冲（只用低 32 位）
D_RST = 0x10048B60            # 4B：Reset 时刻（QPC_lo + 1）
D_WIN = 0x10048B64            # 4B：窗口 tick 数（qpf*3/10）
D_QPF8 = 0x10048B68           # 8B：QPF 输出缓冲

# IAT 槽（PE 导入表权威解析得到；构建时二次 assert）
IAT_QPC = 0x1003F9A4
IAT_QPF = 0x1003F9A8

CLEAR_VT_OFF = 43 * 4         # 0xAC：IDirect3DDevice9::Clear（vt[43]）
WINDOW_MS = 300


def va(foff):
    return foff + TEXT


def rel32(from_va, to_va):
    """from_va = 跳转指令之后的 VA"""
    d = (to_va - from_va) & 0xFFFFFFFF
    if d >= 0x80000000:
        d -= 0x100000000
    return struct.pack("<i", d)


def disp32(target_va, base_va):
    return struct.pack("<I", (target_va - base_va) & 0xFFFFFFFF)


# ---------------------------------------------------------------- 逻辑仿真②
def sim_time_window():
    """时间窗状态机仿真（unsigned 32 位算术）"""
    def behavior(now, reset, win):
        if reset == 0:
            return 'normal'
        diff = (now - reset) & 0xFFFFFFFF
        return 'clear' if diff < win else 'normal'

    win = 3_000_000   # 10MHz 的 300ms
    t0 = 123_456_789
    cases = [
        # (now, reset, win, 期望, 说明)
        (t0, 0, win, 'normal', '从未 Reset（启动期）→ 恒不清'),
        (t0, t0 + 1, win, 'normal', 'Reset 同 tick δ=0（0.1μs 理论边缘）→ 跳过（安全方向）'),
        (t0 + 1, t0 + 1, win, 'clear', 'Reset 后 δ=1 tick → 清'),
        (t0 + 100_000, t0 + 1, win, 'clear', 'Reset 后 10ms → 清'),
        (t0 + win, t0 + 1, win, 'clear', 'diff=win-1（精确边界内）→ 清'),
        (t0 + win + 1, t0 + 1, win, 'normal', 'diff=win（精确边界外）→ 放行'),
        (t0 + 3_000_002, t0 + 1, win, 'normal', 'Reset 后 300.01ms → 放行'),
        (t0 + 10_000_000, t0 + 1, win, 'normal', 'Reset 后 1s → 放行'),
        (0x00000100, 0xFFFFFF00, win, 'clear', '2^32 回绕（diff=0x200）→ 清'),
        (t0, t0 + 1, 0, 'normal', 'QPF 失败 win=0 → 永不清（降级）'),
        (t0, 0xFFFFFFFF, 1, 'normal', 'now=reset 边界 → normal'),
    ]
    for now, reset, w, exp, desc in cases:
        got = behavior(now, reset, w)
        assert got == exp, "时间窗仿真失败: %s (now=0x%X rst=0x%X win=%d got=%s)" % (desc, now, reset, w, got)
        print("  [ok] %-38s now=0x%08X rst=0x%08X win=%-9d -> %s" % (desc, now, reset, w, got))
    # div 仿真（qpf*3/10）
    for qpf, exp_win in ((10_000_000, 3_000_000), (3_579_545, 1_073_863), (0, 0)):
        eax = (qpf * 3) & 0xFFFFFFFF
        win_calc = eax // 10
        assert win_calc == exp_win, "QPF 换算失败: %d -> %d" % (qpf, win_calc)
        print("  [ok] QPF 换算 qpf=%-10d win=%-9d" % (qpf, win_calc))
    print("[ok] 时间窗 + QPF 数学仿真全部通过\n")


def sim_stack(code, base_va, label, clear_off):
    """栈平衡仿真：逐指令追踪 esp（相对值），断言分支跳转点与复刻段入口 esp==0。
    到达复刻段（末尾 10B：复刻5B+jmp5B）即停 —— 复刻段是原函数指令，由原函数自己平衡。"""
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    insns = list(md.disasm(bytes(code), base_va))
    replica_off = len(code) - 10
    esp = 0
    checked = 0
    for ins in insns:
        if (ins.address - base_va) >= replica_off:
            assert esp == 0, "[%s] 复刻段入口 esp!=0 (%d) — 栈不平衡！" % (label, esp)
            break
        m, o = ins.mnemonic, ins.op_str
        if m == 'push':
            esp -= 4
        elif m == 'pop':
            esp += 4
        elif m == 'call':
            if o.startswith('0x'):      # call $+5（相对，内部）
                esp -= 4                 # 压返回地址；紧随的 pop 平衡
            elif 'edx' in o:             # call [edx+..] → QPC/QPF（stdcall 1 参清栈）
                esp += 4                 # 净效果：call 压-4 + callee 清参+4 + 弹返+4 = +4
            elif 'eax' in o:             # call [eax+0xAC] → Clear（stdcall 7 槽 ret 0x1C）
                assert '0x%x' % clear_off in o, "Clear 调用偏移异常: %s" % o
                esp += 28                # 净效果：-4 + 28(清7槽) + 4 = +28
            else:
                raise AssertionError("未知 call: %s" % o)
        elif m in ('jmp', 'ret', 'hlt', 'int3', 'int'):
            # cave 中部（复刻段之前）绝不应出现无条件跳转/返回 —— 出现即编码错误
            raise AssertionError("[%s] 意外控制流指令: 0x%X %s %s" % (label, ins.address, m, o))
        elif m in ('jz', 'jae', 'jne', 'je', 'jb', 'jbe', 'ja'):
            assert esp == 0, "[%s] 分支点 esp!=0: 0x%X %s %s (esp=%d)" % (label, ins.address, m, o, esp)
            checked += 1
        elif m in ('mov', 'lea', 'sub', 'add', 'cmp', 'test', 'xor', 'div', 'imul', 'and', 'or'):
            pass
        else:
            raise AssertionError("未建模指令: %s %s" % (m, o))
    assert esp == 0, "[%s] 出口 esp!=0 (esp=%d) — 栈不平衡！" % (label, esp)
    assert checked >= 1, "[%s] 分支点未覆盖" % label
    print("  [ok] %s 栈平衡: 入口 esp=0 → 出口 esp=0（%d 个分支点校验）" % (label, checked))


# ---------------------------------------------------------------- 构建
def build_cave_r():
    cave = bytearray()
    base = va(CAVE_R) + 5          # call $+5; pop edx 后的 edx

    cave += b"\xE8\x00\x00\x00\x00"                       # 00 call $+5
    cave += b"\x5A"                                        # 05 pop edx
    cave += b"\x83\xBA" + disp32(D_WIN, base) + b"\x00"    # 06 cmp dword [edx+D_WIN], 0
    j_ini = len(cave); cave += b"\x75\x00"                 # 0D jne .inited
    # --- init：QPF → D_WIN = qpf*3/10 ---
    cave += b"\x52"                                        # 0F push edx
    cave += b"\x8D\x8A" + disp32(D_QPF8, base)             # 10 lea ecx,[edx+D_QPF8]
    cave += b"\x51"                                        # 16 push ecx
    cave += b"\xFF\x92" + disp32(IAT_QPF, base)            # 17 call [edx+IAT_QPF]
    cave += b"\x5A"                                        # 1D pop edx
    cave += b"\x8B\x82" + disp32(D_QPF8, base)             # 1E mov eax,[edx+D_QPF8]
    cave += b"\x8D\x04\x40"                                # 24 lea eax,[eax+eax*2]  (×3)
    cave += b"\x52"                                        # 27 push edx
    cave += b"\x31\xD2"                                    # 28 xor edx,edx
    cave += b"\xB9\x0A\x00\x00\x00"                        # 2A mov ecx,10
    cave += b"\xF7\xF1"                                    # 2F div ecx            (eax = ×3/10)
    cave += b"\x5A"                                        # 31 pop edx
    cave += b"\x89\x82" + disp32(D_WIN, base)              # 32 mov [edx+D_WIN], eax
    ini = len(cave)                                        # 38 .inited
    cave[j_ini + 1] = (ini - (j_ini + 2)) & 0xFF
    assert 0 <= (ini - (j_ini + 2)) < 128
    # --- 记 Reset 时刻：D_RST = QPC_lo + 1 ---
    cave += b"\x52"                                        # 38 push edx
    cave += b"\x8D\x8A" + disp32(D_NOW, base)              # 39 lea ecx,[edx+D_NOW]
    cave += b"\x51"                                        # 3F push ecx
    cave += b"\xFF\x92" + disp32(IAT_QPC, base)            # 40 call [edx+IAT_QPC]
    cave += b"\x5A"                                        # 46 pop edx
    cave += b"\x8B\x82" + disp32(D_NOW, base)              # 47 mov eax,[edx+D_NOW]
    cave += b"\x83\xC0\x01"                                # 4D add eax,1
    cave += b"\x89\x82" + disp32(D_RST, base)              # 50 mov [edx+D_RST], eax
    # --- 复刻 + 跳回 ---
    cave += ENTRY_5                                        # 56
    cave += b"\xE9" + rel32(va(CAVE_R) + len(cave) + 5, va(ENTRY_R) + 5)   # 5B jmp
    return bytes(cave)


def build_cave_p(cave_p_off):
    cave = bytearray()
    base = va(cave_p_off) + 5

    cave += b"\xE8\x00\x00\x00\x00"                        # 00 call $+5
    cave += b"\x5A"                                        # 05 pop edx
    cave += b"\x8B\x82" + disp32(D_RST, base)              # 06 mov eax,[edx+D_RST]
    cave += b"\x85\xC0"                                    # 0C test eax,eax
    j_z = len(cave); cave += b"\x74\x00"                   # 0E jz .fwd
    # --- QPC → 时间窗判断 ---
    cave += b"\x52"                                        # 10 push edx
    cave += b"\x8D\x8A" + disp32(D_NOW, base)              # 11 lea ecx,[edx+D_NOW]
    cave += b"\x51"                                        # 17 push ecx
    cave += b"\xFF\x92" + disp32(IAT_QPC, base)            # 18 call [edx+IAT_QPC]
    cave += b"\x5A"                                        # 1E pop edx
    cave += b"\x8B\x82" + disp32(D_NOW, base)              # 1F mov eax,[edx+D_NOW]
    cave += b"\x2B\x82" + disp32(D_RST, base)              # 25 sub eax,[edx+D_RST]
    cave += b"\x3B\x82" + disp32(D_WIN, base)              # 2B cmp eax,[edx+D_WIN]
    j_ae = len(cave); cave += b"\x73\x00"                  # 31 jae .fwd
    # --- Clear(0, NULL, D3DCLEAR_TARGET, 0xFF000000, 1.0f, 0) ---
    cave += b"\x6A\x00"                                    # 33 push 0            (Stencil)
    cave += b"\x68\x00\x00\x80\x3F"                        # 35 push 0x3F800000   (Z=1.0f)
    cave += b"\x68\x00\x00\x00\xFF"                        # 3A push 0xFF000000   (Color=黑)
    cave += b"\x68\x01\x00\x00\x00"                        # 3F push 1            (Flags=D3DCLEAR_TARGET)
    cave += b"\x6A\x00"                                    # 44 push 0            (pRects=NULL)
    cave += b"\x6A\x00"                                    # 46 push 0            (Count=0)
    cave += b"\xFF\x74\x24\x1C"                            # 48 push [esp+0x1C]   (this=device)
    cave += b"\x8B\x04\x24"                                # 4C mov eax,[esp]
    cave += b"\x8B\x00"                                    # 4F mov eax,[eax]     (vtbl)
    cave += b"\xFF\x90\xAC\x00\x00\x00"                    # 51 call [eax+0xAC]   (vt[43] Clear) — FF /2=call
    fwd = len(cave)                                        # 57 .fwd
    cave[j_z + 1] = (fwd - (j_z + 2)) & 0xFF
    cave[j_ae + 1] = (fwd - (j_ae + 2)) & 0xFF
    assert 0 <= (fwd - (j_z + 2)) < 128 and 0 <= (fwd - (j_ae + 2)) < 128
    # --- 复刻 + 跳回 ---
    cave += ENTRY_5                                        # 57
    cave += b"\xE9" + rel32(va(cave_p_off) + len(cave) + 5, va(ENTRY_P) + 5)  # 5C jmp
    return bytes(cave)


def parse_imports(data):
    """返回 [(dll, fname, iat_va)] 列表（iat_va 为绝对 VA）"""
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
    print("=== 构建 clearflash（Clear 遮罩 v1）===\n")
    raw = open(SRC, "rb").read()
    md5 = hashlib.md5(raw).hexdigest()
    print("[pre] SRC md5 = %s" % md5)
    assert md5 == SRC_MD5, "SRC 非 d2delay 基座（4fefa2f5）！"
    print("[ok] SRC == d2delay 装机基座\n")

    # ---------- 前置：IAT 权威断言 ----------
    imports = parse_imports(raw)
    by_name = {f: v for _, f, v in imports}
    assert by_name.get("QueryPerformanceCounter") == IAT_QPC, "QPC IAT 槽不符: %s" % by_name.get("QueryPerformanceCounter")
    assert by_name.get("QueryPerformanceFrequency") == IAT_QPF, "QPF IAT 槽不符: %s" % by_name.get("QueryPerformanceFrequency")
    assert "GetTickCount" not in by_name, "意外发现 GetTickCount"
    print("[ok] 导入表断言: QPC@0x%08X QPF@0x%08X（共 %d 项，无 GetTickCount）\n" % (IAT_QPC, IAT_QPF, len(imports)))

    # ---------- 前置：入口字节与 cave 区 ----------
    data = bytearray(raw)
    assert bytes(data[ENTRY_R:ENTRY_R + 5]) == ENTRY_5, "Reset 入口前 5 字节不符"
    assert bytes(data[ENTRY_P:ENTRY_P + 5]) == ENTRY_5, "Present 入口前 5 字节不符"
    print("[ok] 两入口前 5 字节 = %s" % ENTRY_5.hex(' '))

    # cave 区（Reset cave + Present cave + 余量）全 CC 检查
    probe_len = 256
    assert set(data[CAVE_R:CAVE_R + probe_len]) == {0xCC}, "cave 区非全 CC"
    print("[ok] cave 0x%X 前 %dB 全 CC\n" % (CAVE_R, probe_len))

    # BSS 槽检查（文件外 → 运行时 0）
    for s in (D_NOW, D_RST, D_WIN, D_QPF8, D_NOW + 7):
        fo = s - 0x10000000 - 0x2400
        if fo < len(data):
            v = struct.unpack_from('<I', data, fo)[0]
            assert v == 0, "BSS 槽 0x%08X 非零初值 0x%X" % (s, v)
    print("[ok] BSS 槽 0x%08X~0x%08X 初值全 0（含文件外 BSS 判定）\n" % (D_NOW, D_QPF8 + 7))

    # ---------- 三重校验① 逻辑仿真（先于字节生成：时间窗） ----------
    print("=== 三重校验② — 逻辑仿真 ===")
    sim_time_window()

    # ---------- 字节生成 ----------
    cave_r = build_cave_r()
    cave_p_off = CAVE_R + len(cave_r)
    cave_p = build_cave_p(cave_p_off)
    total = len(cave_r) + len(cave_p)
    print("=== cave 布局 ===")
    print("  CAVE_R FOFF 0x%X len %dB  (VA 0x%08X)" % (CAVE_R, len(cave_r), va(CAVE_R)))
    print("  CAVE_P FOFF 0x%X len %dB  (VA 0x%08X)" % (cave_p_off, len(cave_p), va(cave_p_off)))
    print("  合计 %dB（cave 区 414B）" % total)
    assert total <= 414, "cave 超界"

    # ---------- 三重校验① capstone ----------
    print("\n=== 三重校验① — capstone ===")
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    import re as _re
    md = Cs(CS_ARCH_X86, CS_MODE_32)

    for code, coff, label, is_r in ((cave_r, CAVE_R, 'CAVE_R', True),
                                    (cave_p, cave_p_off, 'CAVE_P', False)):
        base = va(coff) + 5
        print("--- %s 反汇编 ---" % label)
        dis = []
        for ins in md.disasm(code, va(coff)):
            line = "  0x%08X  %-20s %s %s" % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str)
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
            assert ('call dword ptr [edx + 0x%x]' % (IAT_QPF - base)) in text, "QPF 调用缺失"
            assert ('call dword ptr [edx + 0x%x]' % (IAT_QPC - base)) in text, "QPC 调用缺失"
            assert ('jmp 0x%x' % (va(ENTRY_R) + 5)) in text, "跳回目标错"
        else:
            for need in (D_RST, D_NOW, D_WIN):
                assert need in eff, "缺少 0x%08X" % need
            assert ('call dword ptr [edx + 0x%x]' % (IAT_QPC - base)) in text, "QPC 调用缺失"
            assert 'call dword ptr [eax + 0xac]' in text, "vt[43] Clear 调用缺失"
            assert ('jmp 0x%x' % (va(ENTRY_P) + 5)) in text, "跳回目标错"
            assert b"\x68\x01\x00\x00\x00" in bytes(code), "D3DCLEAR_TARGET=1 缺失"
        print("  [ok] %s 地址/调用/jmp 断言通过" % label)

        sim_stack(code, va(coff), label, CLEAR_VT_OFF)
    print("[ok] capstone 全部断言通过\n")

    # ---------- patch ----------
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
