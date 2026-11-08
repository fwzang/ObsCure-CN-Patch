# build_clearflash8.py — 方案 F-3：Clear 停黑 = "文字绘制稳定"（连续 3 帧绑字体）
#
# 背景：口令法铁证——切屏必触发 Reset（切出/切回成对）；"文字空白一会儿"= 游戏重建期（~2.6s，
#   此间不画字）；v7 的 Clear 被 D_BOUND（Reset 后 2 帧"首次绑字体"）截停 → 只黑 33ms → "没黑"。
# F-3 实现（用户方案）：
#   - CAVE_ST（SetTexture 钩子）：命中"绑字体"（stage0+AB8+tex==g_fontTex）时 **D_LAST = FRAMES**
#   - CAVE_P（Present 入口）：维护"连续绑字体"计数——`delta = FRAMES - D_LAST`，delta==0（本帧绑过）
#     → D_CNT++；否则 D_CNT=0；D_CNT>=3 → **D_STABLE=1** → Clear 停
#   - 保留：D_EVER（启动期不 Clear）、3.5s 超时兜底、D_BOUND/D_EVER 写入（逻辑保留）
#   - Reset（CAVE_R）：清 D_BOUND / D_CNT / D_STABLE（D_LAST 不清——大 delta 自然断）
# 预期：主菜单切屏黑 ~3 帧（50ms）；暂停菜单切屏黑 ~2.6s（重建期）→ 画面+文字一起出现，无空白。
# 其余 = clearflash7 全部（含 D2S 安静窗 30 帧）。
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
DST = r"D:\SteamLibrary\steamapps\common\Obscure\hzp_tools\decomp_work\version_c93e_d2delay_clearflash8.dll"
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
D_EVER = 0x10048B6D      # "曾绑字体"（跨 Reset 持久，永不清 0）
D_LAST = 0x10048B70      # ★v8：最后"绑字体"事件的帧号（CAVE_ST 写）
D_CNT = 0x10048B74       # ★v8：连续"本帧绑过"计数（CAVE_P 维护，Reset 清 0）
D_STABLE = 0x10048B78    # ★v8：文字绘制稳定标志（连续 3 帧 → 1，Reset 清 0）
D_FRAMES = 0x10041898    # c93e 帧计数器（Present 钩子体内递增）

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
    """F-3 行为仿真：连续绑字体计数 → D_STABLE → 停黑"""
    def run(binds, ever=1, win=999):
        cnt, stable, last = 0, False, -999
        res = []
        for f, b in enumerate(binds):
            if b:
                last = f
            if ever == 0:
                res.append('normal')
                continue
            d = f - last
            cnt = cnt + 1 if d == 0 else 0
            if cnt >= 3:
                stable = True
            if stable or f >= win:
                res.append('normal')
            else:
                res.append('clear')
        return res

    r = run([True]*10)
    assert r == ['clear', 'clear'] + ['normal']*8, r
    print("  场景1 主菜单（每帧绑）：黑 2 帧后停 → %s" % r[:5])
    r = run([False]*10 + [True]*10)
    assert r[11] == 'clear' and r[12] == 'normal' and set(r[13:]) == {'normal'}, r
    print("  场景2 暂停重建 10 帧：黑 12 帧（重建+2）后停 → %s" % r[8:16])
    r = run([True, True, False, True, True, True])
    assert r[:5] == ['clear']*5 and r[5] == 'normal', r
    print("  场景3 中断重置计数 → %s" % r)
    r = run([False]*20, win=15)
    assert r[15] == 'normal'
    print("  场景4 永不绑 → 超时(第15帧)放行 → %s" % r[13:18])
    r = run([True]*5, ever=0)
    assert all(x == 'normal' for x in r)
    print("  场景5 启动期（D_EVER=0）→ 全放行 ✓")
    print("[ok] F-3 行为仿真通过\n")

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
        elif m == 'jmp':
            # v8：cave 内部"放行隧道"jmp——不动栈；断言 esp==0 后继续
            assert esp == 0, "[%s] jmp 点 esp!=0: 0x%X" % (label, ins.address)
            checked += 1
        elif m in ('hlt', 'int3', 'int', 'ret'):
            raise AssertionError("[%s] 意外控制流: 0x%X %s" % (label, ins.address, m))
        elif m in ('jz', 'jae', 'jne', 'je', 'jb', 'jbe', 'ja'):
            assert esp == 0, "[%s] 分支点 esp!=0: 0x%X" % (label, ins.address)
            checked += 1
        elif m in ('mov', 'lea', 'sub', 'add', 'cmp', 'test', 'xor', 'div', 'mul', 'imul', 'and', 'or',
                   'sete', 'setz', 'setne', 'setnz', 'movzx', 'neg', 'not'):
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
    cave += b"\xB9\xAC\x0D\x00\x00"    # mov ecx, 3500 ★v7（原 2000=B9 D0 07 00 00）→ 超时 3.5s
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
    # D_BOUND=0 / D_CNT=0 / D_STABLE=0（v8；D_LAST 不清——大 delta 自然断）
    cave += b"\xC6\x82" + disp32(D_BOUND, base) + b"\x00"
    cave += b"\xC7\x82" + disp32(D_CNT, base) + b"\x00\x00\x00\x00"
    cave += b"\xC6\x82" + disp32(D_STABLE, base) + b"\x00"
    cave += ENTRY_5
    cave += b"\xE9" + rel32(va(CAVE_R) + len(cave) + 5, va(ENTRY_R) + 5)
    return bytes(cave)


def build_cave_p(cave_p_off):
    cave = bytearray()
    base = va(cave_p_off) + 5
    cave += b"\xE8\x00\x00\x00\x00"                        # call $+5
    cave += b"\x5A"                                        # pop edx
    cave += b"\x8B\x82" + disp32(D_RST, base)              # mov eax,[D_RST]
    cave += b"\x85\xC0"                                    # test eax,eax
    j_z = len(cave); cave += b"\x74\x00"                   # jz .tunnel
    cave += b"\x80\xBA" + disp32(D_EVER, base) + b"\x00"   # cmp byte [D_EVER],0
    j_e = len(cave); cave += b"\x74\x00"                   # je .tunnel
    # ★v8 连续绑字体计数（无跳转）：cnt = (delta==0) ? cnt+1 : 0
    cave += b"\x8B\x82" + disp32(D_LAST, base)             # mov eax,[D_LAST]
    cave += b"\x8B\x8A" + disp32(D_FRAMES, base)           # mov ecx,[FRAMES]
    cave += b"\x29\xC1"                                    # sub ecx,eax（delta）
    cave += b"\x0F\x94\xC1"                                # setz cl
    cave += b"\x83\xE1\x01"                                # and ecx,1
    cave += b"\x8B\x82" + disp32(D_CNT, base)              # mov eax,[D_CNT]
    cave += b"\x0F\xAF\xC1"                                # imul eax,ecx
    cave += b"\x01\xC8"                                    # add eax,ecx
    cave += b"\x89\x82" + disp32(D_CNT, base)              # mov [D_CNT],eax
    cave += b"\x83\xF8\x03"                                # cmp eax,3
    j_n = len(cave); cave += b"\x72\x00"                   # jb .nostable
    cave += b"\xC6\x82" + disp32(D_STABLE, base) + b"\x01" # mov byte [D_STABLE],1
    nost = len(cave)
    cave[j_n + 1] = (nost - (j_n + 2)) & 0xFF
    # D_STABLE==1 → 放行隧道；否则继续超时检查
    cave += b"\x80\xBA" + disp32(D_STABLE, base) + b"\x01" # cmp byte [D_STABLE],1
    j_s = len(cave); cave += b"\x75\x00"                   # jne .cont
    tunnel = len(cave)
    cave += b"\xEB\x00"                                    # jmp .fwd（放行隧道）
    cont = len(cave)
    cave[j_s + 1] = (cont - (j_s + 2)) & 0xFF
    cave[j_z + 1] = (tunnel - (j_z + 2)) & 0xFF
    cave[j_e + 1] = (tunnel - (j_e + 2)) & 0xFF
    # 超时检查（3.5s）
    cave += b"\x52"
    cave += b"\x8D\x8A" + disp32(D_NOW, base)
    cave += b"\x51"
    cave += b"\xFF\x92" + disp32(IAT_QPC, base)
    cave += b"\x5A"
    cave += b"\x8B\x82" + disp32(D_NOW, base)
    cave += b"\x2B\x82" + disp32(D_RST, base)
    cave += b"\x3B\x82" + disp32(D_WIN, base)
    j_ae = len(cave); cave += b"\x73\x00"                  # jae .fwd
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
    cave[tunnel + 1] = (fwd - (tunnel + 2)) & 0xFF
    cave[j_ae + 1] = (fwd - (j_ae + 2)) & 0xFF
    for jp, tgt in ((j_z, tunnel), (j_e, tunnel), (j_s, cont), (tunnel, fwd), (j_ae, fwd)):
        assert 0 <= (tgt - (jp + 2)) < 128, "rel8 越界: jp=0x%X tgt=0x%X" % (jp, tgt)
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
    cave += b"\xC6\x82" + disp32(D_EVER, base) + b"\x01"   # mov byte [D_EVER], 1（持久）
    cave += b"\x8B\x82" + disp32(D_FRAMES, base)           # mov eax,[FRAMES] ★v8
    cave += b"\x89\x82" + disp32(D_LAST, base)             # mov [D_LAST],eax ★v8（最后绑字帧）
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
    print("=== 构建 clearflash8（F-3：文字绘制稳定停黑）===\n")
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
                  (D_BOUND, 'D_BOUND'), (D_EVER, 'D_EVER'), (D_LAST, 'D_LAST'), (D_CNT, 'D_CNT'),
                  (D_STABLE, 'D_STABLE'), (D_AB8, 'D_AB8'), (D_1A70, 'D_1A70')):
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
    dis_check(cave_p, cave_p_off, 'CAVE_P', (D_RST, D_EVER, D_LAST, D_FRAMES, D_CNT, D_STABLE, D_NOW, D_WIN), va(ENTRY_P) + 5)
    sim_stack(cave_p, va(cave_p_off), 'CAVE_P', CLEAR_VT_OFF, 5)
    print("--- CAVE_ST ---")
    dis_check(cave_st, cave_st_off, 'CAVE_ST', (D_AB8, D_1A70, D_BOUND, D_EVER), va(ENTRY_ST) + 5)
    sim_stack(cave_st, va(cave_st_off), 'CAVE_ST', CLEAR_VT_OFF, 3)
    print("[ok] capstone 全部断言通过\n")

    diff_before = bytes(data)
    data[CAVE_R:CAVE_R + total] = cave_r + cave_p + cave_st
    data[ENTRY_R:ENTRY_R + 5] = b"\xE9" + rel32(va(ENTRY_R) + 5, va(CAVE_R))
    data[ENTRY_P:ENTRY_P + 5] = b"\xE9" + rel32(va(ENTRY_P) + 5, va(cave_p_off))
    data[ENTRY_ST:ENTRY_ST + 5] = b"\xE9" + rel32(va(ENTRY_ST) + 5, va(cave_st_off))
    # ★v5：D2 路径A（[D2S] 启动武装）安静窗 150→30（单字节）
    # 指令 `cmp eax, 0x96`（3D 96 00 00 00）位于 FOFF 0x4BBD，立即数在 0x4BBE
    assert data[0x4BBE] == 0x96, "0x4BBE 基座值非 0x96（预期 150）"
    data[0x4BBE] = 0x1E
    print("[v5] 0x4BBE: 0x96(150) -> 0x1E(30) ✓")
    # patch 后反汇编验证（0x57B6 起：mov eax,[FRAMES]; sub eax,edx; cmp eax,0x1e; jl ...）
    for i2 in md.disasm(bytes(data[0x4BB0:0x4BC8]), 0x100057B0):
        print("  0x%08X  %-20s %s %s" % (i2.address, i2.bytes.hex(), i2.mnemonic, i2.op_str))
    txt = ' '.join(i2.mnemonic + ' ' + i2.op_str for i2 in md.disasm(bytes(data[0x4BAF:0x4BC8]), 0x100057AF))
    assert 'cmp eax, 0x1e' in txt, "0x4BBD 单字节 patch 未生效"
    diff = [i for i in range(len(data)) if data[i] != diff_before[i]]
    allowed = set(range(CAVE_R, CAVE_R + total)) | set(range(ENTRY_R, ENTRY_R + 5)) \
        | set(range(ENTRY_P, ENTRY_P + 5)) | set(range(ENTRY_ST, ENTRY_ST + 5)) | {0x4BBE}
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
