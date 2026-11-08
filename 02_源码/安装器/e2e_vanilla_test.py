# -*- coding: utf-8 -*-
"""
★ 真·原版安装 E2E（最高置信度测试）

用项目自带的**英文原版** datapack（hzp_tools/out/pristine/datapack_vanilla_20261015.hvp,
md5=23ef769749d5eac0b0109cb62f4a753f, en.lng 149816B 纯英文）作为目标包，
完整跑一遍差分安装，然后：

  1. 断言确实走了 extract → 替换 → create 重建分支
  2. 重建后的 datapack 内 en.lng == payload zh_en.lng（中文真正注入）
  3. 重建包与「当前已装机汉化版」en.lng 完全一致（= 与手工装机等价）
  4. 重建包体积合理，且能被 obscure-hvp 正常再解包
  5. 重复安装幂等
  6. 卸载逐字节还原为英文原版
  7. 真实游戏目录 0 改动

这一步是对「别人拿到安装包能不能用」最有说服力的证据：
原版 → 差分安装 → 结果 == 我们手工验证过的汉化版。
"""
import os
import sys
import json
import shutil
import time
import zipfile
import hashlib
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REL = os.path.dirname(HERE)
EXE = os.path.join(REL, "dist", "ObsCure汉化安装器_v1.00.exe")
GAME = r"E:\SteamLibrary\steamapps\common\Obscure"
VANILLA = r"E:\SteamLibrary\steamapps\common\Obscure\hzp_tools\out\datapack.hvp.bak"
HV = os.path.join(REL, "payload", "obscure-hvp.exe")
LIVE = os.path.join(GAME, "datapack.hvp")

sys.path.insert(0, HERE)
import installer_diff as I

FAIL = []
TMP = []
VAN_MD5 = "23ef769749d5eac0b0109cb62f4a753f"
ZH_MD5 = "ddef75d0998cf3d717375c9c96ca01de"


def ck(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAIL.append(msg)
    return cond


def md5(p):
    return I.md5_of(p)


def run(*a):
    return subprocess.run(list(a), capture_output=True, creationflags=0x08000000)


def lng_of(hvp, out):
    """解包 hvp 并返回 _texts\\en.lng 的 md5。"""
    r = run(HV, "-g", "obscure1", "extract", hvp, out)
    if r.returncode != 0:
        return None
    p = os.path.join(out, "_texts", "en.lng")
    return md5(p) if os.path.isfile(p) else None


def tex_of(hvp, out):
    """解包 hvp 并返回 {贴图相对路径: md5}（_common/maps_pc 下 33 件）。"""
    r = run(HV, "-g", "obscure1", "extract", hvp, out)
    if r.returncode != 0:
        return None
    base = os.path.join(out, "_common", "maps_pc")
    got = {}
    for root, _d, fs in os.walk(base):
        for fn in fs:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, base).replace("\\", "/")
            got[rel] = md5(fp)
    return got


def map_tex_from_manifest(mf):
    return dict((a[len("maps_pc/"):], v["md5"])
                for a, v in mf["files"].items() if a.startswith("maps_pc/"))


def main():
    print("== 0. 前置校验 ==")
    ck(os.path.isfile(VANILLA), "英文原版 datapack 存在")
    ck(md5(VANILLA) == VAN_MD5, "原版 md5 = %s" % VAN_MD5)
    LIVE0 = md5(LIVE)   # v1.00：动态基线（开发机可能处于任意合法状态）
    ck(len(LIVE0) == 32, "现装 datapack 指纹已记录")
    z = zipfile.ZipFile(EXE, "r")
    mf = json.loads(z.read(I.MANIFEST).decode("utf-8"))
    ck(mf["files"]["zh_en.lng"]["md5"] == ZH_MD5, "payload 译文 md5 = %s" % ZH_MD5)
    z.close()

    sb = tempfile.mkdtemp(prefix="obszh_van_")
    TMP.append(sb)
    print("      沙盒: %s" % sb)

    print("== 1. 搭「原版游戏目录」==")
    g = os.path.join(sb, "Obscure")
    os.makedirs(os.path.join(g, "scripts"))
    shutil.copy2(os.path.join(GAME, "Obscure.exe"), os.path.join(g, "Obscure.exe"))
    t0 = time.time()
    shutil.copy2(VANILLA, os.path.join(g, "datapack.hvp"))
    print("      复制原版包 %.1f s" % (time.time() - t0))
    # 宽屏/汉化的其它文件故意缺失 = 模拟"纯净 Steam 安装"
    ck(sorted(os.listdir(g)) == ["Obscure.exe", "datapack.hvp", "scripts"],
       "初始为纯净原版态（仅有 Obscure.exe + datapack.hvp）")

    inst = I.Installer.__new__(I.Installer)
    zz = zipfile.ZipFile(EXE, "r")
    inst.z, inst.devdir = zz, None
    inst.mf = mf
    logs = []
    inst.ui = type("U", (), {"log": staticmethod(logs.append),
                             "progress": staticmethod(lambda a, b: None),
                             "status": staticmethod(lambda s: print("      · " + s))})()

    print("== 2. 执行差分安装（汉化核心 + 宽屏 + 备份）==")
    t0 = time.time()
    ok, msg, bdir = inst.install(g, True, True, True, sb)
    dt = time.time() - t0
    ck(ok, "install 成功（%.1f s）：%s" % (dt, msg))
    if not ok:
        print("      日志:"); [print("       |", l) for l in logs]
        return 1

    print("== 3. 分支断言：必须是「重建」而非幂等跳过 ==")
    j = "\n".join(logs)
    ck("替换中文译文" in j, "日志含「替换中文译文 en.lng」")
    ck(("替换地图贴图 30/33" in j) or ("替换地图贴图 33/33" in j),
       "日志含「替换地图贴图」（30/33=有3件中英原件相同无需替换）")
    ck("重建 datapack" in j, "日志含「重建 datapack」")
    ck("跳过重建" not in j, "日志**不含**「跳过重建」")

    print("== 4. 4 件套 + 宽屏 3 件 md5 ==")
    for a in ["version.dll", "big_atlas_c.raw", "table_c.bin",
              "dinput8.dll", "scripts/obscure.asi", "scripts/main.patch"]:
        p = os.path.join(g, a.replace("/", os.sep))
        ck(os.path.isfile(p) and md5(p) == mf["files"][a]["md5"], "%-22s = 发布基线" % a)

    print("== 5. ★ 核心判定：重建包内译文 == 汉化译文 ==")
    got = lng_of(os.path.join(g, "datapack.hvp"), os.path.join(sb, "chkA"))
    ck(got == ZH_MD5, "重建包 en.lng md5 = %s（中文已注入）" % (got or "None"))
    ck(got != "022510f5beaae5f272ab8cecb4fce510", "不再是英文原版译文 022510f5…")
    nsz = os.path.getsize(os.path.join(g, "datapack.hvp"))
    vsz = os.path.getsize(VANILLA)
    ck(nsz > 100 * 1024 * 1024, "重建包体积正常 %d B" % nsz)
    print("      原版 %d -> 重建 %d B（差 %+d）" % (vsz, nsz, nsz - vsz))

    print("== 6. ★ 等价性：与手工装机的汉化版逐字节对比 en.lng + 33 贴图 ==")
    live_lng = lng_of(LIVE, os.path.join(sb, "chkB"))
    ck(got == live_lng, "差分安装结果 == 当前已验收装机版（%s）" % live_lng)
    want_tex = map_tex_from_manifest(mf)
    got_tex = tex_of(os.path.join(g, "datapack.hvp"), os.path.join(sb, "chkTA"))
    hit = [k for k in want_tex if got_tex and k in got_tex]
    ck(len(hit) == 33,
       "重建包含 33 件地图贴图（实际命中 %d）" % len(hit))
    if got_tex:
        bad = [k for k in want_tex if got_tex.get(k) != want_tex[k]]
        ck(not bad, "33 件贴图 md5 全部 = 中文基线（不一致 %s）" % (bad or "无"))
    live_tex = tex_of(LIVE, os.path.join(sb, "chkTB"))
    ck(live_tex is not None and all(live_tex.get(k) == v for k, v in want_tex.items()),
       "33 件贴图 == 当前已验收装机版")

    print("== 7. 幂等：再装一次应跳过重建 ==")
    mt = os.path.getmtime(os.path.join(g, "datapack.hvp"))
    time.sleep(1.1)
    ok2, msg2, _ = inst.install(g, True, True, True, sb)
    ck(ok2, "二次安装成功：" + msg2)
    ck(any("跳过重建" in l for l in logs), "日志含「跳过重建」")
    ck(os.path.getmtime(os.path.join(g, "datapack.hvp")) == mt, "datapack 未被重复重建")

    print("== 8. 卸载 → 逐字节还原为英文原版 ==")
    okU, msgU = inst.uninstall(g, logs.append)
    ck(okU, "uninstall 成功：" + msgU)
    ck(md5(os.path.join(g, "datapack.hvp")) == VAN_MD5,
       "datapack.hvp md5 回到原版 %s" % VAN_MD5)
    back = lng_of(os.path.join(g, "datapack.hvp"), os.path.join(sb, "chkC"))
    ck(back == "022510f5beaae5f272ab8cecb4fce510", "包内 en.lng 回到英文原版 022510f5…")
    for a in ["version.dll", "big_atlas_c.raw", "table_c.bin",
              "dinput8.dll", "scripts/obscure.asi", "scripts/main.patch"]:
        ck(not os.path.isfile(os.path.join(g, a.replace("/", os.sep))),
           "%s 已移除（原本不存在）" % a)

    print("== 9. 真实游戏目录零改动 ==")
    ck(md5(LIVE) == LIVE0, "现装 datapack 未变（动态基线）")
    ck(md5(os.path.join(GAME, "version.dll")) == "028ec9a88266ba1526aeeabe930c16e6",
       "现装 version.dll 未变")
    ck(md5(VANILLA) == VAN_MD5, "原版基准包未被测试污染")

    zz.close()
    print("== 10. 关键日志 ==")
    for l in logs:
        if any(k in l for k in ("备份", "解包", "替换", "重建", "跳过", "新数据包", "写入", "校验")):
            print("      |", l)

    print()
    if FAIL:
        print("✗ %d 项失败" % len(FAIL))
        for m in FAIL:
            print("   -", m)
        return 1
    print("★★ 真·原版差分安装 E2E 全部通过：他人拿到安装包可直接用")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for d in TMP:
            shutil.rmtree(d, ignore_errors=True)
    sys.exit(rc)