#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_cave_drawscope.py  ·  2026-10-26
===============================================================
CAVE 改造一期：DrawPrimitive 绘制期「作用域换纹理」

问题（已在源码/反汇编层面取证）
  SetTexture 钩子在引擎绑定字体纹理时就把 stage0 换成 bigTex，
  于是「实际 stage0」!= 「引擎缓存的 stage0」。
  Reset / StateBlock::Apply 会把 stage0 还原成引擎自己的字体纹理，
  此后若引擎不再重绑（实测 Reset 后 SetTexture 调用 = 0），
  文字就用回原版小图集（变小/乱码），且残留 bigTex 会被背景 draw 采样（竖条）。

本补丁做什么（纯增量，不改任何既有逻辑）
  把 vtable[81] DrawPrimitive 的钩子从 Hook_DP 换成 Hook_DP_S：
    · 入口先 TestCooperativeLevel（c41 教训：设备丢失期禁止查询），不 OK 直接放行
    · GetTexture(0) 读实际 stage0（读后立即 Release，c81 教训：配对释放）
    · 若 stage0 == g_fontTex（= 引擎此刻要的是字体）：
        SetTexture(0, g_bigTex) → 调原 DrawPrimitive → SetTexture(0, g_fontTex)
      即「只为这一次绘制替换纹理，画完立刻还原」
    · 其它情况一律原样透传（等价于原 Hook_DP 的放行路径）
  结果：绘制结束后 stage0 恒等于引擎意图，闪帧的物理来源被消掉。

安全边界
  · 只挂 DrawPrimitive（源码实测 UI/菜单走 DP，DIP 属 3D），爆炸半径最小
  · 不触碰 SetTexture 钩子、指纹库、Clear 遮罩、Reset 逻辑
  · 最坏情况退化为「多 3 次 COM 调用后放行」，不会改变现有渲染结果

实现约束
  · 本机无编译器 → 手写机器码 + 新增 PE 节承载
  · 沿用既有 cave 的绝对地址约定（ImageBase 0x10000000 固定，与现网一致）
  · 全部改动必须落在白名单区间，否则拒绝产出
"""
import struct, sys, hashlib, os

SRC = r'D:\SteamLibrary\steamapps\common\Obscure\version.dll'
OUTDIR = r'E:\ObsCure汉化项目_v1.00_完整交付包_2026-10-18\05_hook源码与译文\hook_cave_ds_2026-10-26'
OUT = os.path.join(OUTDIR, 'version_c93e_cave_ds.dll')

EXPECT_SRC_MD5 = '0c17dfc07b666255b04652dbf0cbe541'   # 封存的稳定保底版

IMAGE_BASE   = 0x10000000
NEW_VA       = 0x1039E000
FILE_ALIGN   = 0x200
SECT_ALIGN   = 0x1000

G_FONTTEX    = 0x10041A70
G_BIGTEX     = 0x10041A74
G_REF_SETTEX = 0x100419C4
G_ORIG_DP    = 0x100419E8
G_DRAWCNT_DP = 0x10048C64

ATTACH_FO    = 0x1BBF          # push imm32 operand of attach_dev(dev,81,...)
HOOK_DP_OLD  = 0x10005750
HOOK_DP_NEW  = NEW_VA
VT_TESTCOOP  = 3               # IDirect3DDevice9
VT_GETTEX    = 64
VT_SETTEX    = 65              # 仅作注释；实际用 g_ref_SetTexture 直调


# ---------------------------------------------------------------- 迷你汇编器
class Asm:
    def __init__(self, base):
        self.base = base
        self.code = bytearray()
        self.labels = {}
        self.fixups = []      # (imm_off, kind, label)

    def at(self):
        return len(self.code)

    def va(self, off):
        return self.base + off

    def b(self, *bs):
        for x in bs:
            self.code.append(x & 0xFF)

    def raw(self, s):
        self.code += s

    def label(self, name):
        assert name not in self.labels, 'dup label %s' % name
        self.labels[name] = len(self.code)

    def jcc(self, opcode2, label):          # 0F 8x rel32
        self.b(0x0F, opcode2)
        self.fixups.append((len(self.code), 'rel32', label))
        self.b(0, 0, 0, 0)

    def jmp(self, label):
        self.b(0xE9)
        self.fixups.append((len(self.code), 'rel32', label))
        self.b(0, 0, 0, 0)

    def call(self, label):
        self.b(0xE8)
        self.fixups.append((len(self.code), 'rel32', label))
        self.b(0, 0, 0, 0)

    def resolve(self):
        for off, kind, label in self.fixups:
            assert label in self.labels, 'undefined label %s' % label
            target = self.base + self.labels[label]
            rel = target - (self.base + off + 4)
            struct.pack_into('<i', self.code, off, rel)


def build_code():
    a = Asm(NEW_VA)

    # ---- 序言
    a.raw(b'\x55')                     # push ebp
    a.raw(b'\x89\xE5')                 # mov ebp, esp
    a.raw(b'\x53')                     # push ebx
    a.raw(b'\x56')                     # push esi
    a.raw(b'\x57')                     # push edi
    a.raw(b'\x83\xEC\x10')             # sub esp, 0x10   (local: [ebp-0x14])

    # ---- 保留原 Hook_DP 的绘制计数（诊断用）
    a.raw(b'\xFF\x05' + struct.pack('<I', G_DRAWCNT_DP))   # inc dword [g_drawCntDP]

    # ---- 门禁：三个指针必须就绪
    a.raw(b'\xA1' + struct.pack('<I', G_FONTTEX))          # mov eax, [g_fontTex]
    a.raw(b'\x85\xC0')                                     # test eax,eax
    a.jcc(0x84, 'pass')                                    # je pass
    a.raw(b'\xA1' + struct.pack('<I', G_BIGTEX))           # mov eax, [g_bigTex]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x8B\x1D' + struct.pack('<I', G_REF_SETTEX))   # mov ebx, [g_ref_SetTexture]
    a.raw(b'\x85\xDB')                                     # test ebx,ebx
    a.jcc(0x84, 'pass')

    # ---- this / vtable
    a.raw(b'\x8B\x75\x08')             # mov esi, [ebp+8]      ; this (device)
    a.raw(b'\x8B\x06')                 # mov eax, [esi]        ; vtable
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')

    # ---- TestCooperativeLevel（设备丢失期绝不查询纹理）
    a.raw(b'\x8B\x40\x0C')             # mov eax, [eax+3*4]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x56')                     # push esi
    a.raw(b'\xFF\xD0')                 # call eax
    a.raw(b'\x85\xC0')                 # test eax,eax
    a.jcc(0x85, 'pass')                # jne pass  (非 S_OK 一律放行)

    # ---- GetTexture(0, &local)
    a.raw(b'\x8B\x06')                                     # mov eax,[esi]
    a.raw(b'\x8B\x80' + struct.pack('<I', VT_GETTEX * 4))  # mov eax,[eax+64*4]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'pass')
    a.raw(b'\x8D\x4D\xEC')             # lea ecx,[ebp-0x14]
    a.raw(b'\x51')                     # push ecx
    a.raw(b'\x6A\x00')                 # push 0
    a.raw(b'\x56')                     # push esi
    a.raw(b'\xFF\xD0')                 # call eax
    a.raw(b'\x85\xC0')
    a.jcc(0x88, 'pass')                # js pass  (失败 -> 不写 local, 不碰设备)

    a.raw(b'\x8B\x7D\xEC')             # mov edi,[ebp-0x14]    ; tex0
    a.raw(b'\xA1' + struct.pack('<I', G_FONTTEX))          # mov eax,[g_fontTex]
    a.raw(b'\x3B\xF8')                 # cmp edi,eax
    a.jcc(0x85, 'relpass')             # jne relpass

    # ---- stage0 就是字体纹理：本帧这一次绘制换成大图集
    a.call('rel')                      # 先放掉 GetTexture 拿到的引用
    a.raw(b'\xA1' + struct.pack('<I', G_BIGTEX))           # mov eax,[g_bigTex]
    a.raw(b'\x50')                     # push eax
    a.raw(b'\x6A\x00')                 # push 0
    a.raw(b'\x56')                     # push esi
    a.raw(b'\xFF\xD3')                 # call ebx   (orig SetTexture)
    a.raw(b'\xFF\x75\x14')             # push dword [ebp+0x14]  count
    a.raw(b'\xFF\x75\x10')             # push dword [ebp+0x10]  start
    a.raw(b'\xFF\x75\x0C')             # push dword [ebp+0x0C]  type
    a.raw(b'\xFF\x75\x08')             # push dword [ebp+8]     this
    a.raw(b'\xFF\x15' + struct.pack('<I', G_ORIG_DP))      # call [g_orig_DP]
    a.raw(b'\xA1' + struct.pack('<I', G_FONTTEX))          # mov eax,[g_fontTex]
    a.raw(b'\x50')                     # push eax
    a.raw(b'\x6A\x00')                 # push 0
    a.raw(b'\x56')                     # push esi
    a.raw(b'\xFF\xD3')                 # call ebx   (还原字体纹理)
    a.jmp('done')

    # ---- 非字体纹理：放掉引用后原样透传
    a.label('relpass')
    a.call('rel')
    a.jmp('pass')

    # ---- 子程序：Release(edi)，edi=0 时安全跳过
    a.label('rel')
    a.raw(b'\x85\xFF')                 # test edi,edi
    a.jcc(0x84, 'relret')
    a.raw(b'\x8B\x07')                 # mov eax,[edi]
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'relret')
    a.raw(b'\x8B\x40\x08')             # mov eax,[eax+2*4]  ; Release
    a.raw(b'\x85\xC0')
    a.jcc(0x84, 'relret')
    a.raw(b'\x57')                     # push edi
    a.raw(b'\xFF\xD0')                 # call eax
    a.label('relret')
    a.raw(b'\xC3')                     # ret

    # ---- 透传路径
    a.label('pass')
    a.raw(b'\xFF\x75\x14')
    a.raw(b'\xFF\x75\x10')
    a.raw(b'\xFF\x75\x0C')
    a.raw(b'\xFF\x75\x08')
    a.raw(b'\xFF\x15' + struct.pack('<I', G_ORIG_DP))

    a.label('done')
    a.raw(b'\x83\xC4\x10')             # add esp,0x10
    a.raw(b'\x5F')                     # pop edi
    a.raw(b'\x5E')                     # pop esi
    a.raw(b'\x5B')                     # pop ebx
    a.raw(b'\x5D')                     # pop ebp
    a.raw(b'\xC2\x10\x00')             # ret 0x10

    a.resolve()
    return bytes(a.code), a


def main():
    src = open(SRC, 'rb').read()
    md5 = hashlib.md5(src).hexdigest()
    print('source      : %s' % SRC)
    print('source md5  : %s' % md5)
    assert md5 == EXPECT_SRC_MD5, 'source is not the sealed baseline (expected %s)' % EXPECT_SRC_MD5

    pe = struct.unpack_from('<I', src, 0x3C)[0]
    nsec = struct.unpack_from('<H', src, pe + 6)[0]
    optsz = struct.unpack_from('<H', src, pe + 20)[0]
    sizeofimage = struct.unpack_from('<I', src, pe + 24 + 56)[0]
    sizeofheaders = struct.unpack_from('<I', src, pe + 24 + 60)[0]
    first_sec = pe + 24 + optsz
    sec_tbl_end = first_sec + nsec * 40
    print('sections    : %d, table ends 0x%X, headers 0x%X (slack %d)'
          % (nsec, sec_tbl_end, sizeofheaders, sizeofheaders - sec_tbl_end))
    assert sizeofheaders - sec_tbl_end >= 40, 'no room for one more section header'
    assert sizeofimage == 0x39E000, 'unexpected SizeOfImage 0x%X' % sizeofimage

    code, asm = build_code()
    print('cave size   : %d bytes (VA 0x%X)' % (len(code), NEW_VA))
    assert len(code) < 0x400

    # ---------- 组装新文件
    out = bytearray(src)
    raw_size = (len(code) + FILE_ALIGN - 1) // FILE_ALIGN * FILE_ALIGN
    new_fo = (len(out) + FILE_ALIGN - 1) // FILE_ALIGN * FILE_ALIGN
    assert new_fo == len(src), 'file size not aligned as expected'
    out += code + b'\x00' * (raw_size - len(code))

    # 节头
    hdr = bytearray(40)
    hdr[0:8] = b'.cf10\x00\x00\x00'
    struct.pack_into('<I', hdr, 8,  len(code))        # VirtualSize
    struct.pack_into('<I', hdr, 12, NEW_VA - IMAGE_BASE)  # VirtualAddress (RVA)
    struct.pack_into('<I', hdr, 16, raw_size)         # SizeOfRawData
    struct.pack_into('<I', hdr, 20, new_fo)           # PointerToRawData
    struct.pack_into('<I', hdr, 36, 0x60000020)       # CNT_CODE|EXEC|READ
    out[sec_tbl_end:sec_tbl_end + 40] = hdr

    struct.pack_into('<H', out, pe + 6, nsec + 1)                    # NumberOfSections
    new_image = sizeofimage + ((len(code) + SECT_ALIGN - 1) // SECT_ALIGN) * SECT_ALIGN
    struct.pack_into('<I', out, pe + 24 + 56, new_image)             # SizeOfImage
    struct.pack_into('<I', out, pe + 24 + 4,                         # SizeOfCode += raw
                     struct.unpack_from('<I', out, pe + 24 + 4)[0] + raw_size)

    # 重定向 vtable 挂载点
    cur = struct.unpack_from('<I', out, ATTACH_FO)[0]
    assert cur == HOOK_DP_OLD, 'attach imm32 = 0x%X, expected 0x%X' % (cur, HOOK_DP_OLD)
    struct.pack_into('<I', out, ATTACH_FO, HOOK_DP_NEW)

    out = bytes(out)
    os.makedirs(OUTDIR, exist_ok=True)
    open(OUT, 'wb').write(out)
    print('written     : %s' % OUT)
    print('out size    : %d (was %d, +%d)' % (len(out), len(src), len(out) - len(src)))
    print('out md5     : %s' % hashlib.md5(out).hexdigest())
    print('SizeOfImage : 0x%X -> 0x%X' % (sizeofimage, new_image))

    # ---------- 白名单差分审计
    diff = [i for i in range(len(src)) if src[i] != out[i]]
    allowed = set(range(first_sec + (nsec - 1) * 40 + 40, first_sec + nsec * 40 + 40))  # 新节头
    allowed |= set(range(ATTACH_FO, ATTACH_FO + 4))                                      # 挂载点
    allowed |= {pe + 6, pe + 7}                                                          # NumberOfSections
    allowed |= set(range(pe + 24 + 56, pe + 24 + 60))                                    # SizeOfImage
    allowed |= set(range(pe + 24 + 4, pe + 24 + 8))                                      # SizeOfCode
    stray = [i for i in diff if i < len(src) and i not in allowed]
    print('diff bytes  : %d total, %d outside whitelist' % (len(diff), len(stray)))
    for i in stray[:20]:
        print('   stray 0x%X: 0x%02X -> 0x%02X' % (i, src[i], out[i]))
    assert not stray, 'unexpected modification outside whitelist'
    assert len(out) > len(src)
    print('[ok] diff confined to whitelist + appended section')
    return out, code, asm


if __name__ == '__main__':
    main()
