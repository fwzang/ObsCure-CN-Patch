# -*- coding: utf-8 -*-
"""build_clearflash9_arm.py -- 2026-10-26 黑屏遮罩扩展：首次进入 + 切屏回来

需求（用户）：主界面【首次进入】和【切屏回来】也要"黑屏直到文字出现"。
现行 clearflash8 的 Clear 门控（已逆向确认）：
    Clear 触发 <=> D_RST!=0（仅 Reset 武装） && D_EVER!=0（曾绑过字体）
                   && D_STABLE==0（未稳定） && (QPC - D_RST) < D_WIN
    → 启动期从未 Reset => D_RST=0 => 永不黑（用户观察正确）。
    → D_STABLE 阈值 3 帧 = 50ms，肉眼几乎不可见。

两处补丁（单变量、可回滚）：
  P1  CAVE_ST 重写（file 0x37984，119 字节预算）：
      保留原有门控（stage0 / AB8 / g_fontTex / tex==g_fontTex / D_BOUND=1 /
      D_EVER=1 / D_LAST=FRAMES），新增：**首次绑字体（旧 D_LAST==0）时复刻
      Reset 的武装动作** —— QPC 写入 D_RST、QPF 重算 D_WIN(=3s)。
      （QPC 一次写 8 字节会连带覆盖 D_WIN，故先 QPC(D_RST) 再算 D_WIN。）
  P2  CAVE_P 的 D_STABLE 阈值 3 -> 20 帧（file 0x3791E 一字节）：
      黑屏最短 ~333ms@60fps，肉眼可见（原 50ms 不可见）。

锚点（装机 DLL 实测，edx = 0x10038189 为 CAVE_ST 的 PIC 基址）：
  D_NOW   0x10048758   D_RST  0x10048760   D_WIN  0x10048764
  D_BOUND 0x1004876C   D_EVER 0x1004876D   D_LAST 0x10048770
  D_CNT   0x10048774   D_STABLE 0x10048778 D_FRAMES 0x10041498
  g_fontTex 0x10041670 D_AB8  0x100486B8   qpf_tmp 0x10048768
  IAT QPC 0x1003F5A4   IAT QPF 0x1003F5A8
校验：capstone 逐条反汇编 / 栈平衡仿真 / 差分审计（仅预期字节变化）/
      行为仿真（首次绑定武装、后续不重复武装、门控拦截）。
"""
import os
import struct
import hashlib
import sys
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

BASE = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18'
SRC = os.path.join(BASE, '稳定保底版_V1.0', 'version.dll')
OUTDIR = os.path.join(BASE, '05_hook源码与译文', 'hook_patch_2026-10-26')
OUT = os.path.join(OUTDIR, 'version_c93e_clearflash9_arm.dll')
SRC_MD5 = '028ec9a88266ba1526aeeabe930c16e6'

TEXT_OFF, TEXT_VA = 0x400, 0x10000C00
CAVE_FO = 0x37984              # CAVE_ST 区（0x37984..0x379FF 共 119 字节可用）
CAVE_VA = TEXT_VA + CAVE_FO - TEXT_OFF   # 0x10038184
EDX_BASE = CAVE_VA + 5                   # call $+5 之后 edx 的值
CAVE_LIMIT = 0x37A00           # .text raw end（含 50 字节 int3 尾带）
THRESH_FO = 0x3791E            # CAVE_P 内 cmp eax,3 的立即数

# 绝对地址（三处 cave 交叉验证一致）
D_NOW, D_RST, D_WIN = 0x10048758, 0x10048760, 0x10048764
D_BOUND, D_EVER, D_LAST = 0x1004876C, 0x1004876D, 0x10048770
D_CNT, D_STABLE = 0x10048774, 0x10048778
D_FRAMES, G_FONTTEX, D_AB8 = 0x10041498, 0x10041670, 0x100486B8
QPF_TMP = 0x10048768
IAT_QPC, IAT_QPF = 0x1003F5A4, 0x1003F5A8
ORIG_BODY = 0x10004B25          # SetTexture 钩子体（序言复刻后跳入）
NEW_THRESH = 0x14               # 20 帧稳定判定（0x03 -> 0x14）


def disp(addr):
    return struct.pack('<i', addr - EDX_BASE)


def md5(b):
    return hashlib.md5(b).hexdigest()


def build_cave():
    """生成 115 字节 cave 代码；返回 (code, fixup_list)。"""
    c = bytearray()
    jmp_fix = []      # (offset_of_rel8, target_label)

    c += b'\xE8\x00\x00\x00\x00'                       # call $+5
    c += b'\x5A'                                       # pop edx
    # gates
    c += b'\x80\x7C\x24\x08\x00'                       # cmp byte [esp+8],0   (stage==0)
    jmp_fix.append((len(c) + 1, 'exit')); c += b'\x75\x00'
    c += b'\x80\xBA' + disp(D_AB8) + b'\x01'           # cmp byte [edx+AB8],1
    jmp_fix.append((len(c) + 1, 'exit')); c += b'\x75\x00'
    c += b'\x8B\x82' + disp(G_FONTTEX)                 # mov eax,[edx+g_fontTex]
    c += b'\x39\x44\x24\x0C'                           # cmp [esp+0xC],eax  (tex==fontTex)
    jmp_fix.append((len(c) + 1, 'exit')); c += b'\x75\x00'
    # flags: D_BOUND=1, D_EVER=1（相邻字节，一次 word 写）
    c += b'\x66\xC7\x82' + disp(D_BOUND) + b'\x01\x01'
    # D_LAST 更新 + 旧值捕获
    c += b'\x8B\x8A' + disp(D_FRAMES)                  # mov ecx,[edx+D_FRAMES]
    c += b'\x87\x8A' + disp(D_LAST)                    # xchg ecx,[edx+D_LAST]
    c += b'\x85\xC9'                                   # test ecx,ecx
    jmp_fix.append((len(c) + 1, 'exit')); c += b'\x75\x00'
    # 首次绑字体 -> 武装：QPC 直接写 D_RST（8 字节会连带覆盖 D_WIN，随后重算）
    c += b'\x52'                                       # push edx
    c += b'\x8D\x8A' + disp(D_RST)                     # lea ecx,[edx+D_RST]
    c += b'\x51'                                       # push ecx
    c += b'\xFF\x92' + disp(IAT_QPC)                   # call [edx+IAT_QPC]
    c += b'\x5A'                                       # pop edx
    # D_WIN = qpf * 3（约 3 秒）
    c += b'\x52'                                       # push edx
    c += b'\x8D\x8A' + disp(QPF_TMP)                   # lea ecx,[edx+qpf_tmp]
    c += b'\x51'                                       # push ecx
    c += b'\xFF\x92' + disp(IAT_QPF)                   # call [edx+IAT_QPF]
    c += b'\x5A'                                       # pop edx
    c += b'\x8B\x82' + disp(QPF_TMP)                   # mov eax,[edx+qpf_tmp]
    c += b'\x8D\x4C\x40\x00'                           # lea ecx,[eax+eax*2]
    c += b'\x89\x8A' + disp(D_WIN)                     # mov [edx+D_WIN],ecx
    # 序言复刻 + 跳回钩子体
    exit_off = len(c)
    c += b'\x55\x89\xE5\x53\x57'                       # push ebp; mov ebp,esp; push ebx; push edi
    c += b'\xE9' + struct.pack('<i', ORIG_BODY - (CAVE_VA + len(c) + 5))
    for off, label in jmp_fix:
        rel = exit_off - (off + 1)
        assert -128 <= rel <= 127, 'rel8 out of range'
        c[off] = rel & 0xFF
    return bytes(c), exit_off


def sim_stack(code, base_va):
    """栈平衡仿真（capstone）：分支点必须 esp==0；序言复刻 12 字节后可跳入钩子体。
    间接调用按 stdcall 参数清理建模：QPC/QPF 各 1 个指针参数（callee 清 4 字节）。"""
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    esp = 0
    i = 0
    insns = list(md.disasm(code, base_va))
    for ins in insns:
        m, o = ins.mnemonic, ins.op_str
        if m == 'push':
            esp -= 4
        elif m == 'pop':
            esp += 4
        elif m == 'call':
            if o.startswith('0x'):
                esp -= 4                      # call $+5（随后 pop 平衡）
            elif '[' in o:
                # 间接调用：ret 4（返回地址）+ 4（1 个参数）=> 净 +4
                esp += 4
            else:
                raise AssertionError('unexpected call: %s' % o)
        elif m == 'jmp':
            tgt = int(o, 16)
            if tgt == ORIG_BODY:
                assert esp == -12, 'prologue replay pushes != 3 (esp=%d)' % esp
            else:
                assert esp == 0, 'jmp with esp=%d' % esp
        elif m in ('jne', 'je', 'jb', 'jae'):
            assert esp == 0, 'branch with esp=%d' % esp
        elif m in ('ret', 'int3', 'hlt'):
            raise AssertionError('unexpected control flow: %s' % m)
        i += 1
    return True


def sim_behavior():
    """行为仿真：首次绑定武装，后续不重复武装；门控拦截。"""
    def run(seq):
        state = {'last': 0, 'rst': 0, 'bound': 0, 'ever': 0, 'armed': 0}
        out = []
        for stage, ab8, tex_is_font, frames in seq:
            if stage != 0 or ab8 != 1 or not tex_is_font:
                out.append('pass'); continue
            state['bound'] = 1; state['ever'] = 1
            old = state['last']; state['last'] = frames
            if old == 0:
                state['rst'] = 1; state['armed'] += 1
                out.append('bind+arm')
            else:
                out.append('bind')
        return out, state
    r, st = run([(0, 1, True, 500)])                       # 首次
    assert r == ['bind+arm'] and st['armed'] == 1, r
    r, st = run([(0, 1, True, 500), (0, 1, True, 501), (0, 1, True, 502)])
    assert r == ['bind+arm', 'bind', 'bind'] and st['armed'] == 1, r
    r, _ = run([(0, 1, False, 1), (1, 1, True, 2), (0, 0, True, 3)])   # 门控拦截
    assert r == ['pass', 'pass', 'pass'], r
    r, st = run([(0, 1, True, 0)])                          # 帧号 0 边界：old=0 判定
    assert r == ['bind+arm'], r
    print('[ok] 行为仿真：首次武装 / 不重复 / 门控拦截')


def main():
    src = open(SRC, 'rb').read()
    print('src %s md5=%s' % (SRC, md5(src)))
    assert md5(src) == SRC_MD5, 'source md5 mismatch'

    code, exit_off = build_cave()
    budget = CAVE_LIMIT - CAVE_FO
    print('cave: %d bytes (budget %d, spare %d)' % (len(code), budget, budget - len(code)))
    assert len(code) <= budget, 'cave too large'

    # 解析式校验
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    print('\n=== disassembly of new CAVE_ST ===')
    for ins in md.disasm(code, CAVE_VA):
        print('  0x%X  %-8s %s' % (ins.address, ins.mnemonic, ins.op_str))
    sim_stack(code, CAVE_VA)
    print('[ok] stack balance')
    sim_behavior()

    # 落盘
    out = bytearray(src)
    out[CAVE_FO:CAVE_FO + len(code)] = code
    out[CAVE_FO + len(code):CAVE_LIMIT] = b'\xCC' * (CAVE_LIMIT - CAVE_FO - len(code))
    assert out[THRESH_FO] == 0x03 and out[THRESH_FO - 2:THRESH_FO] == b'\x83\xF8', \
        'threshold patch site mismatch: %s' % out[THRESH_FO - 2:THRESH_FO + 1].hex()
    out[THRESH_FO] = NEW_THRESH
    out = bytes(out)

    # 差分审计：所有差异字节必须落在 允许区域 = CAVE_ST 区 + 阈值字节
    diff = [i for i in range(len(src)) if src[i] != out[i]]
    allowed = list(range(CAVE_FO, CAVE_LIMIT)) + [THRESH_FO]
    illegal = [i for i in diff if i not in allowed]
    print('\n=== diff audit ===')
    print('  changed bytes: %d (threshold=1, cave=%d)' %
          (len(diff), len([i for i in diff if CAVE_FO <= i < CAVE_LIMIT])))
    print('  illegal offsets: %s' % ([hex(i) for i in illegal[:10]] or 'none'))
    assert not illegal, 'unexpected diff outside patch regions: %s' % illegal
    assert THRESH_FO in diff, 'threshold byte not patched'
    assert out[THRESH_FO] == NEW_THRESH
    print('[ok] diff confined to CAVE_ST + threshold byte')

    os.makedirs(OUTDIR, exist_ok=True)
    open(OUT, 'wb').write(out)
    print('\nout: %s\n     md5=%s  size=%d' % (OUT, md5(out), len(out)))


if __name__ == '__main__':
    main()
