# build_clearflash4.py — 「Clear 遮罩」v4（启动期不 Clear，切屏期正常）
#
# v3 实测：启动加载期（前 6 次 Reset）游戏不画 UI 文字 → "绑字体"信号不来 → Clear 每次走满
#   2s 兜底 → 启动黑被显著延长（"初次打开延迟"）。v2 时代无此现象（AB8 同帧自绑，窗口极短）。
# v4 修复（用户方案 A 精确化，沿承 v3 的"绑字体"停黑信号）：
#   新标志 D_EVER（"曾绑字体"，跨 Reset 持久不清）：
#     D_EVER==0（启动期，从未画过文字）→ Present 直接放行（不 Clear）
#     首次"绑字体"（D_BOUND=1 写入时）→ D_EVER=1 → 此后（切屏场景）Clear 正常
#
# 三个 cave：
#   CAVE_R @0x37862（Reset 钩子 0x2100）：D_WIN 惰性初始化(qpf/1000*2000)；D_RST=QPC_lo+1；D_BOUND=0
#   CAVE_P @随后（Present 钩子 0x26A0）：if D_RST!=0 && D_BOUND!=1 && (QPC_lo-D_RST)<2s → Clear
#   CAVE_ST @随后（SetTexture 钩子 0x4320，新增检测，对行为零改动）：
#       if (Stage==0 && AB8==1 && g_fontTex[0x10041A70]!=0 && tex==g_fontTex) → D_BOUND=1
#   标志 D_BOUND = byte[0x10048B6C]（BSS，文件外，加载器清零）
#
# 保留：D3DCLEAR_TARGET=1、vt[43]=0xAC、QPC/QPF（0x1003F9A4/A8）；2s 兜底超时。
# 输出：version_c93e_d2delay_clearflash3.dll（SRC=analysis_copy.dll=d2delay 4fefa2f5 不动）

import hashlib
import os
import struct
import sys

SRC = r"D:\SteamLibrary\steamapps\common\Obscure\hzp_tools\decomp_work\analysis_copy.dll"
DST = r"D:\SteamLibrary\steamapps\common\Obscure\hzp_tools\decomp_work\version_c93e_d2delay_clearflash4.dll"
SRC_MD5 = "4fefa2f50ee833329a7bc34f52f0b3fd"

TEXT = 0x10000C00
CAVE_R = 0x37862
ENTRY_R = 0x2100
ENTRY_P = 0x26A0
ENTRY_ST = 0x4320
ENTRY_5 = bytes.fromhex("55 89 E5 53 57")

D_NOW = 0x10048B58
D_RST = 0x10048B60
D_WIN = 0x10048B64
D_QPF8 = 0x10048B68
D_BOUND = 0x10048B6C     # "本轮 Reset 后游戏已绑字体"（Reset 清 0）
D_EVER = 0x10048B6D      # ★v4："曾绑字体"（跨 Reset 持久，永不清 0）

D_AB8 = 0x10048AB8
D_1A70 = 0x10041A70

IAT_QPC = 0x1003F9A4
IAT_QPF = 0x1003F9A8
CLEAR_VT_OFF = 43 * 4


def va(foff):
    return foff + TEXT


def rel32(from_va, to_va):
    d = (to_va - from_va) & 0xFFFFFFFF
    if d >= 0x80000000:
        d -= 0x100000000
    return struct.pack("<i", d)


def disp32(target_va, base_va):
    return struct.pack("<I", (target_va - base_va) & 0xFFFFFFFF)


def sim_behavior():
    """Clear 决策 + D_BOUND 状态机仿真"""
    WIN = 20_000_000
    def decide(now, rst, ever, bound, win=WIN):
        if rst == 0:
            return 'normal'
        if ever == 0:            # ★v4 启动期（从未绑字体）→ 不 Clear
            return 'normal'
        if bound == 1:
            return 'normal'
        diff = (now - rst) & 0xFFFFFFFF
        return 'clear' if diff < win else 'normal'
    t0 = 123_456_789
    cases = [
        (t0, 0, 1, 0, 'normal', '从未 Reset → 不清'),
        (t0 + 1, t0 + 1, 0, 0, 'normal', '★启动期 D_EVER=0 → 不 Clear'),
        (t0 + 1, t0 + 1, 1, 0, 'clear', '运行期 Reset 后未绑 → 黑'),
        (t0 + 10_000_000, t0 + 1, 1, 0, 'clear', '1s 未绑 → 黑'),
        (t0 + 20_000_000 + 1, t0 + 1, 1, 0, 'normal', '2s 超时兜底 → 放行'),
        (t0 + 1, t0 + 1, 1, 1, 'normal', '已绑字体 → 立即停黑'),
        (0x00000100, 0xFFFFFF00, 1, 0, 'clear', '回绕 diff=0x200 → 黑'),
    ]
    for now, rst, ev_, b, exp, desc in cases:
        got = decide(now, rst, ev_, b)
        assert got == exp, "行为仿真失败: %s" % desc
        print("  [ok] %-34s rst=%d ever=%d bound=%d -> %s" % (desc, rst, ev_, b, got))

    # D_BOUND 状态机（SetTexture 事件序列）
    def step(ev, bound, ab8, g_font):
        stage, tex_is_font = ev
        if stage == 0 and ab8 == 1 and g_font and tex_is_font:
            return 1
        return bound
    bound, ab8, g_font = 0, 0, 0
    ever = 0
    # 1. 启动期：Reset 前（g_fontTex=0 → 不置位；D_EVER 保持 0）
    assert step((0, True), bound, ab8, g_font) == 0
    assert ever == 0
    print("  [ok] 启动期 D_EVER=0（Clear 禁用）")
    # 2. Reset：bound=0；恢复序列绑字体（AB8=0）→ 不应置位
    bound = 0
    bound = step((0, True), bound, ab8=0, g_font=1)
    assert bound == 0, "恢复序列的绑定（AB8=0）不应置位"
    print("  [ok] 恢复序列绑定（AB8=0）→ 不置位（闪帧源排除）")
    # 3. AB8=1 后第一次正常绘制绑字体 → 置位（D_BOUND=1 同时 D_EVER=1）
    bound = step((0, True), bound, ab8=1, g_font=1)
    assert bound == 1, "AB8=1 后绑字体应置位"
    ever = 1 if bound == 1 else ever
    assert ever == 1, "D_EVER 应在首次绑字体时置 1"
    print("  [ok] AB8=1 后绑字体 → 置位（D_BOUND=1 且 D_EVER=1）")
    # 4. 非 stage0 / 非字体纹理 → 不变
    b2 = step((1, True), 0, 1, 1)
    assert b2 == 0
    b2 = step((0, False), 0, 1, 1)
    assert b2 == 0
    b2 = step((0, True), 0, 1, 0)  # g_fontTex==0
    assert b2 == 0
    print("  [ok] stage!=0 / 非字体 / g_fontTex=0 → 不置位")
    print("[ok] 行为 + D_BOUND 状态机仿真通过\n")


def sim_stack(code, base_va, label, clear_off, need_branches=1):
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
                esp += 4
            elif 'eax' in o:
                assert '0x%x' % clear_off in o
                esp += 28
            else:
                raise AssertionError("未知 call: %s" % o)
        elif m in ('hlt', 'int3', 'int', 'ret', 'jmp'):
            raise AssertionError("[%s] 意外控制流: 0x%X %s" % (label, ins.address, m))
        elif m in ('jz', 'jae', 'jne', 'je', 'jb', 'jbe', 'ja'):
            assert esp == 0, "[%s] 分支点 esp!=0: 0x%X" % (label, ins.address)
            checked += 1
        elif m in ('mov', 'lea', 'sub', 'add', 'cmp', 'test', 'xor', 'div', 'mul', 'imul', 'and', 'or'):
            pass
        else:
            raise AssertionError("未建模指令: %s %s" % (m, o))
    assert esp == 0, "[%s] 出口 esp!=0 (%d)" % (label, esp)
    assert checked >= need_branches, "[%s] 分支点不足" % label
    print("  [ok] %s 栈平衡 OK（%d 分支点）" % (label, checked))


def build_cave_r():
    cave = bytearray()
    base = va(CAVE_R) + 5
    cave += b"\xE8\x00\x00\x00\x00"
    cave += b"\x5A"
    cave += b"\x83\xBA" + disp32(D_WIN, base) + b"\x00"
    j_ini = len(cave); cave += b"\x75\x00"
    cave += b"\x52"
    cave += b"\x8D\x8A" + disp32(D_QPF8, base)
    cave += b"\x51"
    cave += b"\xFF\x92" + disp32(IAT_QPF, base)
    cave += b"\x5A"
    cave += b"\x8B\x82" + disp32(D_QPF8, base)
    cave += b"\x52"
    cave += b"\x31\xD2"
    cave += b"\xB9\xE8\x03\x00\x00"
    cave += b"\xF7\xF1"
    cave += b"\xB9\xD0\x07\x00\x00"
    cave += b"\xF7\xE1"
    cave += b"\x5A"
    cave += b"\x89\x82" + disp32(D_WIN, base)
    ini = len(cave)
    cave[j_ini + 1] = (ini - (j_ini + 2)) & 0xFF
    assert 0 <= (ini - (j_ini + 2)) < 128
    # D_RST = QPC_lo + 1
    cave += b"\x52"
    cave += b"\x8D\x8A" + disp32(D_NOW, base)
    cave += b"\x51"
    cave += b"\xFF\x92" + disp32(IAT_QPC, base)
    cave += b"\x5A"
    cave += b"\x8B\x82" + disp32(D_NOW, base)
    cave += b"\x83\xC0\x01"
    cave += b"\x89\x82" + disp32(D_RST, base)
    # ★ D_BOUND = 0（新）
    cave += b"\xC6\x82" + disp32(D_BOUND, base) + b"\x00"
    cave += ENTRY_5
    cave += b"\xE9" + rel32(va(CAVE_R) + len(cave) + 5, va(ENTRY_R) + 5)
    return bytes(cave)


def build_cave_p(cave_p_off):
    cave = bytearray()
    base = va(cave_p_off) + 5
    cave += b"\xE8\x00\x00\x00\x00"
    cave += b"\x5A"
    cave += b"\x8B\x82" + disp32(D_RST, base)
    cave += b"\x85\xC0"
    j_z = len(cave); cave += b"\x74\x00"
    # ★ D_EVER==0（启动期）→ 不 Clear
    cave += b"\x80\xBA" + disp32(D_EVER, base) + b"\x00"
    j_e = len(cave); cave += b"\x74\x00"
    # ★ D_BOUND==1 → 停（已绑字体）
    cave += b"\x80\xBA" + disp32(D_BOUND, base) + b"\x01"
    j_b = len(cave); cave += b"\x74\x00"
    cave += b"\x52"
    cave += b"\x8D\x8A" + disp32(D_NOW, base)
    cave += b"\x51"
    cave += b"\xFF\x92" + disp32(IAT_QPC, base)
    cave += b"\x5A"
    cave += b"\x8B\x82" + disp32(D_NOW, base)
    cave += b"\x2B\x82" + disp32(D_RST, base)
    cave += b"\x3B\x82" + disp32(D_WIN, base)
    j_ae = len(cave); cave += b"\x73\x00"
    cave += b"\x6A\x00"
    cave += b"\x68\x00\x00\x80\x3F"
    cave += b"\x68\x00\x00\x00\xFF"
    cave += b"\x68\x01\x00\x00\x00"
    cave += b"\x6A\x00"
    cave += b"\x6A\x00"
    cave += b"\xFF\x74\x24\x1C"
    cave += b"\x8B\x04\x24"
    cave += b"\x8B\x00"
    cave += b"\xFF\x90\xAC\x00\x00\x00"
    fwd = len(cave)
    cave[j_z + 1] = (fwd - (j_z + 2)) & 0xFF
    cave[j_e + 1] = (fwd - (j_e + 2)) & 0xFF
    cave[j_b + 1] = (fwd - (j_b + 2)) & 0xFF
    cave[j_ae + 1] = (fwd - (j_ae + 2)) & 0xFF
    for jp in (j_z, j_e, j_b, j_ae):
        assert 0 <= (fwd - (jp + 2)) < 128
    cave += ENTRY_5
    cave += b"\xE9" + rel32(va(cave_p_off) + len(cave) + 5, va(ENTRY_P) + 5)
    return bytes(cave)


def build_cave_st(cave_st_off):
    """SetTexture 检测 cave（对行为零改动，仅置标志）"""
    cave = bytearray()
    base = va(cave_st_off) + 5
    cave += b"\xE8\x00\x00\x00\x00"                       # call $+5
    cave += b"\x5A"                                        # pop edx
    cave += b"\x83\x7C\x24\x08\x00"                        # cmp dword [esp+8], 0   (Stage)
    j1 = len(cave); cave += b"\x75\x00"                    # jne .skip
    cave += b"\x80\xBA" + disp32(D_AB8, base) + b"\x01"    # cmp byte [AB8], 1
    j2 = len(cave); cave += b"\x75\x00"                    # jne .skip
    cave += b"\x8B\x82" + disp32(D_1A70, base)             # mov eax,[g_fontTex]
    cave += b"\x85\xC0"                                    # test eax,eax
    j3 = len(cave); cave += b"\x74\x00"                    # jz .skip
    cave += b"\x39\x44\x24\x0C"                            # cmp [esp+0xC], eax   (tex)
    j4 = len(cave); cave += b"\x75\x00"                    # jne .skip
    cave += b"\xC6\x82" + disp32(D_BOUND, base) + b"\x01"  # mov byte [D_BOUND], 1
    cave += b"\xC6\x82" + disp32(D_EVER, base) + b"\x01"   # mov byte [D_EVER], 1 ★v4（持久）
    skip = len(cave)                                       # .skip
    for jp in (j1, j2, j3, j4):
        cave[jp + 1] = (skip - (jp + 2)) & 0xFF
        assert 0 <= (skip - (jp + 2)) < 128
    cave += ENTRY_5
    cave += b"\xE9" + rel32(va(cave_st_off) + len(cave) + 5, va(ENTRY_ST) + 5)
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
    print("=== 构建 clearflash4（启动期不 Clear）===\n")
    raw = open(SRC, "rb").read()
    md5 = hashlib.md5(raw).hexdigest()
    print("[pre] SRC md5 = %s" % md5)
    assert md5 == SRC_MD5, "SRC 非 d2delay 基座！"
    print("[ok] SRC == d2delay 基座\n")

    imports = parse_imports(raw)
    by_name = {f: v for _, f, v in imports}
    assert by_name.get("QueryPerformanceCounter") == IAT_QPC
    assert by_name.get("QueryPerformanceFrequency") == IAT_QPF
    print("[ok] 导入表断言 QPC/QPF\n")

    data = bytearray(raw)
    for e_off, nm in ((ENTRY_R, "Reset"), (ENTRY_P, "Present"), (ENTRY_ST, "SetTexture")):
        assert bytes(data[e_off:e_off + 5]) == ENTRY_5, "%s 入口字节不符" % nm
    print("[ok] 三个钩子入口前 5 字节 = %s" % ENTRY_5.hex(' '))
    assert set(data[CAVE_R:CAVE_R + 288]) == {0xCC}, "cave 区非全 CC"
    print("[ok] cave 区前 288B 全 CC")

    for s, nm in ((D_NOW, 'D_NOW'), (D_RST, 'D_RST'), (D_WIN, 'D_WIN'), (D_QPF8, 'D_QPF8'),
                  (D_BOUND, 'D_BOUND'), (D_EVER, 'D_EVER'), (D_AB8, 'D_AB8'), (D_1A70, 'D_1A70')):
        assert 0x10041000 <= s < 0x10041000 + 0x355E90, "%s 不在 .data 范围" % nm
        fo = s - 0x10000000 - 0x2400
        if 0x3EC00 <= fo < 0x3EE00:
            assert struct.unpack_from('<I', data, fo)[0] == 0, "%s 初值非零" % nm
    print("[ok] 目标槽范围/初值检查通过（BSS 加载器清零）\n")

    print("=== 逻辑仿真 ===")
    sim_behavior()

    cave_r = build_cave_r()
    cave_p_off = CAVE_R + len(cave_r)
    cave_p = build_cave_p(cave_p_off)
    cave_st_off = cave_p_off + len(cave_p)
    cave_st = build_cave_st(cave_st_off)
    total = len(cave_r) + len(cave_p) + len(cave_st)
    print("=== cave 布局 ===")
    print("  CAVE_R  @0x%X len %dB" % (CAVE_R, len(cave_r)))
    print("  CAVE_P  @0x%X len %dB" % (cave_p_off, len(cave_p)))
    print("  CAVE_ST @0x%X len %dB" % (cave_st_off, len(cave_st)))
    print("  合计 %dB（上限 414B）" % total)
    assert total <= 414

    print("\n=== capstone ===")
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    import re as _re
    md = Cs(CS_ARCH_X86, CS_MODE_32)

    def dis_check(code, coff, label, expect_addrs, expect_jmp, is_p=None):
        base = va(coff) + 5
        dis = []
        for ins in md.disasm(code, va(coff)):
            print("  0x%08X  %-22s %s %s" % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str))
            dis.append((ins.address, ins.mnemonic, ins.op_str))
        text = '\n'.join('0x%08X %s %s' % (a, m, o) for a, m, o in dis)
        eff = set()
        for m in _re.finditer(r'\[edx \+ 0x([0-9a-f]+)\]', text):
            eff.add(base + int(m.group(1), 16))
        for need in expect_addrs:
            assert need in eff, "[%s] 缺地址 0x%08X（eff=%s）" % (label, need, ['0x%08X' % x for x in sorted(eff)])
        assert ('jmp 0x%x' % expect_jmp) in text, "[%s] jmp 目标错" % label
        print("  [ok] %s 断言通过 (eff=%d)" % (label, len(eff)))

    print("--- CAVE_R ---")
    dis_check(cave_r, CAVE_R, 'CAVE_R', (D_WIN, D_QPF8, D_NOW, D_RST, D_BOUND), va(ENTRY_R) + 5)
    sim_stack(cave_r, va(CAVE_R), 'CAVE_R', CLEAR_VT_OFF, 1)
    print("--- CAVE_P ---")
    dis_check(cave_p, cave_p_off, 'CAVE_P', (D_RST, D_EVER, D_BOUND, D_NOW, D_WIN), va(ENTRY_P) + 5)
    sim_stack(cave_p, va(cave_p_off), 'CAVE_P', CLEAR_VT_OFF, 4)
    print("--- CAVE_ST ---")
    dis_check(cave_st, cave_st_off, 'CAVE_ST', (D_AB8, D_1A70, D_BOUND, D_EVER), va(ENTRY_ST) + 5)
    sim_stack(cave_st, va(cave_st_off), 'CAVE_ST', CLEAR_VT_OFF, 3)
    print("[ok] capstone 全部断言通过\n")

    diff_before = bytes(data)
    data[CAVE_R:CAVE_R + total] = cave_r + cave_p + cave_st
    data[ENTRY_R:ENTRY_R + 5] = b"\xE9" + rel32(va(ENTRY_R) + 5, va(CAVE_R))
    data[ENTRY_P:ENTRY_P + 5] = b"\xE9" + rel32(va(ENTRY_P) + 5, va(cave_p_off))
    data[ENTRY_ST:ENTRY_ST + 5] = b"\xE9" + rel32(va(ENTRY_ST) + 5, va(cave_st_off))
    diff = [i for i in range(len(data)) if data[i] != diff_before[i]]
    allowed = set(range(CAVE_R, CAVE_R + total)) | set(range(ENTRY_R, ENTRY_R + 5)) \
        | set(range(ENTRY_P, ENTRY_P + 5)) | set(range(ENTRY_ST, ENTRY_ST + 5))
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
