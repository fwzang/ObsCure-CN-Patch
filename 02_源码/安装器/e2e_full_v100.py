# -*- coding: utf-8 -*-
"""加强版 v1.00 E2E：Steam 原版 → ODE 层 + 汉化（文本+33贴图）→ 幂等 → 卸载还原。"""
import os, sys, time, json, shutil, zipfile, tempfile, hashlib, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = r"E:/SteamLibrary/steamapps/common/Obscure"
EXE = os.path.join(HERE, "dist", "ObsCure加强汉化版安装器_v1.00.exe")
HV = os.path.join(HERE, "payload_full", "obscure-hvp.exe")
VANILLA = os.path.join(GAME, "hzp_tools", "out", "datapack.hvp.bak")
CACH_VAN = os.path.join(GAME, "cachpack.hvp")   # 当前游戏 cachpack = 原版 cb5e4c66
KINE_VAN = os.path.join(GAME, "kinepack.hvp")   # 项目从未改过 kinepack = 原版
VAN_MD5 = "23ef769749d5eac0b0109cb62f4a753f"
CACH_MD5 = "cb5e4c66046a57354fcd55e515b4813c"
ZH_MD5 = "ddef75d0998cf3d717375c9c96ca01de"
TMP = []

def ck(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILS.append(msg)
FAILS = []

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b: break
            h.update(b)
    return h.hexdigest()

def run(*a):
    return subprocess.run(list(a), capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

def lng_of(hvp, out):
    r = run(HV, "-g", "obscure1", "extract", hvp, out)
    p = os.path.join(out, "_texts", "en.lng")
    return md5(p) if (r.returncode == 0 and os.path.isfile(p)) else None

def tex_of(hvp, out):
    r = run(HV, "-g", "obscure1", "extract", hvp, out)
    if r.returncode != 0: return None
    base = os.path.join(out, "_common", "maps_pc")
    got = {}
    if os.path.isdir(base):
        for root, _d, fs in os.walk(base):
            for fn in fs:
                fp = os.path.join(root, fn)
                got[os.path.relpath(fp, base).replace("\\", "/")] = md5(fp)
    return got

def main():
    print("== 0. 前置校验 ==")
    ck(md5(VANILLA) == VAN_MD5, "原版 datapack = %s" % VAN_MD5[:8])
    ck(md5(CACH_VAN) == CACH_MD5, "cachpack 原版 = %s" % CACH_MD5[:8])
    z = zipfile.ZipFile(EXE, "r")
    mf = json.loads(z.read("INSTALLER_MANIFEST.json").decode("utf-8"))
    ck(mf["ver"] == "1.00", "manifest ver = 1.00")
    ck(mf["mode"] == "full", "manifest mode = full")
    ck(mf["map_textures"] == 33, "manifest 地图贴图 = 33")
    want_tex = dict((a[len("maps_pc/"):], v["md5"])
                    for a, v in mf["files"].items() if a.startswith("maps_pc/"))
    ck(len(want_tex) == 33, "payload 含 33 件贴图")
    real_fp = {f: md5(os.path.join(GAME, f)) for f in
               ["datapack.hvp", "cachpack.hvp", "kinepack.hvp"]}

    sb = tempfile.mkdtemp(prefix="obsfull_"); TMP.append(sb)
    print("      沙盒: %s" % sb)
    g = os.path.join(sb, "Obscure")
    os.makedirs(os.path.join(g, "scripts"))
    shutil.copy2(os.path.join(GAME, "Obscure.exe"), os.path.join(g, "Obscure.exe"))
    print("== 1. 搭建 Steam 原版沙盒（dp+cach+kine，约 2.8GB IO）==")
    t0 = time.time()
    shutil.copy2(VANILLA, os.path.join(g, "datapack.hvp"))
    shutil.copy2(CACH_VAN, os.path.join(g, "cachpack.hvp"))
    shutil.copy2(KINE_VAN, os.path.join(g, "kinepack.hvp"))
    print("      复制完成 %.1f s" % (time.time() - t0))

    inst = I.Installer.__new__(I.Installer)
    inst.z, inst.devdir = z, None
    inst.mf = mf
    logs = []
    inst.ui = type("U", (), {"log": staticmethod(lambda s: logs.append(str(s))),
                             "progress": staticmethod(lambda a, b: None),
                             "status": staticmethod(lambda s: print("      · " + s))})()

    print("== 2. 完整安装（ODE+汉化+宽屏+备份）==")
    t0 = time.time()
    ok, msg, bdir = inst.install(g, True, True, True, True, sb)
    dt = time.time() - t0
    ck(ok, "install 成功（%.1f s）：%s" % (dt, msg))
    if not ok:
        print("      日志:"); [print("       |", l) for l in logs[-25:]]
        return 1

    print("== 3. ODE 层断言（补丁目标 md5 记录）==")
    for name in ["datapack.hvp", "cachpack.hvp", "kinepack.hvp"]:
        got = md5(os.path.join(g, name))
        print("      %s = %s" % (name, got))
    ck(md5(os.path.join(g, "cachpack.hvp")) != CACH_MD5, "cachpack 已变（ODE 层生效）")
    ck(md5(os.path.join(g, "kinepack.hvp")) != md5(KINE_VAN), "kinepack 已变（ODE 层生效）")
    data_files = [a for a in mf["files"] if a.startswith("data/")]
    ck(all(os.path.isfile(os.path.join(g, a.replace("/", os.sep))) for a in data_files),
       "data/ ODE 文件 %d 个全部就位" % len(data_files))

    print("== 4. 汉化层断言 ==")
    for a in ["version.dll", "big_atlas_c.raw", "table_c.bin"]:
        ck(md5(os.path.join(g, a)) == mf["files"]["files/" + a]["md5"], "%s = 发布基线" % a)
    got_lng = lng_of(os.path.join(g, "datapack.hvp"), os.path.join(sb, "chkA"))
    ck(got_lng == ZH_MD5, "en.lng = 汉化译文 %s" % (got_lng or "None"))
    got_tex = tex_of(os.path.join(g, "datapack.hvp"), os.path.join(sb, "texA"))
    hit = [k for k in want_tex if got_tex and got_tex.get(k) == want_tex[k]]
    ck(len(hit) == 33, "33 件地图贴图全部命中中文基线（命中 %d）" % len(hit))
    j = "\n".join(logs)
    ck("替换中文译文" in j and "重建 datapack" in j, "日志含重建链路")
    ck("ODE 补丁链完成（3/3）" in j, "日志含 ODE 补丁链完成")

    print("== 5. 幂等：二次安装应跳过 ==")
    mt = os.path.getmtime(os.path.join(g, "datapack.hvp"))
    time.sleep(1.1)
    ok2, msg2, _ = inst.install(g, True, True, True, True, sb)
    ck(ok2, "二次安装成功：" + msg2)
    ck(any("跳过重建" in l for l in logs), "日志含「跳过重建」")
    ck(os.path.getmtime(os.path.join(g, "datapack.hvp")) == mt, "datapack 未重复重建")

    print("== 6. 卸载 → 回到 Steam 原版 ==")
    okU, msgU = inst.uninstall(g, logs.append)
    ck(okU, "uninstall 成功：" + msgU)
    ck(md5(os.path.join(g, "datapack.hvp")) == VAN_MD5, "datapack 回原版")
    ck(md5(os.path.join(g, "cachpack.hvp")) == CACH_MD5, "cachpack 回原版")
    ck(md5(os.path.join(g, "kinepack.hvp")) == md5(KINE_VAN), "kinepack 回原版")
    for a in ["version.dll", "big_atlas_c.raw", "table_c.bin",
              "dinput8.dll", "scripts/obscure.asi", "scripts/main.patch"]:
        ck(not os.path.isfile(os.path.join(g, a.replace("/", os.sep))), "%s 已移除" % a)
    left_data = [a for a in data_files if os.path.isfile(os.path.join(g, a.replace("/", os.sep)))]
    ck(not left_data, "data/ ODE 文件已删除（残留 %s）" % (left_data or "无"))

    print("== 7. 真实游戏目录零改动 ==")
    for f in real_fp:
        ck(md5(os.path.join(GAME, f)) == real_fp[f], "真实 %s 未变" % f)

    print()
    if FAILS:
        print("✗ %d 项失败:" % len(FAILS))
        for f in FAILS: print("   -", f)
        return 1
    print("★★ 加强版 v1.00 E2E 全部通过")
    return 0

import installer_full_v100 as I
if __name__ == "__main__":
    sys.exit(main())
