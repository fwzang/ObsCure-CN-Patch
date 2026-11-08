# -*- coding: utf-8 -*-
"""全包逐块对比：新包 vs 备份，列出所有差异区段"""
import sys, os
PACK='../datapack.hvp'; BAK='out/datapack.hvp.bak'
CH=1<<22
f1=open(BAK,'rb'); f2=open(PACK,'rb')
s1=os.path.getsize(BAK); s2=os.path.getsize(PACK)
print('大小: 备份 %d / 新包 %d  %s' % (s1,s2,'✓相同' if s1==s2 else '✗不同'))
print()
diffs=[]; off=0
while True:
    a=f1.read(CH); b=f2.read(CH)
    if not a and not b: break
    L=min(len(a),len(b))
    for i in range(L):
        if a[i]!=b[i]:
            diffs.append(off+i)
            # 跳过该字节
    off+=L
    if not a or not b: break
f1.close(); f2.close()
if not diffs:
    print('✓ 两包完全相同')
else:
    # 压缩成区段
    segs=[]; st=diffs[0]; pv=diffs[0]
    for x in diffs[1:]:
        if x-pv>64:
            segs.append((st,pv)); st=x
        pv=x
    segs.append((st,pv))
    print('差异区段 %d 个（前 20）:' % len(segs))
    for a,b in segs[:20]:
        print('  0x%08X - 0x%08X  (%d 字节)' % (a,b,b-a+1))
    print()
    print('总差异字节: %d' % len(diffs))
    lo=min(diffs); hi=max(diffs)
    print('差异范围: 0x%X .. 0x%X' % (lo,hi))
    print('  sp.lng 区域 0x2976BBA3..0x%X' % (0x2976BBA3+53757))
    print('  目录项区域 0x117F7 (1 字节)')
