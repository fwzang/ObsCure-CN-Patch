#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_cave_drawscope_v2.py  ·  2026-10-26
===============================================================
CAVE 一期 v2：DrawPrimitive 绘制期「作用域换纹理」——位置无关(PIC)版

v1 为什么崩（由 minidump 实证）
  · 进程内 version.dll 基址 = 0x6F1E0000，不是首选 0x10000000 -> ASLR 生效
  · 既有 cave 的绝对地址能用，是因为编译器为它们生成了 .reloc 项，加载时被修正
  · 手写的新节没有任何重定位项 -> 绝对地址 0x10048C64 未被修正
  · 崩溃点 0x6F1E0000+0x39E009 = cave 第一条 inc dword [0x10048C64]
    异常 0xC0000005 写违规，参数 addr=0x10048C64  ->  完全吻合
  · 挂钩点本身正常（0x6F57E000 = 重定位后的 attach imm32，说明入口跳转正确）

v2 修法
  · 入口 call/pop 自取运行时基址 -> esi，全局一律 [esi+偏移] 访问
  · 代码中不再出现任何 0x1004xxxx 绝对地址 -> 与本进程基址无关
  · 其余逻辑与 v1 完全一致（绘制期换纹理 + 立即还原 + 严格门禁）
"""
import struct, sys, hashlib, os

SRC = r'D:\SteamLibrary\steamapps\common\Obscure\version_baseline_0c17dfc0.dll.bak'
OUTDIR = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18\05_hook源码与译文\hook_cave_ds_2026-10-26'
OUT = os.path.join(OUTDIR, 'version_c93e_cave_ds2.dll')

EXPECT_SRC_MD5 = '0c17dfc07b666255b04652dbf0cbe541'   # 稳定保底版 clearflash9_arm

IMAGE_BASE   = 0x10000000
NEW_VA       = 0x1039E000
FILE_ALIGN   = 0x200
SECT_ALIGN   = 0x1000

# 全局量：RVA（相对 ImageBase）——代码里只出现这些偏移
R_FONTTEX    = 0x10041A70 - IMAGE_BASE      # 0x41A70
R_BIGTEX     = 0x10041A74 - IMAGE_BASE      # 0x41A74
R_REFSETTEX  = 0x100419C4 - IMAGE_BASE      # 0x419C4
R_ORIGDP     = 0x100419E8 - IMAGE_BASE      # 0x419E8
R_DRAWCNT_DP = 0x10048C64 - IMAGE_BASE      # 0x48C64

ATTACH_FO    = 0x1BBF
HOOK_DP_OLD  = 0x10005750
VT_TESTCOOP  = 3
VT_GETTEX    = 64


class Asm:
    def __init__(self, base):
        self.base = base
        self.code = bytearray()
        self.labels = {}
        self.fixups = []

    def b(self, *bs):
        for x in bs:
            self.code.append(x & 0xFF)

    def raw(self, s):
        self.code += s

    def label(self, name):
        assert name not in self.labels
        self.labels[name] = len(self.code)

    def jcc(self, op2, label):
        self.b(0x0F, op2)
        self.fixups.append((len(self.code), label))
        self.b(0, 0, 0, 0)

    def jmp(self, label):
        self.b(0xE9)
        self.fixups.append((len(self.code), label))
        self.b(0, 0, 0, 0)

    def call(self, label):
        self.b(0xE8)
        self.fixups.append((len(self.code), label))
        self.b(0, 0, 0, 0)

    def resolve(self):
        for off, label in self.fixups:
            tgt = self.base + self.labels[label]
            rel = tgt - (self.base + off + 4)
            struct.pack_into('<i', self.code, off, rel)


def off32(v):
    return struct.pack('<I', v)


def build_code():
    a = Asm(NEW_VA)

    a.raw(b'\x55')                       # push ebp
    a.raw(b'\x89\xE5')                   # mov ebp, esp
    a.raw(b'\x53')                       # push ebx
    a.raw(b'\x56')                       # push esi
    a.raw(b'\x57')                       # push edi
    a.raw(b'\x83\xEC\x10')               # sub esp, 0x10    ([ebp-0x14] = tex0)

    # ---- 取运行时基址：call $+5 / pop esi / sub esi, RVA(getbase)
    a.raw(b'\xE8\x00\x00\x00\x00')       # call $+5
    a.label('getbase')
    a.raw(b'\x5E')                       # pop esi
    rva_getbase = NEW_VA - IMAGE_BASE + a.labels['getbase']
    a.raw(b'\x81\xEE' + off32(rva_getbase))   # sub esi, rva_getbase  -> esi = 真实基址

    a.raw(b'\xFF\x86' + off32(R_DRAWCNT_DP))  # inc dword [esi+g_drawCntDP]

    # ---- 门禁：三指针就绪
    a.raw(b'\x8B\x86' + off32(R_FONTTEX))     # mov eax,[esi+g_fontTex]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x8B\x86' + off32(R_BIGTEX))      # mov eax,[esi+g_bigTex]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x8B\x9E' + off32(R_REFSETTEX))   # mov ebx,[esi+g_ref_SetTexture]
    a.raw(b'\x85\xDB')
    a.jcc(0x84, 'pass')

    a.raw(b'\x8B\x7D\x08')                    # mov edi,[ebp+8]   ; this
    a.raw(b'\x8B\x07')                        # mov eax,[edi]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x8B\x40\x0C')                    # mov eax,[eax+3*4]  ; TestCooperativeLevel
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x57')                            # push edi
    a.raw(b'\xFF\xD0')                        # call eax
    a.raw(b'\x85\xC0')
    a.jcc(0x85, 'pass')                       # jne pass  (非 S_OK 放行)

    # ---- GetTexture(0,&tex0)
    a.raw(b'\x8B\x07')                        # mov eax,[edi]
    a.raw(b'\x8B\x80' + off32(VT_GETTEX * 4)) # mov eax,[eax+64*4]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x8D\x4D\xEC')                    # lea ecx,[ebp-0x14]
    a.raw(b'\x51')                            # push ecx
    a.raw(b'\x6A\x00')                        # push 0
    a.raw(b'\x57')                            # push edi
    a.raw(b'\xFF\xD0')                        # call eax
    a.raw(b'\x85\xC0')
    a.jcc(0x88, 'pass')                       # js pass

    a.raw(b'\x8B\x7D\xEC')                    # mov edi,[ebp-0x14]   ; tex0
    a.raw(b'\x8B\x86' + off32(R_FONTTEX))     # mov eax,[esi+g_fontTex]
    a.raw(b'\x3B\xF8')                        # cmp edi,eax
    a.jcc(0x85, 'relpass')

    # ---- 是字体纹理：本次绘制换大图集
    a.call('rel')                             # Release(tex0)
    a.raw(b'\xFF\xB6' + off32(R_BIGTEX))      # push dword [esi+g_bigTex]
    a.raw(b'\x6A\x00')                        # push 0
    a.raw(b'\xFF\x75\x08')                    # push dword [ebp+8]
    a.raw(b'\xFF\xD3')                        # call ebx (orig SetTexture)
    a.raw(b'\xFF\x75\x14')                    # push [ebp+0x14]
    a.raw(b'\xFF\x75\x10')                    # push [ebp+0x10]
    a.raw(b'\xFF\x75\x0C')                    # push [ebp+0x0C]
    a.raw(b'\xFF\x75\x08')                    # push [ebp+8]
    a.raw(b'\xFF\x96' + off32(R_ORIGDP))      # call dword [esi+g_orig_DP]
    a.raw(b'\xFF\xB6' + off32(R_FONTTEX))     # push dword [esi+g_fontTex]
    a.raw(b'\x6A\x00')                        # push 0
    a.raw(b'\xFF\x75\x08')                    # push dword [ebp+8]
    a.raw(b'\xFF\xD3')                        # call ebx
    a.jmp('done')

    a.label('relpass')
    a.call('rel')
    a.jmp('pass')

    # ---- Release(edi) 子程序
    a.label('rel')
    a.raw(b'\x85\xFF')                        # test edi,edi
    a.jcc(0x84, 'relret')
    a.raw(b'\x8B\x07')                        # mov eax,[edi]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'relret')
    a.raw(b'\x8B\x40\x08')                    # mov eax,[eax+2*4]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'relret')
    a.raw(b'\x57')                            # push edi
    a.raw(b'\xFF\xD0')                        # call eax
    a.label('relret')
    a.raw(b'\xC3')                            # ret

    a.label('pass')
    a.raw(b'\xFF\x75\x14')
    a.raw(b'\xFF\x75\x10')
    a.raw(b'\xFF\x75\x0C')
    a.raw(b'\xFF\x75\x08')
    a.raw(b'\xFF\x96' + off32(R_ORIGDP))      # call dword [esi+g_orig_DP]

    a.label('done')
    a.raw(b'\x83\xC4\x10')                    # add esp,0x10
    a.raw(b'\x5F')                            # pop edi
    a.raw(b'\x5E')                            # pop esi
    a.raw(b'\x5B')                            # pop ebx
    a.raw(b'\x5D')                            # pop ebp
    a.raw(b'\xC2\x10\x00')                    # ret 0x10

    a.resolve()
    return bytes(a.code), a


def main():
    src = open(SRC, 'rb').read()
    md5 = hashlib.md5(src).hexdigest()
    print('source      : %s' % SRC)
    print('source md5  : %s  %s' % (md5, 'OK' if md5 == EXPECT_SRC_MD5 else 'MISMATCH'))
    assert md5 == EXPECT_SRC_MD5

    pe = struct.unpack_from('<I', src, 0x3C)[0]
    nsec = struct.unpack_from('<H', src, pe + 6)[0]
    optsz = struct.unpack_from('<H', src, pe + 20)[0]
    sizeofimage = struct.unpack_from('<I', src, pe + 24 + 56)[0]
    sizeofheaders = struct.unpack_from('<I', src, pe + 24 + 60)[0]
    first_sec = pe + 24 + optsz
    sec_tbl_end = first_sec + nsec * 40
    assert sizeofheaders - sec_tbl_end >= 40
    assert sizeofimage == 0x39E000, hex(sizeofimage)

    code, asm = build_code()
    print('cave size   : %d bytes @VA 0x%X,  getbase RVA=0x%X'
          % (len(code), NEW_VA, NEW_VA - IMAGE_BASE + asm.labels['getbase']))
    assert len(code) <= 0x200

    out = bytearray(src)
    raw_size = (len(code) + FILE_ALIGN - 1) // FILE_ALIGN * FILE_ALIGN
    assert (len(out) % FILE_ALIGN) == 0
    new_fo = len(out)
    out += code + b'\x00' * (raw_size - len(code))

    hdr = bytearray(40)
    hdr[0:8] = b'.cf10\x00\x00\x00'
    struct.pack_into('<I', hdr, 8, len(code))
    struct.pack_into('<I', hdr, 12, NEW_VA - IMAGE_BASE)
    struct.pack_into('<I', hdr, 16, raw_size)
    struct.pack_into('<I', hdr, 20, new_fo)
    struct.pack_into('<I', hdr, 36, 0x60000020)
    out[sec_tbl_end:sec_tbl_end + 40] = hdr

    struct.pack_into('<H', out, pe + 6, nsec + 1)
    new_image = sizeofimage + ((len(code) + SECT_ALIGN - 1) // SECT_ALIGN) * SECT_ALIGN
    struct.pack_into('<I', out, pe + 24 + 56, new_image)
    struct.pack_into('<I', out, pe + 24 + 4,
                     struct.unpack_from('<I', out, pe + 24 + 4)[0] + raw_size)

    cur = struct.unpack_from('<I', out, ATTACH_FO)[0]
    assert cur == HOOK_DP_OLD, hex(cur)
    struct.pack_into('<I', out, ATTACH_FO, NEW_VA)

    out = bytes(out)
    os.makedirs(OUTDIR, exist_ok=True)
    open(OUT, 'wb').write(out)
    print('written     : %s' % OUT)
    print('out size    : %d (+%d)  md5 %s'
          % (len(out), len(out) - len(src), hashlib.md5(out).hexdigest()))
    print('SizeOfImage : 0x%X -> 0x%X' % (sizeofimage, new_image))

    # ---- 差分白名单
    diff = [i for i in range(len(src)) if src[i] != out[i]]
    allowed = set(range(sec_tbl_end - 40 + 40, sec_tbl_end + 40))
    allowed |= set(range(ATTACH_FO, ATTACH_FO + 4))
    allowed |= {pe + 6, pe + 7}
    allowed |= set(range(pe + 24 + 56, pe + 24 + 60))
    allowed |= set(range(pe + 24 + 4, pe + 24 + 8))
    stray = [i for i in diff if i not in allowed]
    print('diff bytes  : %d, outside whitelist: %d' % (len(diff), len(stray)))
    assert not stray, ['0x%X' % i for i in stray[:10]]
    print('[ok] diff confined to whitelist')

    # ---- PIC 自检（操作数级，不是朴素字节扫描）：
    #      cave 内不得出现任何落在镜像地址区间 [0x10000000,0x11000000) 的
    #      立即数 / 内存位移（除了取基址用的 RVA，它远小于该区间）
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM
    md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail = True
    bad = []
    for ins in md.disasm(code, NEW_VA):
        # 相对跳转/相对调用本身就是位置无关的，不算绝对地址
        is_branch = ins.mnemonic == 'call' and ins.operands[0].type == X86_OP_IMM
        is_branch = is_branch or ins.mnemonic.startswith('j')
        if is_branch:
            continue
        for op in ins.operands:
            v = None
            if op.type == X86_OP_IMM:
                v = op.imm & 0xFFFFFFFF
            elif op.type == X86_OP_MEM and op.mem.disp:
                v = op.mem.disp & 0xFFFFFFFF
            if v is not None and 0x10000000 <= v < 0x11000000:
                bad.append((hex(ins.address), ins.mnemonic + ' ' + ins.op_str))
    print('absolute-image operands in cave: %d %s' % (len(bad), bad[:6]))
    assert not bad, 'cave still references absolute image addresses'
    print('[ok] cave is position independent (no absolute image operand)')
    return out, code


if __name__ == '__main__':
    main()
