# -*- coding: utf-8 -*-
"""
Obscure 语言文件结构验证器（修正版）
====================================

基于原始 en.lng / sp.lng 的字节级验证结果：

1. 真正固定文件头为 24 字节，不是 25 字节。
2. 每条普通记录：
       [u16 BE L][body: L bytes][meta: 6 bytes]
   其中：
       - L 是大端（big-endian）16 位长度；
       - body[0] 是文本类型：
           0 = 8-bit 文本，body[1:] 以 00 结束
           1 = 宽字符/富文本，body[2:] 为 UTF-16LE 风格数据，
               body[1] 是额外控制字节，末尾 00 00
       - meta 为 6 字节，必须原样保留。
3. 最后一条记录没有 6 字节 meta，body 后直接到 EOF。
4. EN / SP 都应解析为 3078 条记录。
5. parse -> build 必须与原文件逐字节完全相同。

本脚本只做验证，不修改游戏包、不写回 datapack.hvp。
"""

from __future__ import print_function
import os
import sys
import struct
import hashlib
from collections import Counter

HEADER_SIZE = 24
META_SIZE = 6


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def parse(data):
    """完整解析一个 .lng，要求覆盖到 EOF。"""
    if len(data) < HEADER_SIZE:
        raise ValueError("文件太短")

    records = []
    p = HEADER_SIZE
    n = len(data)

    while p < n:
        if p + 2 > n:
            raise ValueError("0x%X: 长度字段不完整" % p)

        # 关键：大端 u16
        L = struct.unpack_from(">H", data, p)[0]
        if L <= 0:
            raise ValueError("0x%X: 非法长度 L=%d" % (p, L))

        body_start = p + 2
        body_end = body_start + L
        if body_end > n:
            raise ValueError(
                "0x%X: body 越界，L=%d，剩余=%d"
                % (p, L, n - body_start)
            )

        body = data[body_start:body_end]
        if not body:
            raise ValueError("0x%X: 空 body" % p)

        text_type = body[0]

        # 最后一条：body 恰好到 EOF，没有 meta
        if body_end == n:
            meta = b""
            next_p = n
        else:
            meta_end = body_end + META_SIZE
            if meta_end > n:
                raise ValueError("0x%X: meta 不完整" % p)
            meta = data[body_end:meta_end]
            next_p = meta_end

        rec = {
            "index": len(records),
            "off": p,
            "L": L,
            "type": text_type,
            "body": body,
            "meta": meta,
            "end": next_p,
        }
        records.append(rec)
        p = next_p

    if p != n:
        raise AssertionError("解析没有精确到 EOF")

    return records


def validate_body(rec):
    """验证已观察到的两种文本 body。"""
    body = rec["body"]
    t = rec["type"]

    if t == 0:
        # 00 + 8-bit 文本 + 00
        return len(body) >= 2 and body[-1:] == b"\x00"

    if t == 1:
        # 01 + 控制字节 + UTF-16LE 数据 + 00 00
        return (
            len(body) >= 4
            and body[-2:] == b"\x00\x00"
            and len(body[2:-2]) % 2 == 0
        )

    return False


def build(header, records):
    """原样重建；用于 round-trip 验证。"""
    out = bytearray(header)

    for rec in records:
        body = rec["body"]
        meta = rec["meta"]

        out += struct.pack(">H", len(body))
        out += body
        out += meta

    return bytes(out)


def decode_preview(rec, limit=100):
    """只用于终端预览，不参与解析。"""
    body = rec["body"]
    t = rec["type"]

    try:
        if t == 0:
            raw = body[1:-1]
            s = raw.decode("latin-1", errors="replace")
        elif t == 1:
            # body[1] 是额外控制字节；正文从 body[2] 起
            raw = body[2:-2]
            s = raw.decode("utf-16le", errors="replace")
        else:
            return "<unknown type %d>" % t
    except Exception as e:
        return "<decode error: %s>" % e

    s = s.replace("\r", "\\r").replace("\n", "\\n")
    if len(s) > limit:
        s = s[:limit] + "..."
    return s


def analyze(path):
    data = open(path, "rb").read()
    records = parse(data)
    rebuilt = build(data[:HEADER_SIZE], records)

    bad = [r for r in records if not validate_body(r)]
    types = Counter(r["type"] for r in records)

    print("=" * 72)
    print("文件:", path)
    print("大小:", len(data))
    print("SHA256:", sha256(data))
    print("固定头:", data[:HEADER_SIZE].hex())
    print("记录数:", len(records))
    print("类型分布:", dict(sorted(types.items())))
    print("body 校验异常:", len(bad))
    print("最后记录 meta 长度:", len(records[-1]["meta"]))
    print("round-trip:", "PASS" if rebuilt == data else "FAIL")
    print("重建 SHA256:", sha256(rebuilt))

    print("\n前 5 条:")
    for r in records[:5]:
        print(
            "#%-4d off=0x%X L=%-4d type=%d meta=%-12s %s"
            % (
                r["index"], r["off"], r["L"], r["type"],
                r["meta"].hex(), decode_preview(r)
            )
        )

    print("\n最后 3 条:")
    for r in records[-3:]:
        print(
            "#%-4d off=0x%X L=%-4d type=%d meta=%-12s %s"
            % (
                r["index"], r["off"], r["L"], r["type"],
                r["meta"].hex(), decode_preview(r)
            )
        )

    if rebuilt != data:
        m = min(len(rebuilt), len(data))
        first = next((i for i in range(m) if rebuilt[i] != data[i]), None)
        print("\n首个差异:", "长度差" if first is None else "0x%X" % first)
        raise SystemExit(2)

    if bad:
        print("\n发现未识别 body 类型/终止格式：")
        for r in bad[:20]:
            print(
                "#%d off=0x%X L=%d type=%d body=%s"
                % (r["index"], r["off"], r["L"], r["type"], r["body"][:32].hex())
            )
        raise SystemExit(3)

    return data, records


def compare(en_records, sp_records):
    print("\n" + "=" * 72)
    print("EN / SP 结构对照")

    print("记录数:",
          len(en_records), "/", len(sp_records),
          "PASS" if len(en_records) == len(sp_records) else "FAIL")

    n = min(len(en_records), len(sp_records))

    meta_diff = [
        i for i in range(n)
        if en_records[i]["meta"] != sp_records[i]["meta"]
    ]
    type_diff = [
        i for i in range(n)
        if en_records[i]["type"] != sp_records[i]["type"]
    ]

    print("逐条 meta 完全相同:",
          "%d/%d" % (n - len(meta_diff), n),
          "PASS" if not meta_diff and len(en_records) == len(sp_records) else "CHECK")
    print("type 不同记录:", len(type_diff),
          type_diff[:20] if type_diff else [])

    if not meta_diff and len(en_records) == len(sp_records):
        print("结论: 两种语言可按 record index / meta 一一对应，不需要依赖字节偏移。")


def main():
    # 优先使用命令行路径；否则寻找脚本同目录下的 en.lng / sp.lng
    here = os.path.dirname(os.path.abspath(__file__))

    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        paths = []
        for name in ("en.lng", "sp.lng"):
            p = os.path.join(here, name)
            if os.path.exists(p):
                paths.append(p)

    if not paths:
        print("用法:")
        print("  python FINAL_PARSE_FIXED.py en.lng sp.lng")
        sys.exit(1)

    results = []
    for path in paths:
        results.append(analyze(path))

    if len(results) >= 2:
        compare(results[0][1], results[1][1])

    print("\n全部验证完成。此脚本没有修改任何输入文件。")


if __name__ == "__main__":
    main()
