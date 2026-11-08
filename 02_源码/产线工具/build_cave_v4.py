#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_cave_v4.py  ·  2026-10-26  ·  v4 = 绘制期同帧修复 + 帧内缓存 (+ 可选关黑屏遮罩)
================================================================
产物：
  version_c93e_cave_v4.dll        v4 + 黑屏遮罩【关闭】（目标：无缝体验）
  version_c93e_cave_v4_mask.dll   v4 + 黑屏遮罩【保留】（备用对照）

设计（与两轮失败的对应关系）
  v1 崩  : 绝对地址 + ASLR          → 全 PIC（call $+5 / pop esi / [esi+RVA]）
  v2 丢字: g_fontTex/g_bigTex 取反  → PDB 钉死 bigTex=0x41A70 / fontTex=0x41A74
  v2 隐患: 没查"参数已补丁"          → 门禁 g_doneHN > 0
  本版新增: 「本帧已检查」缓存（比较 g_frames），把 3 次 COM 调用压到每帧一次
  本版新增: 关闭 clearflash Clear 遮罩（CAVE_P 里那个 jae 改成无条件跳）
  语义    : 命中即换、**不还原**（正确终态就是大图集绑着）
"""
import struct, hashlib, os, sys

SRC = r'D:\SteamLibrary\steamapps\common\Obscure\version_baseline_0c17dfc0.dll.bak'
OUTDIR = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18\05_hook源码与译文\hook_cave_ds_2026-10-26'
EXPECT_SRC_MD5 = '0c17dfc07b666255b04652dbf0cbe541'

IMAGE_BASE, NEW_VA = 0x10000000, 0x1039E000
FILE_ALIGN, SECT_ALIGN = 0x200, 0x1000
SEC_RVA = NEW_VA - IMAGE_BASE          # 0x39E000

# ---- PDB 权威 RVA（见 方案B_静态推演报告.md 1.1）
R_BIGTEX, R_FONTTEX = 0x41A70, 0x41A74      # bigTex / fontTex（★ 别反）
R_DONEHN = 0x48AA4                          # 已改高对象数（>0 = 参数已补丁）
R_FRAMES = 0x41898                          # g_frames
R_DRAWCNT = 0x48C64                         # g_drawCntDP
R_REFSETTEX, R_ORIGDP = 0x419C4, 0x419E8
FLAG_RVA = SEC_RVA + 0x1F0                  # 「上次检查的帧号」存储位（本帧缓存）
VT_TESTCOOP, VT_GETTEX = 3, 64

ATTACH_FO, HOOK_DP_OLD = 0x1BBF, 0x10005750
MASK_FO = 0x37954                            # CAVE_P: jae short +0x24（超窗则跳过 Clear）
MASK_OLD = bytes.fromhex('7324')             # 73 24 = jae +0x24
MASK_OFF = bytes.fromhex('eb24')             # EB 24 = jmp +0x24（无条件跳过 Clear）


def pin_constants():
    assert R_BIGTEX == 0x41A70 and R_FONTTEX == 0x41A74 and R_BIGTEX != R_FONTTEX
    assert R_DONEHN == 0x48AA4 and R_FRAMES == 0x41898
    print('[ok] RVAs pinned: bigTex=0x41A70 fontTex=0x41A74 doneHN=0x48AA4 frames=0x41898')


class Asm:
    def __init__(self, base):
        self.base, self.code, self.labels, self.fix = base, bytearray(), {}, []

    def b(self, *bs):
        for x in bs:
            self.code.append(x & 0xFF)

    def raw(self, s):
        self.code += s

    def label(self, n):
        assert n not in self.labels
        self.labels[n] = len(self.code)

    def jcc(self, op2, lab):
        self.b(0x0F, op2); self.fix.append((len(self.code), lab)); self.b(0, 0, 0, 0)

    def jmp(self, lab):
        self.b(0xE9); self.fix.append((len(self.code), lab)); self.b(0, 0, 0, 0)

    def call(self, lab):
        self.b(0xE8); self.fix.append((len(self.code), lab)); self.b(0, 0, 0, 0)

    def resolve(self):
        for off, lab in self.fix:
            struct.pack_into('<i', self.code, off,
                             (self.base + self.labels[lab]) - (self.base + off + 4))


def u32(v):
    return struct.pack('<I', v)


def build_code():
    a = Asm(NEW_VA)
    a.raw(b'\x55\x89\xE5')                       # push ebp / mov ebp,esp
    a.raw(b'\x53\x56\x57')                       # push ebx/esi/edi
    a.raw(b'\x83\xEC\x10')                       # sub esp,0x10  ([ebp-0x14]=tex0)

    a.raw(b'\xE8\x00\x00\x00\x00')               # call $+5
    a.label('getbase')
    a.raw(b'\x5E')                               # pop esi
    a.raw(b'\x81\xEE' + u32(SEC_RVA + a.labels['getbase']))   # sub esi,RVA

    a.raw(b'\xFF\x86' + u32(R_DRAWCNT))          # inc dword [esi+g_drawCntDP]

    # ---- 本帧已检查？  (g_frames == last ?)
    a.raw(b'\x8B\x86' + u32(R_FRAMES))           # mov eax,[esi+g_frames]
    a.raw(b'\x3B\x86' + u32(FLAG_RVA))           # cmp eax,[esi+flag]
    a.jcc(0x84, 'pass')                          # je pass
    a.raw(b'\x89\x86' + u32(FLAG_RVA))           # mov [esi+flag],eax

    # ---- 门禁 1/2/3
    a.raw(b'\x8B\x86' + u32(R_FONTTEX)); a.raw(b'\x85\xC0'); a.jcc(0x84, 'pass')
    a.raw(b'\x8B\x86' + u32(R_BIGTEX));  a.raw(b'\x85\xC0'); a.jcc(0x84, 'pass')
    a.raw(b'\x8B\x86' + u32(R_DONEHN));  a.raw(b'\x85\xC0'); a.jcc(0x8E, 'pass')  # jle

    a.raw(b'\x8B\x7D\x08')                       # mov edi,[ebp+8]  ; this
    a.raw(b'\x8B\x07'); a.raw(b'\x85\xC0'); a.jcc(0x84, 'pass')

    # ---- TestCooperativeLevel
    a.raw(b'\x8B\x40' + bytes([VT_TESTCOOP * 4]))
    a.raw(b'\x85\xC0'); a.jcc(0x84, 'pass')
    a.raw(b'\x57\xFF\xD0')                       # push edi; call eax
    a.raw(b'\x85\xC0'); a.jcc(0x85, 'pass')      # jne pass

    # ---- GetTexture(0,&tex0)
    a.raw(b'\x8B\x07')                           # mov eax,[edi]
    a.raw(b'\x8B\x80' + u32(VT_GETTEX * 4))      # mov eax,[eax+64*4]
    a.raw(b'\x85\xC0'); a.jcc(0x84, 'pass')
    a.raw(b'\x8D\x4D\xEC\x51\x6A\x00\x57')       # lea ecx,[ebp-0x14]; push ecx; push 0; push edi
    a.raw(b'\xFF\xD0')                           # call eax
    a.raw(b'\x85\xC0'); a.jcc(0x88, 'pass')      # js pass

    # ---- 判据：stage0 == g_fontTex ?
    a.raw(b'\x8B\x9E' + u32(R_FONTTEX))          # mov ebx,[esi+g_fontTex]
    a.raw(b'\x39\x5D\xEC')                       # cmp [ebp-0x14],ebx
    a.jcc(0x85, 'relpass')                       # jne relpass

    # ---- 命中：释放引用后重绑大图集（不还原）
    a.raw(b'\x8B\x5D\xEC')                       # mov ebx,[ebp-0x14]
    a.call('rel')
    a.raw(b'\xFF\xB6' + u32(R_BIGTEX))           # push dword [esi+g_bigTex]
    a.raw(b'\x6A\x00\xFF\x75\x08')               # push 0; push [ebp+8]
    a.raw(b'\xFF\x96' + u32(R_REFSETTEX))        # call [esi+g_orig_SetTexture]
    a.jmp('pass')

    # ---- 未命中：释放引用后透传
    a.label('relpass')
    a.raw(b'\x8B\x5D\xEC')                       # mov ebx,[ebp-0x14]
    a.call('rel')
    a.jmp('pass')

    # ---- Release(ebx)
    a.label('rel')
    a.raw(b'\x85\xDB'); a.jcc(0x84, 'relret')
    a.raw(b'\x8B\x03'); a.raw(b'\x85\xC0'); a.jcc(0x84, 'relret')
    a.raw(b'\x8B\x40\x08')                       # mov eax,[eax+8]
    a.raw(b'\x85\xC0'); a.jcc(0x84, 'relret')
    a.raw(b'\x53\xFF\xD0')                       # push ebx; call eax
    a.label('relret')
    a.raw(b'\xC3')

    a.label('pass')
    a.raw(b'\xFF\x75\x14\xFF\x75\x10\xFF\x75\x0C\xFF\x75\x08')
    a.raw(b'\xFF\x96' + u32(R_ORIGDP))           # call [esi+g_orig_DP]
    a.raw(b'\x83\xC4\x10\x5F\x5E\x5B\x5D')       # add esp,0x10; pop edi/esi/ebx/ebp
    a.raw(b'\xC2\x10\x00')                       # ret 0x10
    a.resolve()
    return bytes(a.code), a


def patch(out, mask_off):
    notes = []
    assert struct.unpack_from('<I', out, ATTACH_FO)[0] == HOOK_DP_OLD
    struct.pack_into('<I', out, ATTACH_FO, NEW_VA)
    notes.append('attach imm32 @0x1BBF: 0x10005750 -> 0x%X' % NEW_VA)
    if mask_off:
        assert out[MASK_FO:MASK_FO + 2] == MASK_OLD, out[MASK_FO:MASK_FO + 2].hex(' ')
        out[MASK_FO:MASK_FO + 2] = MASK_OFF
        notes.append('CAVE_P @0x37954: jae -> jmp（Clear 不再执行，黑屏遮罩关闭）')
    return out, notes


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

    code, asm = build_code()
    assert len(code) < 0x1F0, 'cave too big for fixed flag offset'

    out = bytearray(src)
    raw = 0x200
    new_fo = len(out)
    body = bytearray(b'\x00' * raw)
    body[:len(code)] = code
    body[0x1F0:0x1F4] = b'\xff\xff\xff\xff'   # flag 初值=-1，避免与 g_frames=0 撞车
    out += body

    hdr = bytearray(40)
    hdr[0:8] = b'.cf11\x00\x00\x00'
    struct.pack_into('<I', hdr, 8, 0x1F4)
    struct.pack_into('<I', hdr, 12, SEC_RVA)
    struct.pack_into('<I', hdr, 16, raw)
    struct.pack_into('<I', hdr, 20, new_fo)
    struct.pack_into('<I', hdr, 36, 0xE0000020)   # CODE|EXEC|READ|WRITE（要写 flag）
    out[sec_end:sec_end + 40] = hdr
    struct.pack_into('<H', out, pe + 6, nsec + 1)
    struct.pack_into('<I', out, pe + 24 + 56, sizeofimage + SECT_ALIGN)
    struct.pack_into('<I', out, pe + 24 + 4,
                     struct.unpack_from('<I', out, pe + 24 + 4)[0] + raw)

    out, notes = patch(out, mask_off)
    return bytes(out), code, notes, sec_end


def verify(out, src, code, sec_end, mask_off):
    diff = [i for i in range(len(src)) if src[i] != out[i]]
    allowed = set(range(sec_end, sec_end + 40)) | set(range(ATTACH_FO, ATTACH_FO + 4))
    allowed |= {0x7E, 0x7F} | set(range(0xC8, 0xCC)) | set(range(0x94, 0x98))
    if mask_off:
        allowed |= set(range(MASK_FO, MASK_FO + 2))
    stray = [i for i in diff if i not in allowed]
    print('  diff bytes: %d  stray: %d' % (len(diff), len(stray)))
    if stray:
        print('   stray offs:', ['0x%X' % i for i in stray[:8]])
    assert not stray

    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM
    md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail = True
    insns = list(md.disasm(code, NEW_VA))
    print('  insns: %d  code bytes: %d' % (len(insns), len(code)))
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
    print('  PIC absolute-image operands: %d' % len(bad))
    assert not bad, bad[:5]

    imap = {i.address: i for i in insns}
    END = NEW_VA + len(code)
    PRO = insns[0].address
    pro_end = next(i.address for i in insns
                   if i.mnemonic == 'call' and i.operands[0].type == X86_OP_IMM)

    def sim(pc, esp, base=None, first=False, seen=frozenset()):
        if first:
            p = PRO
            while p != pro_end:
                i = imap[p]
                if i.mnemonic == 'push':
                    esp -= 4
                elif i.mnemonic.startswith('sub') and 'esp' in i.op_str:
                    esp -= int(i.op_str.split(', ')[1], 16)
                p = i.address + i.size
            base, pc = esp, p
        if base is None:
            base = esp
        while True:
            i = imap.get(pc)
            if i is None:
                return ('bad_pc_0x%X' % pc, esp)
            m, o = i.mnemonic, i.op_str
            if m == 'push':
                esp -= 4
            elif m == 'pop':
                esp += 4
            elif m.startswith('sub') and o.startswith('esp, 0x'):
                esp -= int(o.split(', ')[1], 16)
            elif m.startswith('add') and o.startswith('esp, 0x'):
                esp += int(o.split(', ')[1], 16)
            elif m == 'call':
                t = i.operands[0]
                if t.type == X86_OP_IMM and t.imm == i.address + i.size:
                    esp -= 4
                elif t.type == X86_OP_IMM and NEW_VA <= t.imm < END:
                    r = sim(t.imm, esp, base=base)
                    if r[0] != 'ret_plain':
                        return ('bad_sub_%s' % r[0], esp)
                else:
                    n = (base - esp) // 4
                    if n not in (1, 3, 4):
                        return ('argc_%d@0x%X' % (n, i.address), esp)
                    esp = base
            elif m == 'ret':
                return ('ret16' if o == '0x10' else 'ret_plain', esp)
            elif m == 'jmp':
                pc = i.operands[0].imm
                continue
            elif m.startswith('j'):
                tgt = i.operands[0].imm
                if not (NEW_VA <= tgt < END):
                    return ('bad_jcc', esp)
                if (pc, esp, tgt) not in seen:
                    r = sim(tgt, esp, base=base, seen=seen | {(pc, esp, tgt)})
                    if r[0] not in ('ret16', 'ret_plain'):
                        return r
            pc = i.address + i.size

    r = sim(PRO, 0, first=True)
    print('  stack main path:', r)
    assert r[0] == 'ret16' and r[1] == 0
    nb = 0
    for i in insns:
        if i.mnemonic.startswith('j') and i.mnemonic != 'jmp':
            r2 = sim(i.operands[0].imm, -32, base=-32)
            if r2[0] not in ('ret16', 'ret_plain'):
                nb += 1
                print('    BAD branch 0x%X -> %s' % (i.address, r2))
    print('  branch paths unbalanced: %d' % nb)
    assert nb == 0
    print('  [ok] verifications passed')


def main():
    pin_constants()
    for mask_off, name in ((True, 'version_c93e_cave_v4.dll'),
                           (False, 'version_c93e_cave_v4_mask.dll')):
        print('\n=== build %s (mask_off=%s) ===' % (name, mask_off))
        out, code, notes, sec_end = build(mask_off)
        src = open(SRC, 'rb').read()
        for n in notes:
            print('  ', n)
        verify(out, src, code, sec_end, mask_off)
        p = os.path.join(OUTDIR, name)
        open(p, 'wb').write(out)
        print('  written: %s  %d bytes  md5=%s' % (p, len(out), hashlib.md5(out).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
