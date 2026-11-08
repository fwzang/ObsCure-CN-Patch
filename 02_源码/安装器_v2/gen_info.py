# -*- coding: utf-8 -*-
"""gen_info.py — 把 info_*.txt（UTF-8）转成 C 宽字符串头文件。

用法: python gen_info.py info_o2.txt info_o2.h [变量名]
说明框文字改这里，重新跑 build_all 即可，不用手写转义。
"""
import io
import sys


def esc(line):
    out = []
    for ch in line:
        if ch == '\\':
            out.append('\\\\')
        elif ch == '"':
            out.append('\\"')
        else:
            out.append(ch)
    return ''.join(out)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    var = sys.argv[3] if len(sys.argv) > 3 else 'kInfo'
    text = io.open(src, 'r', encoding='utf-8', newline='').read()
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    body = []
    for i, ln in enumerate(lines):
        tail = '\\r\\n' if i < len(lines) - 1 else ''
        body.append('L"%s%s"' % (esc(ln), tail))
    with io.open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write('/* 自动生成，勿手改；源：%s */\n' % src)
        f.write('static const wchar_t* %s =\n' % var)
        f.write('\n'.join(body))
        f.write(';\n')
    print('%s -> %s (%d 行)' % (src, dst, len(lines)))


if __name__ == '__main__':
    main()
