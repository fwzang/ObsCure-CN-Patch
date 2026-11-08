#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_cave_v6.py  ·  2026-10-26  ·  v6 = 纯前哨（thunk），原 Hook_DP 逻辑一行不动
================================================================
v5 为什么丢文字（截图证据）
  标题画面 3D 正常、右下角英文小字正常渲染，但中文菜单文字没有
  => 替换机制整帧未生效（不是 UV 采样错位）。
  v5 把 Hook_DP **整个替换**成自己的 cave，丢掉了原函数体
  （g_drawCntDP++ 与 d2_try 那段内联的「绑大图集 + 补参数」原子块）。
  也就是说：本 build 里让文字生效的可能正是那段内联块，我把它删了。

v6 的做法（最小侵入）
  不替换、只前插：
      push esi                     ; 自己取基址要占用 esi，先存
      call $+5 / pop esi / sub esi,RVA
      if (g_frames != flag) { flag = g_frames; ecx = this; call watchdog_swap }
      pop esi                      ; 还原 caller 的 esi（ABI：esi 是 callee-saved）
      jmp 0x10005750               ; 跳回【原 Hook_DP 入口】，之后一切照旧
  · 栈在 jmp 时与入口完全一致（push/pop 平衡），原函数看到的世界没有任何变化
  · 唯一新增行为 = 每帧第一次 DP 之前多调一次 watchdog_swap
  · 目标 0x10005750 / 0x10006270 都是 rel32，ASLR 下自动正确

产物：
  version_c93e_cave_v6.dll       遮罩【保留】（= 只测"前哨"这一个变量，纪律要求）
  version_c93e_cave_v6_off.dll   遮罩【关闭】（前一步有效后再试这个）
"""
import struct, hashlib, os, sys

SRC = r'D:\SteamLibrary\steamapps\common\Obscure\version_baseline_0c17dfc0.dll.bak'
OUTDIR = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18\05_hook源码与译文\hook_cave_ds_2026-10-26'
EXPECT_SRC_MD5 = '0c17dfc07b666255b04652dbf0cbe541'

IMAGE_BASE, NEW_VA = 0x10000000, 0x1039E000
SECT_ALIGN = 0x1000
SEC_RVA = NEW_VA - IMAGE_BASE

R_FRAMES = 0x41898
R_WATCHDOG = 0x6270
HOOK_DP_VA = 0x10005750
FLAG_RVA = SEC_RVA + 0x1F0
ATTACH_FO, HOOK_DP_OLD = 0x1BBF, HOOK_DP_VA
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

    def jmp_va(self, va):
        self.b(0xE9); self.fix.append((len(self.code), va)); self.b(0, 0, 0, 0)

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
    a.raw(b'\x56')                              # push esi
    a.raw(b'\xE8\x00\x00\x00\x00')              # call $+5
    a.label('gb')
    a.raw(b'\x5E')                              # pop esi
    a.raw(b'\x81\xEE' + u32(SEC_RVA + a.labels['gb']))   # sub esi,RVA(gb)

    a.raw(b'\x8B\x86' + u32(R_FRAMES))          # mov eax,[esi+g_frames]
    a.raw(b'\x3B\x86' + u32(FLAG_RVA))          # cmp eax,[esi+flag]
    a.jcc(0x84, 'skip')                         # je skip
    a.raw(b'\x89\x86' + u32(FLAG_RVA))          # mov [esi+flag],eax
    a.raw(b'\x8B\x4C\x24\x08')                  # mov ecx,[esp+8]  (this；esp 已被我们压了 esi)
    a.call_va(IMAGE_BASE + R_WATCHDOG)          # call watchdog_swap

    a.label('skip')
    a.raw(b'\x5E')                              # pop esi  （还原 caller 的 esi）
    a.jmp_va(HOOK_DP_VA)                        # jmp 原 Hook_DP 入口
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
    hdr[0:8] = b'.cf13\x00\x00\x00'
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
    # 关键：attach 指向我们的 thunk，thunk 末尾再跳回原入口
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
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    for i in md.disasm(code, NEW_VA):
        print('    0x%X  %-7s %s' % (i.address, i.mnemonic, i.op_str))
    print('  [ok] diff whitelist clean')
    # 关键断言：末端必须是 jmp 回 0x10005750
    assert code[-5] == 0xE9, code[-6:].hex(' ')
    tgt = NEW_VA + len(code) + struct.unpack('<i', code[-4:])[0]
    print('  jmp back target = 0x%X (expect 0x%X)' % (tgt, HOOK_DP_VA))
    assert tgt == HOOK_DP_VA


def main():
    for mask_off, name in ((False, 'version_c93e_cave_v6.dll'),
                           (True, 'version_c93e_cave_v6_off.dll')):
        print('\n=== %s (mask_off=%s) ===' % (name, mask_off))
        out, code, sec_end = build(mask_off)
        verify(out, open(SRC, 'rb').read(), code, sec_end, mask_off)
        p = os.path.join(OUTDIR, name)
        open(p, 'wb').write(out)
        print('  written %s  md5=%s' % (os.path.basename(p), hashlib.md5(out).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
