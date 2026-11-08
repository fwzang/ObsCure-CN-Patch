# -*- coding: utf-8 -*-
"""日志时间对齐观察器
每 1 秒采样一次 x_hook.log 的新增内容，并打上墙钟时间戳。
用于把"用户的操作时刻"和"日志事件"精确对齐。

输出: hzp_tools/out/watch/timeline.log
用法: python hzp_tools/log_watch.py            # Ctrl+C 结束
      python hzp_tools/log_watch.py --minutes 10
"""
import os, re, sys, time, argparse

BASE = r'E:\SteamLibrary\steamapps\common\Obscure\hzp_tools\out'
LOG  = os.path.join(BASE, 'x_hook.log')
OUTD = os.path.join(BASE, 'watch')
OUT  = os.path.join(OUTD, 'timeline.log')
STOP = os.path.join(BASE, 'log_watch_stop.flag')

os.makedirs(OUTD, exist_ok=True)

# 关心的关键事件（其余噪音丢弃）
KEEP = re.compile(
    r'\[(RESET|3B|PATCH|SWAP#?|FONT-CONFIRM|FONT-OTHER|WD-B0|HEAL|STATE|D6-FIRST|CAND|CT)\]')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=int, default=0)
    a = ap.parse_args()

    if os.path.exists(OUT):
        os.remove(OUT)
    if os.path.exists(STOP):
        os.remove(STOP)

    pos = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    frame = 0
    t0 = time.time()
    last_beat = 0

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('# 时间对齐观察器启动  起始偏移=%d\n' % pos)
        f.write('# 格式: [墙钟] 事件\n')
        f.flush()
        while True:
            time.sleep(1.0)
            if a.minutes and (time.time() - t0) > a.minutes * 60:
                f.write('[%s] === 观察器到期退出 ===\n' % time.strftime('%H:%M:%S')); break
            if os.path.exists(STOP):
                f.write('[%s] === stop flag，退出 ===\n' % time.strftime('%H:%M:%S')); break

            sz = os.path.getsize(LOG) if os.path.exists(LOG) else 0
            if sz < pos:
                pos = 0                     # 日志被轮换
            if sz == pos:
                # 心跳：每 10 秒报一次（含当前帧号），便于确认观察器活着
                if time.time() - last_beat >= 10:
                    last_beat = time.time()
                    f.write('[%s] ...心跳 f≈%d\n' % (time.strftime('%H:%M:%S'), frame))
                    f.flush()
                continue

            with open(LOG, 'rb') as g:
                g.seek(pos)
                data = g.read(sz - pos)
            pos = sz
            txt = data.decode('utf-8', 'replace')
            ts = time.strftime('%H:%M:%S')
            for line in txt.splitlines():
                t = line.strip()
                if not t:
                    continue
                m = re.search(r'frame=(\d+)', t)
                if m:
                    frame = int(m.group(1))
                if KEEP.search(t):
                    f.write('[%s] %s\n' % (ts, t[:150]))
                    f.flush()


if __name__ == '__main__':
    main()
