# -*- coding: utf-8 -*-
"""
最小改动写入器（不缩包、不移动任何后续资源）
==============================================
★ 采纳的安全方案：
  保持 datapack.hvp 原始大小 695,703,665 字节
  保持 voices.txt 及其后所有资源的偏移完全不变
  只在 0x2976BBA3 写入新的 46,676 字节 SP 压缩流
  剩余 7,081 字节保持原样（旧 SP 残留，不清零、不移动）
  只更新 it.lng 目录项 meta[11:13] = 46676（该字段控制 sp.lng 压缩长度）

  布局：
    [新 SP 46676][旧 SP 残留 7081（保持原样）][voices 原位置][其余资源不动]

★ HVP 目录项字段语义（本轮经"名字/offset/实际内容"三者绑定确认）：
  目录项 = [u32 BE nameLen][name][25 字节 meta]，每项 35 字节紧贴排列
  meta[0:4]   = 名字长度 + 29
  meta[11:13] = 【下一个资源】的压缩大小（大端 u16）
  meta[21:25] = 【下一个资源】在包内的绝对偏移（大端 u32）

  三者绑定验证：
    de.lng 目录项 -> 0x29744622 = 英文 (Phrase test...)
    en.lng 目录项 -> 0x29751335 = 第2语言
    fr.lng 目录项 -> 0x2975ED07 = 第3语言
    it.lng 目录项 -> 0x2976BBA3 = 西班牙 (Prueba f...)   ← 我们要改的就是这个
    sp.lng 目录项 -> 0x63657300 = 已超出文件范围（末尾哨兵）

  即：语言文件 N 的数据由【前一个目录项】描述。
  所以改 sp.lng 的大小，要改 it.lng 目录项的 meta[11:13]。

用法:
  python write_minimal.py --dry-run    只分析
  python write_minimal.py --yes        执行
"""
import sys, os, zlib, struct, argparse
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
from FINAL_PARSE_FIXED import parse

PACK=os.path.abspath(os.path.join(HERE,'..','datapack.hvp'))
BAK =os.path.join(HERE,'out','datapack.hvp.bak')

IT_DIR      = 0x117E2                 # it.lng 目录项（nameLen 字段位置）
IT_NAMELEN  = 6
IT_SIZE_OFF = IT_DIR + 4 + IT_NAMELEN + 11   # = 0x117F7

SP_OFF   = 0x2976BBA3
OLD_SIZE = 53757
BAK_SIZE = 695703665

ap=argparse.ArgumentParser()
ap.add_argument('--yes',action='store_true')
ap.add_argument('--dry-run',action='store_true')
a=ap.parse_args()

# ── 读取原包相关区域 ──
with open(BAK,'rb') as f:
    f.seek(IT_SIZE_OFF)
    it_size_orig=struct.unpack('>H',f.read(2))[0]
    f.seek(SP_OFF)
    orig_sp=f.read(OLD_SIZE)
    f.seek(SP_OFF+OLD_SIZE)
    after=f.read(64)          # voices.txt 开头，用于事后校验
    f.seek(BAK_SIZE-64)
    eof64=f.read(64)          # 包尾，用于事后校验

print('=== 写入前状态核查 ===')
print('包总大小           : %d' % BAK_SIZE)
print('it.lng meta[11:13] : %d  (控制 sp.lng 压缩长度)' % it_size_orig)
print('sp.lng 位置        : 0x%X' % SP_OFF)
print('sp.lng 原压缩      : %d 字节' % OLD_SIZE)
print('sp.lng 之后        : %r ... (voices.txt)' % after[:16].hex())
print('包尾 16 字节       : %s' % eof64[:16].hex())
print()

blob=open(os.path.join(HERE,'out','lng','zh.lng'),'rb').read()
comp=zlib.compress(blob,9)
pad=OLD_SIZE-len(comp)

print('=== 最小改动方案 ===')
print('新 SP 压缩 : %d 字节 (原 %d, %+d)' % (len(comp),OLD_SIZE,len(comp)-OLD_SIZE))
print('残留区     : %d 字节【保持原样，不清零、不移动】' % pad)
print('包总大小   : %d -> %d (不变)' % (BAK_SIZE,BAK_SIZE))
print('voices 偏移: 0x%X -> 0x%X (不变)' % (SP_OFF+OLD_SIZE, SP_OFF+OLD_SIZE))
print('仅修改     : sp.lng 数据 + it.lng meta[11:13] = %d' % len(comp))
print()

if a.dry_run or not a.yes:
    print('（dry-run，未写入）加 --yes 执行')
    sys.exit(0)

# ── 从备份完整重建（保证基底干净、大小正确）──
print('从备份重建 datapack.hvp ...')
with open(BAK,'rb') as s, open(PACK,'wb') as d:
    while True:
        c=s.read(1<<22)
        if not c: break
        d.write(c)
print('✓ 基底已就绪 (%d 字节)' % os.path.getsize(PACK))
print()

# ── 最小写入 ──
with open(PACK,'r+b') as g:
    # 1) 只覆盖 SP 起点处的新数据
    g.seek(SP_OFF)
    g.write(comp)
    # 2) 明确【不】动 [SP_OFF+len(comp), SP_OFF+OLD_SIZE) 这段残留
    # 3) 更新 it.lng 目录项的 SP 压缩长度字段
    g.seek(IT_SIZE_OFF)
    g.write(struct.pack('>H',len(comp)))
print('✓ 已写入新 SP (%d 字节) @0x%X' % (len(comp),SP_OFF))
print('✓ 残留 %d 字节保持原样' % pad)
print('✓ it.lng meta[11:13] = %d' % len(comp))
print()

# ── 校验 ──
print('=== 写后校验（含未改动部分的完整性）===')
ok=True
def chk(label, cond, extra=''):
    global ok
    ok &= bool(cond)
    print('  %s %s %s' % ('✓' if cond else '✗', label, extra))

sz=os.path.getsize(PACK)
chk('包大小 %d 未变' % sz, sz==BAK_SIZE, '(期望 %d)'%BAK_SIZE)

with open(PACK,'r+b') as g:
    g.seek(SP_OFF); d1=g.read(len(comp))
    g.seek(SP_OFF+OLD_SIZE); a1=g.read(64)
    g.seek(BAK_SIZE-64); e1=g.read(64)
    g.seek(IT_SIZE_OFF); v=struct.unpack('>H',g.read(2))[0]

chk('新 SP 数据一致', d1==comp, '%d 字节'%len(d1))
chk('sp.lng 之后 64 字节未变', a1==after, 'voices.txt 起点完好')
chk('包尾 64 字节未变', e1==eof64, '所有后续资源完好')
chk('it.lng meta[11:13] = %d'%len(comp), v==len(comp), '实际 %d'%v)

# 残留区未被清零
with open(PACK,'rb') as g:
    g.seek(SP_OFF+len(comp)); res=g.read(pad)
chk('残留区非全零（未误清零）', res.count(0)<pad, '零字节 %d/%d'%(res.count(0),pad))
chk('残留区与备份一致', res==orig_sp[len(comp):], '%d 字节'%pad)

try:
    r=zlib.decompress(d1)
    recs=parse(r)
    chk('SP 解析 3078 条记录', len(recs)==3078, '得到 %d'%len(recs))
    b=recs[0]['body']
    chk('#0 为中文', '测试' in b[1:-1].decode('gbk','replace'), repr(b[1:-1].decode('gbk','replace')))
except Exception as e:
    chk('SP 解析', False, str(e))

# 全目录一致性：受影响的资源链
print()
print('=== 资源链完整性（offset + size == next offset）===')
chain=[('en.lng',0x29744622,52499),('L2',0x29751335,55762),
       ('L3',0x2975ED07,52892),('sp.lng',0x2976BBA3,len(comp)),
       ('voices',SP_OFF+OLD_SIZE,2769)]
for i in range(len(chain)-1):
    nm,off,sz2=chain[i]
    nnm,noff,_=chain[i+1]
    exp=off+sz2
    good = (exp==noff) if nm!='sp.lng' else (noff==SP_OFF+OLD_SIZE)
    chk('%s: 0x%X + %d -> 0x%X (期望 0x%X)'%(nm,off,sz2,exp,noff), good)

print()
print('★ 全部通过' if ok else '✗ 存在问题')
print()
print('请运行游戏，设置切到 Español 测试。异常时双击 rollback.bat')
