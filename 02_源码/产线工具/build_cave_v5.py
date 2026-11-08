#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_cave_v5.py  ·  2026-10-26  ·  v5 = 「把 watchdog_swap 提前到第一个 DrawPrimitive」
================================================================
为什么要换做法（v4 实测失败：主菜单在，文字全部不显示）
  v4 自己实现了判断：stage0 == g_fontTex → 绑 g_bigTex。
  但它只搬了「换纹理」这一半，没有原子地补上「参数 +12 = 4096」。
  于是引擎按 128 归一化去采样 4096 高的图集 → 采到空白区 → 文字全丢。
  （这正是源码 1491 行那条铁律：换纹理与改参数必须同进同退。）

v5 的做法：不自己判断，直接调用【已经在 Present 里跑通】的 watchdog_swap。
  - RVA 0x6270，参数走 ECX，普通 ret（调用方不清栈）
  - 它内部自带完整门禁（dev / g_fontTex / g_bigTex / g_orig_SetTexture 任一为 0 就 ret）
  - 它自己会做「同帧开闸 + 补参数 + 换纹理」的原子动作（源码 c45 注释）
  => 最坏情况等价于"把 Present 的动作提前一帧内做完"，不会引入新的错误状态。

本版只加一件事：**每帧第一次 DrawPrimitive 时调用它**，把 1~2 帧的错窗压到 0。
另外可选关闭 clearflash 的 Clear 遮罩（同 v4：file 0x37954 的 73 24 -> EB 24）。

产物：
  version_c93e_cave_v5.dll        遮罩【关闭】
  version_c93e_cave_v5_mask.dll   遮罩【保留】
"""
import struct, hashlib, os, sys

SRC = r'D:\SteamLibrary\steamapps\common\Obscure\version_baseline_0c17dfc0.dll.bak'
OUTDIR = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18\05_hook源码与译文\hook_cave_ds_2026-10-26'
EXPECT_SRC_MD5 = '0c17dfc07b666255b04652dbf0cbe541'

IMAGE_BASE, NEW_VA = 0x10000000, 0x1039E000
SECT_ALIGN = 0x1000
SEC_RVA = NEW_VA - IMAGE_BASE

R_FRAMES = 0x41898          # g_frames
R_ORIGDP = 0x419E8          # g_orig_DP
R_WATCHDOG = 0x6270         # watchdog_swap（函数 RVA）
FLAG_RVA = SEC_RVA + 0x1F0  # 帧号缓存

ATTACH_FO, HOOK_DP_OLD = 0x1BBF, 0x10005750
MASK_FO = 0x37954
MASK_OLD, MASK_OFF = bytes.fromhex('7324'), bytes.fromhex('eb24')


class Asm:
    def __init__(self, base):
        self.base, self.code, self.labels, self.fix = base, bytearray(), {}, []

    def b(self, *bs):
        for x in bs:
            self.code.append(x & 0xFF)

    def raw(self, s):
        self.code += s

    def label(self, n):
        self.labels[n] = len(self.code)

    def jcc(self, op2, lab):
        self.b(0x0F, op2); self.fix.append((len(self.code), lab)); self.b(0, 0, 0, 0)

    def call_va(self, va):
        self.b(0xE8); self.fix.append((len(self.code), va)); self.b(0, 0, 0, 0)

    def resolve(self):
        for off, lab in self.fix:
            tgt = self.base + self.labels[lab] if isinstance(lab, str) else lab
            struct.pack_into('<i', self.code, off, tgt - (self.base + off + 4))


def u32(v):
    return struct.pack('<I', v)


def build_code():
    a = Asm(NEW_VA)
    a.raw(b'\x55\x89\xE5')                    # push ebp / mov ebp,esp
    a.raw(b'\x56')                            # push esi
    a.raw(b'\xE8\x00\x00\x00\x00')            # call $+5
    a.label('gb')
    a.raw(b'\x5E')                            # pop esi
    a.raw(b'\x81\xEE' + u32(SEC_RVA + a.labels['gb']))   # sub esi,RVA(gb)

    # 每帧只做一次
    a.raw(b'\x8B\x86' + u32(R_FRAMES))        # mov eax,[esi+g_frames]
    a.raw(b'\x3B\x86' + u32(FLAG_RVA))        # cmp eax,[esi+flag]
    a.jcc(0x84, 'pass')                       # je pass
    a.raw(b'\x89\x86' + u32(FLAG_RVA))        # mov [esi+flag],eax

    a.raw(b'\x8B\x4D\x08')                    # mov ecx,[ebp+8]   ; dev -> ecx
    a.call_va(IMAGE_BASE + R_WATCHDOG)        # call watchdog_swap

    a.label('pass')
    a.raw(b'\xFF\x75\x14\xFF\x75\x10\xFF\x75\x0C\xFF\x75\x08')
    a.raw(b'\xFF\x96' + u32(R_ORIGDP))        # call [esi+g_orig_DP]
    a.raw(b'\x5E\x5D')                        # pop esi / pop ebp
    a.raw(b'\xC2\x10\x00')                    # ret 0x10
    a.resolve()
    return bytes(a.code)


def build(mask_off):
    src = open(SRC, 'rb').read()
    assert hashlib.md5(src).hexdigest() == EXPECT_SRC_MD5
    pe = struct.unpack_from('<I', src, 0x3C)[0]
    nsec = struct.unpack_from('<H', src, pe + 6)[0]
    optsz = struct.unpack_from('<H', src, pe + 20)[0]
    sizeofimage = struct.unpack_from('<I', src, pe + 24 + 56)[0]
    sizeofheaders = struct.unpack_from('<I', src, pe + 24 + 60)[0]
    sec_end = pe + 24 + optsz + nsec * 40
    assert sizeofheaders - sec_end >= 40 and sizeofimage == 0x39E000

    code = build_code()
    out = bytearray(src)
    new_fo = len(out)
    body = bytearray(b'\x00' * 0x200)
    body[:len(code)] = code
    body[0x1F0:0x1F4] = b'\xff\xff\xff\xff'
    out += body

    hdr = bytearray(40)
    hdr[0:8] = b'.cf12\x00\x00\x00'
    struct.pack_into('<I', hdr, 8, 0x1F4)
    struct.pack_into('<I', hdr, 12, SEC_RVA)
    struct.pack_into('<I', hdr, 16, 0x200)
    struct.pack_into('<I', hdr, 20, new_fo)
    struct.pack_into('<I', hdr, 36, 0xE0000020)
    out[sec_end:sec_end + 40] = hdr
    struct.pack_into('<H', out, pe + 6, nsec + 1)
    struct.pack_into('<I', out, pe + 24 + 56, sizeofimage + SECT_ALIGN)
    struct.pack_into('<I', out, pe + 24 + 4,
                     struct.unpack_from('<I', out, pe + 24 + 4)[0] + 0x200)

    assert struct.unpack_from('<I', out, ATTACH_FO)[0] == HOOK_DP_OLD
    struct.pack_into('<I', out, ATTACH_FO, NEW_VA)
    if mask_off:
        assert out[MASK_FO:MASK_FO + 2] == MASK_OLD
        out[MASK_FO:MASK_FO + 2] = MASK_OFF
    return bytes(out), code, sec_end


def verify(out, src, code, sec_end, mask_off):
    diff = [i for i in range(len(src)) if src[i] != out[i]]
    allowed = set(range(sec_end, sec_end + 40)) | set(range(ATTACH_FO, ATTACH_FO + 4))
    allowed |= {0x7E, 0x7F} | set(range(0xC8, 0xCC)) | set(range(0x94, 0x98))
    if mask_off:
        allowed |= set(range(MASK_FO, MASK_FO + 2))
    stray = [i for i in diff if i not in allowed]
    print('  cave %d bytes; diff %d, stray %d' % (len(code), len(diff), len(stray)))
    assert not stray, ['0x%X' % i for i in stray[:6]]

    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM
    md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail = True
    insns = list(md.disasm(code, NEW_VA))
    for i in insns:
        print('    0x%X  %-7s %s' % (i.address, i.mnemonic, i.op_str))
    bad = []
    for ins in insns:
        if ins.mnemonic.startswith('j') or (ins.mnemonic == 'call' and ins.operands[0].type == X86_OP_IMM):
            continue
        for op in ins.operands:
            v = None
            if op.type == X86_OP_IMM:
                v = op.imm & 0xFFFFFFFF
            elif op.type == X86_OP_MEM and op.mem.disp:
                v = op.mem.disp & 0xFFFFFFFF
            if v is not None and 0x10000000 <= v < 0x11000000:
                bad.append((hex(ins.address), ins.mnemonic, ins.op_str))
    print('  PIC absolute operands: %d' % len(bad))
    assert not bad

    # call watchdog 的目标必须钉在 0x10006270
    tgts = [i.operands[0].imm for i in insns
            if i.mnemonic == 'call' and i.operands[0].type == X86_OP_IMM]
    print('  call targets:', ['0x%X' % t for t in tgts])
    assert (IMAGE_BASE + R_WATCHDOG) in tgts
    print('  [ok] watchdog_swap target pinned')


def main():
    for mask_off, name in ((True, 'version_c93e_cave_v5.dll'),
                           (False, 'version_c93e_cave_v5_mask.dll')):
        print('\n=== %s (mask_off=%s) ===' % (name, mask_off))
        out, code, sec_end = build(mask_off)
        verify(out, open(SRC, 'rb').read(), code, sec_end, mask_off)
        p = os.path.join(OUTDIR, name)
        open(p, 'wb').write(out)
        print('  written %s  %d bytes  md5=%s' % (p, len(out), hashlib.md5(out).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
