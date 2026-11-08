# -*- coding: utf-8 -*-
"""安装器离线验证：payload 完整性 / 组件计划 / 目录探测 / 备份还原往返。
不启动 GUI，不改动真实游戏目录。"""
import os
import sys
import json
import shutil
import zipfile
import hashlib
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REL = os.path.dirname(HERE)
DIST = os.path.join(REL, "dist")
EXE = os.path.join(DIST, "ObsCure汉化安装器_v1.00.exe")

sys.path.insert(0, HERE)
import installer_diff as I

FAIL = []


def ck(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAIL.append(msg)


def md5(p):
    return I.md5_of(p)


def main():
    print("== 1. payload 完整性（从 exe 尾部直接读）==")
    z = zipfile.ZipFile(EXE, "r")
    names = z.namelist()
    ck("INSTALLER_MANIFEST.json" in names, "manifest 在 exe 尾部可读")
    ck(z.testzip() is None, "zip CRC 全部通过")
    mf = json.loads(z.read("INSTALLER_MANIFEST.json").decode("utf-8"))
    ck(mf.get("ver") == "1.00", "manifest 版本 = 1.00")
    ck(mf.get("map_textures") == 33, "manifest 地图贴图数 = 33")
    ck(mf.get("mode") == "diff", "manifest mode = diff（差分安装）")
    ck("datapack.hvp" not in names, "payload 不含 663MB datapack.hvp")
    total = 0
    for arc, meta in mf["files"].items():
        p = os.path.join(REL, "payload", arc.replace("/", os.sep))
        ck(os.path.isfile(p), "payload 文件存在: " + arc)
        if not os.path.isfile(p):
            continue
        got = md5(p)
        ck(got == meta["md5"], "md5 一致: %s (%s)" % (arc, meta["md5"][:12]))
        ck(os.path.getsize(p) == meta["size"], "size 一致: " + arc)
        total += meta["size"]
    ck(z.getinfo("zh_en.lng").file_size == 117522, "zh_en.lng = 117522 B")
    print("  payload 合计: %.2f MB" % (total / 1048576.0))
    print("  exe 合计    : %.2f MB" % (os.path.getsize(EXE) / 1048576.0))

    print("== 2. 关键文件 hash 对齐发布基线 ==")
    exp = {
        "version.dll": "028ec9a88266ba1526aeeabe930c16e6",
        "big_atlas_c.raw": "12879fb35e455dfc154f97c2a4ce805e",
        "table_c.bin": "9cf562f53b48b1be130aa0453f8607b3",
        "zh_en.lng": "ddef75d0998cf3d717375c9c96ca01de",
    }
    for a, h in exp.items():
        ck(mf["files"][a]["md5"] == h, "%s == %s" % (a, h[:16]))

    print("== 3. zh_en.lng 与现装汉化版一致（幂等重建前提）==")
    game = r"E:\SteamLibrary\steamapps\common\Obscure"
    live = os.path.join(REL, "lngsrc", "_ex", "_texts", "en.lng")
    if os.path.isfile(live):
        ck(md5(live) == mf["files"]["zh_en.lng"]["md5"],
           "现装 datapack 内的 en.lng == payload zh_en.lng（重复安装将跳过重建）")

    print("== 4. 组件计划 ==")
    ck(I.DIRECT_FILES_CORE == ["version.dll", "big_atlas_c.raw", "table_c.bin"],
       "汉化核心直写 3 文件 + 重建 datapack = 4 件套")
    ck(I.DIRECT_FILES_WIDE == ["dinput8.dll", "scripts/main.patch", "scripts/obscure.asi"],
       "宽屏补丁 3 文件")
    ck(I.NEW_DP_MIN == 100 * 1024 * 1024, "重建体积下限 100MB（防损坏包写入）")

    print("== 5. Steam 目录探测 ==")
    found = I.detect_steam_dirs()
    print("  探测到:", found)
    ck(game in found, "探测到本机 ObsCure 目录")
    ck(all(I.is_game_dir(d) for d in found), "所有探测结果均含 Obscure.exe")

    print("== 6. 备份 / 还原 往返（沙盒目录）==")
    sb = tempfile.mkdtemp(prefix="instchk_")
    try:
        fake = os.path.join(sb, "Obscure")
        os.makedirs(os.path.join(fake, "scripts"))
        open(os.path.join(fake, "Obscure.exe"), "wb").write(b"MZ-fake")
        origs = {
            "datapack.hvp": b"ORIGINAL-DP" * 100,
            "version.dll": b"ORIGINAL-VD",
            "big_atlas_c.raw": b"ORIG-ATLAS",
            "table_c.bin": b"ORIG-TABLE",
            "dinput8.dll": b"ORIG-DINPUT",
            "scripts/main.patch": b"ORIG-PATCH",
            "scripts/obscure.asi": b"ORIG-ASI",
        }
        for rel, data in origs.items():
            open(os.path.join(fake, rel.replace("/", os.sep)), "wb").write(data)

        inst = I.Installer.__new__(I.Installer)   # 不重读 payload
        inst.z, inst.devdir = z, None
        inst.mf = mf
        logs = []
        inst.ui = type("U", (), {"log": staticmethod(lambda s: logs.append(s)),
                                 "progress": staticmethod(lambda a, b: None),
                                 "status": staticmethod(lambda s: None)})()
        bdir = os.path.join(fake, I.BACKUP_DIR)   # v1.00：无时间戳子目录，直接放文件
        for rel in list(origs):
            src = os.path.join(fake, rel.replace("/", os.sep))
            dst = os.path.join(bdir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        ck(os.path.isfile(os.path.join(bdir, "datapack.hvp")), "备份含 datapack.hvp")
        ck(os.path.isfile(os.path.join(bdir, "scripts", "obscure.asi")), "备份含 scripts/obscure.asi")

        # 模拟安装覆盖
        for a in I.DIRECT_FILES_CORE + I.DIRECT_FILES_WIDE:
            dst = os.path.join(fake, a.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(REL, "payload", a.replace("/", os.sep)), dst)
        ck(md5(os.path.join(fake, "version.dll")) == "028ec9a88266ba1526aeeabe930c16e6",
           "覆盖后 version.dll = 发布基线")
        ck(open(os.path.join(fake, "datapack.hvp"), "rb").read()
           == b"ORIGINAL-DP" * 100,
           "datapack 未被直写（仍为原版，等待 extract/create 重建）")

        ok, msg = inst.uninstall(fake, logs.append)
        ck(ok, "卸载返回成功: " + msg)
        allok = True
        for rel, data in origs.items():
            p = os.path.join(fake, rel.replace("/", os.sep))
            if open(p, "rb").read() != data:
                allok = False
                print("     不一致:", rel)
        ck(allok, "7 个文件全部逐字节还原为原版")
        ck(not os.path.isdir(os.path.join(fake, I.BACKUP_DIR)), "备份目录已清理")
    finally:
        shutil.rmtree(sb, ignore_errors=True)

    print("== 7. 边界校验 ==")
    ck(not I.is_game_dir(os.path.dirname(REL)), "非游戏目录被正确拒绝")
    ck(not I.is_game_dir(""), "空路径被正确拒绝")

    z.close()
    print()
    if FAIL:
        print("✗ %d 项失败" % len(FAIL))
        for m in FAIL:
            print("   -", m)
        return 1
    print("★ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())